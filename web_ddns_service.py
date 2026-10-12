"""Independent DuckDNS updater. No Tk, web-listener, router or firewall changes."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import ipaddress
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
import uuid

from https_transport import create_verified_ssl_context
from web_secret_store import SecretStorageError, protect_secret, unprotect_secret
from web_service_common import close_logger, diagnostic, diagnostic_logger, read_json, timestamp, write_json

CHECK_INTERVAL = 300
KEEPALIVE_INTERVAL = 21600
MANUAL_INTERVAL = 30


def normalize_domain(value):
    value = value.strip().lower()
    if value.endswith(".duckdns.org"):
        value = value[:-12]
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValueError("DuckDNS에 등록한 주소 이름을 입력해주세요. 예: myboss 또는 myboss.duckdns.org")
    return value


def validate_token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
        raise ValueError("DuckDNS 홈페이지의 Token을 공백 없이 복사해주세요.")


class ProviderRejected(Exception):
    pass


class StaleOperation(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _https(url):
    # Contexts are local; credentials may not follow a redirect or proxy URL.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=create_verified_ssl_context()))
    request = urllib.request.Request(url, headers={"User-Agent": "BossTimer-Web-DDNS"})
    with opener.open(request, timeout=5) as response:
        body = response.read(1025)
        if len(body) > 1024:
            raise ValueError("unexpected DDNS response")
        return body.decode("ascii").strip()


def _public_ipv4():
    address = ipaddress.ip_address(_https("https://api.ipify.org"))
    if address.version != 4 or not address.is_global:
        raise ValueError("unexpected public IPv4")
    return str(address)


def _update_provider(domain, token, address):
    # No TXT/AAAA clearing: this step updates only the selected domain's A record.
    query = urlencode(dict(domains=domain, token=token, ip=address, verbose="true"))
    lines = _https("https://www.duckdns.org/update?" + query).splitlines()
    if not lines or lines[0].strip() != "OK":
        raise ProviderRejected()
    if len(lines) < 4 or lines[1].strip() != address or lines[3].strip() not in {"UPDATED", "NOCHANGE"}:
        raise ValueError("unexpected DDNS confirmation")
    return lines[3].strip()


def _dns(domain, address):
    try:
        rows = socket.getaddrinfo(domain + ".duckdns.org", 0, socket.AF_UNSPEC, socket.SOCK_STREAM)
        addresses = sorted({str(ipaddress.ip_address(row[4][0])) for row in rows})
    except (OSError, ValueError):
        return "pending", "DNS 조회 대기", []
    ipv6 = [value for value in addresses if ipaddress.ip_address(value).version == 6]
    if address in addresses:
        return "ready", "IPv4 DNS 일치" + (" · IPv6 레코드 있음" if ipv6 else ""), addresses
    return "pending", "DNS 반영 대기", addresses


class DDNSService:
    def __init__(self, root, *, on_saved=None):
        self.root, self.path = root, root / "ddns_settings.json"
        self.on_saved = on_saved
        self.lock = threading.RLock()
        self.wake, self.closed = threading.Event(), threading.Event()
        self.thread = None
        self.logger = None
        self.active = False
        self.generation = 0
        self.jobs = deque()
        self.results = {}
        self.next_check = 0.0
        self.last_manual = -MANUAL_INTERVAL
        self.last_update = 0.0
        self.current_address = ""
        self.settings = dict(schema=1, enabled=True, domain="", protected_token="")
        self.view = dict(enabled=True, domain="", has_token=False, state="unconfigured", busy=False,
                         message="무료 주소를 처음 한 번 등록한 뒤 주소 이름과 Token을 입력하세요.",
                         current_ip="", dns_state="waiting", dns_message="미확인", dns_addresses=[],
                         last_checked=None, last_updated=None, configured=False, settings_error=False)
        try:
            if self.path.stat().st_size > 32768:
                raise ValueError("oversized DDNS settings")
            saved = read_json(self.path)
            if (saved.get("schema") != 1 or type(saved.get("enabled")) is not bool
                    or not isinstance(saved.get("domain"), str) or not isinstance(saved.get("protected_token"), str)
                    or len(saved["protected_token"]) > 16384):
                raise ValueError("invalid DDNS settings")
            if saved["domain"]:
                saved["domain"] = normalize_domain(saved["domain"])
            if saved["enabled"] and not (saved["domain"] and saved["protected_token"]):
                raise ValueError("missing DDNS credentials")
            self.settings = {key: saved[key] for key in self.settings}
            self._settings_view()
            self.view.update(state="paused" if self.view["configured"] else "unconfigured",
                             message="웹서버 실행 후 자동 갱신합니다." if saved["enabled"] else "자동 갱신이 꺼져 있습니다.")
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            self.view.update(state="error", settings_error=True,
                             message="DDNS 설정을 읽지 못했습니다. 주소와 Token을 다시 저장해주세요.")
            self.load_error = type(exc).__name__

    def _settings_view(self):
        self.view.update(enabled=self.settings["enabled"], domain=self.settings["domain"] + ".duckdns.org" if self.settings["domain"] else "",
                         has_token=bool(self.settings["protected_token"]),
                         configured=bool(self.settings["domain"] and self.settings["protected_token"]))

    def status(self):
        with self.lock:
            return deepcopy(self.view)

    def set_active(self, active):
        with self.lock:
            if self.closed.is_set() or bool(active) == self.active:
                return
            self.active = bool(active)
            self.generation += 1
            self.next_check = 0
            if active:
                # A different PC may have owned this domain while we were on standby.
                self.current_address = ""
                self.last_update = 0
            if not active and self.view["configured"]:
                self.view.update(state="paused", message="스탠바이 · 송출권한과 외부 공개 준비 후 주소를 갱신합니다.")
            if active and self.settings["enabled"]:
                self._ensure_thread()
        self.wake.set()

    def _ensure_thread(self):
        with self.lock:
            if self.closed.is_set():
                return
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name="boss-web-ddns", daemon=True)
                self.thread.start()

    def configure(self, enabled, domain, token):
        # Validate text immediately; Windows crypto, persistence and network stay off Tk.
        domain = normalize_domain(domain) if domain.strip() else ""
        token = token.strip()
        if token:
            validate_token(token)
        with self.lock:
            if enabled and not domain:
                raise ValueError("먼저 DuckDNS에 등록한 주소 이름을 입력해주세요.")
            if enabled and not token and not self.settings["protected_token"]:
                raise ValueError("첫 설정에는 DuckDNS Token이 필요합니다.")
            return self._submit("configure", dict(enabled=bool(enabled), domain=domain, token=token))

    def update_now(self):
        with self.lock:
            if not self.view["configured"] or not self.settings["enabled"]:
                raise ValueError("주소와 Token을 저장하고 자동 갱신을 켜주세요.")
            if not self.active:
                raise ValueError("서비스 탭에서 웹서비스를 먼저 켜주세요.")
            if time.monotonic() - self.last_manual < MANUAL_INTERVAL:
                raise ValueError("지금 갱신은 30초 뒤 다시 사용할 수 있습니다.")
            self.last_manual = time.monotonic()
            return self._submit("update", {})

    def _submit(self, operation, values):
        if self.closed.is_set() or len(self.jobs) >= 8:
            raise ValueError("DDNS 작업을 잠시 후 다시 시도해주세요.")
        job = uuid.uuid4().hex
        self.jobs.append((job, operation, values))
        self._ensure_thread()
        self.wake.set()
        return job

    def result(self, job):
        with self.lock:
            return self.results.pop(job, None)

    def _current(self, generation):
        with self.lock:
            if self.closed.is_set() or generation != self.generation or not self.active or not self.settings["enabled"]:
                raise StaleOperation()

    def _save(self, values):
        with self.lock:
            saved = dict(self.settings)
        if values["token"]:
            saved["protected_token"] = protect_secret(values["token"])
        saved.update(enabled=values["enabled"], domain=values["domain"])
        if saved["enabled"]:
            validate_token(unprotect_secret(saved["protected_token"]))
        self.root.mkdir(parents=True, exist_ok=True)
        write_json(self.path, saved)
        with self.lock:
            self.settings = saved
            self.generation += 1
            self.next_check = 0
            self.current_address = ""
            self.last_update = 0
            self._settings_view()
            self.view.update(settings_error=False, current_ip="", dns_state="waiting", dns_message="미확인",
                             dns_addresses=[], last_checked=None, last_updated=None,
                             state="waiting" if saved["enabled"] and self.active else "paused",
                             message="설정을 저장했습니다. 웹서버 실행 중에는 자동 갱신합니다." if saved["enabled"] else "자동 갱신을 껐습니다.")
        diagnostic(self.logger, "ddns_settings_saved", enabled=saved["enabled"], domain=saved["domain"])
        if self.on_saved is not None:
            try:
                message = self.on_saved(saved)
                if message:
                    return "DDNS 설정을 저장했습니다. " + message
            except Exception as exc:
                diagnostic(self.logger, "ddns_saved_setup_failed", error_type=type(exc).__name__)
                return "DDNS 설정은 저장했습니다. 웹 준비를 완료하지 못해 웹 강제 승계/재시도가 필요합니다."
        return "DDNS 설정을 저장했습니다."

    def _refresh(self, *, force=False):
        with self.lock:
            generation, saved = self.generation, dict(self.settings)
            self.view.update(state="checking", message="공인 IP와 무료 주소를 확인합니다.")
        self._current(generation)
        token = unprotect_secret(saved["protected_token"])
        validate_token(token)
        address = _public_ipv4()
        self._current(generation)
        updated_at = None
        if force or address != self.current_address or time.monotonic() - self.last_update >= KEEPALIVE_INTERVAL:
            reply = _update_provider(saved["domain"], token, address)
            self._current(generation)
            self.last_update = time.monotonic()
            updated_at = timestamp()
            diagnostic(self.logger, "ddns_provider_updated", domain=saved["domain"], address=address, result=reply)
        dns_state, dns_message, dns_addresses = _dns(saved["domain"], address)
        self._current(generation)
        self.current_address = address
        with self.lock:
            self.view.update(state="ready" if dns_state == "ready" else "dns_pending", current_ip=address,
                             dns_state=dns_state, dns_message=dns_message, dns_addresses=dns_addresses,
                             last_checked=timestamp(), message="무료 주소의 IPv4 DNS가 일치합니다. 외부 웹 접속은 이후 단계입니다." if dns_state == "ready" else
                             "DDNS 갱신은 성공했습니다. DNS 반영을 기다리며 자동으로 다시 확인합니다.")
            if updated_at:
                self.view["last_updated"] = updated_at
        diagnostic(self.logger, "ddns_dns_checked", domain=saved["domain"], state=dns_state, ipv6_present=any(":" in value for value in dns_addresses))
        return "무료 주소 갱신 완료 · " + dns_message

    def _failure(self, exc, *, saving=False):
        reason = getattr(exc, "reason", exc)
        if isinstance(exc, ProviderRejected):
            message = "DuckDNS가 갱신을 거부했습니다. 등록한 주소 이름과 계정 Token을 확인해주세요."
        elif isinstance(exc, SecretStorageError):
            message = "저장된 Token을 읽지 못했습니다. 현재 PC의 Windows 계정에서 Token을 다시 입력해주세요."
        elif isinstance(reason, ssl.SSLCertVerificationError):
            message = "HTTPS 인증서를 검증하지 못했습니다. PC 시간·Windows 인증서 상태를 확인해주세요."
        elif saving:
            message = "DDNS 설정을 저장하지 못했습니다. 기존 설정을 유지합니다. 진단 로그를 확인해주세요."
        elif isinstance(exc, ValueError):
            message = "주소·Token 또는 서비스 응답을 확인하지 못했습니다. 입력값과 진단 로그를 확인해주세요."
        else:
            message = "DDNS 통신을 확인하지 못했습니다. 기존 주소를 유지하며 잠시 후 다시 시도합니다."
        with self.lock:
            self.view.update(state="error", message=message)
        # Never log str(exc), HTTP errors, response bodies or URLs: token is in the query.
        diagnostic(self.logger, "ddns_failed", error_type=type(exc).__name__)
        return message

    def _run(self):
        self.logger = diagnostic_logger(self.root, "ddns")
        if hasattr(self, "load_error"):
            diagnostic(self.logger, "ddns_settings_read_failed", error_type=self.load_error)
        try:
            while not self.closed.is_set():
                with self.lock:
                    task = self.jobs.popleft() if self.jobs else None
                    due = self.active and self.settings["enabled"] and time.monotonic() >= self.next_check
                    work_generation = self.generation
                    self.view["busy"] = bool(task or due)
                if task or due:
                    try:
                        if task:
                            job, operation, values = task
                            message = self._save(values) if operation == "configure" else self._refresh(force=True)
                        else:
                            message = self._refresh()
                        result = dict(ok=True, message=message)
                    except StaleOperation:
                        result = dict(ok=False, message="웹서버 또는 DDNS 설정이 바뀌어 이전 작업을 중단했습니다.")
                    except Exception as exc:
                        with self.lock:
                            stale = self.closed.is_set() or (not task or task[1] == "update") and work_generation != self.generation
                        result = dict(ok=False, message="웹서버 또는 DDNS 설정이 바뀌어 이전 작업을 중단했습니다." if stale else
                                      self._failure(exc, saving=bool(task and task[1] == "configure")))
                    with self.lock:
                        if (not task or task[1] == "update") and work_generation == self.generation:
                            self.next_check = time.monotonic() + CHECK_INTERVAL
                        if task:
                            if len(self.results) >= 16:
                                self.results.pop(next(iter(self.results)))
                            self.results[task[0]] = result
                        self.view["busy"] = False
                    continue
                with self.lock:
                    delay = min(60, max(0.1, self.next_check - time.monotonic())) if self.active and self.settings["enabled"] else 60
                self.wake.wait(delay)
                self.wake.clear()
        finally:
            close_logger(self.logger)

    def close(self):
        with self.lock:
            self.closed.set()
            self.jobs.clear()
            self.generation += 1
        self.wake.set()

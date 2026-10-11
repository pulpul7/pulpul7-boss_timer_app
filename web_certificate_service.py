"""Desktop-only certificate jobs/renewal. Never blocks Tk or the web coordinator."""
from __future__ import annotations

from datetime import datetime, timezone
import http.client
import json
import socket
import threading
import time
import uuid

from https_transport import create_verified_ssl_context
from web_certificate_store import DEFAULT_HTTPS_PORT, hostname, load_certificate, public_certificate, publish_certificate
from web_service_common import close_logger, diagnostic, diagnostic_logger, read_json, timestamp, write_json


class CertificateService:
    def __init__(self, root):
        self.root, self.path = root, root / "https_settings.json"
        self.lock = threading.RLock()
        self.wake, self.closed = threading.Event(), threading.Event()
        self.thread = self.logger = None
        self.active = False
        self.generation = 0
        self.job = None
        self.results = {}
        self.next_check = 0
        self.settings = dict(schema=1, enabled=False, domain="", port=DEFAULT_HTTPS_PORT,
                             terms_agreed_at="", last_attempt_at="")
        self.view = dict(busy=False, message="무료 주소를 확인한 뒤 인증서를 발급해주세요.",
                         probe_message="로컬 HTTPS 확인 전", checked_at="")
        try:
            saved = read_json(self.path)
            if (saved.get("schema") != 1 or type(saved.get("enabled")) is not bool
                    or type(saved.get("port")) is not int or not 1024 <= saved["port"] <= 65535):
                raise ValueError("invalid HTTPS settings")
            if saved.get("domain"):
                hostname(saved["domain"])
            self.settings.update({key: saved[key] for key in self.settings if key in saved})
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.view["message"] = "HTTPS 설정을 읽지 못했습니다. 진단 로그를 확인해주세요."
        self.view["certificate"] = public_certificate(root)

    def status(self):
        with self.lock:
            return dict(self.view, enabled=self.settings["enabled"], port=self.settings["port"],
                        domain=self.settings["domain"], terms_agreed=bool(self.settings["terms_agreed_at"]),
                        certificate=dict(self.view["certificate"]))

    def _start_thread(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, name="web-certificates", daemon=True)
            self.thread.start()

    def set_active(self, active):
        with self.lock:
            if self.closed.is_set() or self.active == bool(active):
                return
            self.active = bool(active)
            self.generation += 1
            if active:
                self.next_check = 0
                self._start_thread()
        self.wake.set()

    def request(self, operation, *, agreed=False, port=DEFAULT_HTTPS_PORT):
        with self.lock:
            if self.closed.is_set() or self.job or self.view["busy"]:
                raise ValueError("HTTPS 작업이 진행 중입니다. 완료 후 다시 시도해주세요.")
            if not self.active and operation == "issue":
                raise ValueError("웹서비스를 켜고 Discord 송출권한을 먼저 얻어주세요.")
            if operation not in {"issue", "probe"}:
                raise ValueError("잘못된 HTTPS 작업입니다.")
            if operation == "issue":
                if not agreed and not self.settings["terms_agreed_at"]:
                    raise ValueError("인증기관 약관에 동의한 뒤 발급해주세요.")
                if type(port) is not int or not 1024 <= port <= 65535:
                    raise ValueError("HTTPS 포트는 1024~65535 사이로 입력해주세요.")
            job = uuid.uuid4().hex
            self.job = (job, operation, bool(agreed), port)
            self.view.update(busy=True, message="HTTPS 작업을 준비합니다…")
            self._start_thread()
        self.wake.set()
        return job

    def result(self, job):
        with self.lock:
            return self.results.pop(job, None)

    def _progress(self, message, stage):
        with self.lock:
            if self.closed.is_set():
                return
            self.view["message"] = message
        diagnostic(self.logger, stage)

    def _cancelled(self, generation=None):
        return self.closed.is_set() or not self.active or (generation is not None and generation != self.generation)

    def _issue(self, agreed, port, automatic=False, generation=None):
        from web_acme_client import CertificateCancelled, CertificateIssueError, issue_certificate
        cancelled = lambda: self._cancelled(generation)
        if cancelled():
            raise CertificateCancelled()
        saved_ddns = read_json(self.root / "ddns_settings.json")
        domain = hostname(saved_ddns["domain"])
        if not saved_ddns.get("protected_token"):
            raise CertificateIssueError("무료 주소의 Token을 먼저 저장해주세요.")
        now = datetime.now(timezone.utc)
        try:
            existing, _, _, _ = load_certificate(self.root)
        except (OSError, ValueError, KeyError):
            existing = {}
        due = not existing or existing.get("domain") != domain or datetime.fromisoformat(existing["renew_at"]) <= now
        with self.lock:
            settings = dict(self.settings)
        last = settings.get("last_attempt_at")
        if due and last:
            try:
                wait = (now - datetime.fromisoformat(last)).total_seconds()
            except (TypeError, ValueError):
                wait = 0
            if wait < (21600 if automatic else 600):
                raise CertificateIssueError("최근 발급을 시도했습니다. 인증기관 제한을 피하도록 잠시 뒤 다시 시도해주세요. (수동 재시도: 10분)")
        settings.update(enabled=True, domain=domain, port=port)
        if agreed and not settings["terms_agreed_at"]:
            settings["terms_agreed_at"] = timestamp()
        if not settings["terms_agreed_at"]:
            raise CertificateIssueError("인증기관 약관 동의가 필요합니다.")
        if due:
            settings["last_attempt_at"] = timestamp()
        write_json(self.path, settings)
        with self.lock:
            self.settings = settings
        if due:
            chain, key, issued_domain = issue_certificate(self.root, cancelled, self._progress)
            if cancelled():
                raise CertificateCancelled()
            current_ddns = read_json(self.root / "ddns_settings.json")
            if (current_ddns.get("domain"), current_ddns.get("protected_token")) != (saved_ddns.get("domain"), saved_ddns.get("protected_token")):
                raise CertificateIssueError("무료 주소 설정이 바뀌었습니다. 새 주소로 다시 발급해주세요.")
            def commit(path, info):
                with self.lock:
                    if cancelled():
                        raise CertificateCancelled()
                    write_json(path, info)
            info = publish_certificate(self.root, chain, key, issued_domain, commit=commit)
            diagnostic(self.logger, "certificate_published", domain=issued_domain, expires_at=info["expires_at"])
        else:
            diagnostic(self.logger, "certificate_reused", domain=domain, expires_at=existing["expires_at"])
        return "인증서가 준비됐습니다. HTTPS가 시작되면 '로컬 HTTPS 확인'을 눌러주세요."

    def _probe(self):
        with self.lock:
            domain, port = self.settings["domain"], self.settings["port"]
        if not domain:
            raise ValueError("no certificate domain")
        # Connect to loopback, but validate the real certificate hostname/SNI.
        context = create_verified_ssl_context()
        class LoopbackHTTPS(http.client.HTTPSConnection):
            def connect(self):
                plain = socket.create_connection(("127.0.0.1", port), timeout=5)
                try:
                    self.sock = context.wrap_socket(plain, server_hostname=domain)
                except Exception:
                    plain.close()
                    raise
        connection = LoopbackHTTPS(domain, port, timeout=5)
        try:
            connection.request("GET", "/api/session", headers={"Host": f"{domain}:{port}", "Connection": "close"})
            response = connection.getresponse()
            body = response.read(2049)
            if response.status != 200 or len(body) > 2048 or not isinstance(json.loads(body).get("available"), bool):
                raise ValueError("unexpected HTTPS response")
        finally:
            connection.close()
        with self.lock:
            self.view.update(probe_message="로컬 TLS·주소·인증서 검증 성공 · 외부 접속 미검증", checked_at=timestamp())
        diagnostic(self.logger, "local_https_verified", domain=domain, port=port)
        return "이 PC의 HTTPS 응답과 인증서가 정상입니다. 외부 공개 탭에서 외부 포트를 연결할 수 있습니다."

    def _loop(self):
        self.logger = diagnostic_logger(self.root, "https")
        try:
            while not self.closed.is_set():
                self.wake.clear()
                with self.lock:
                    task, self.job = self.job, None
                    automatic = self.active and self.settings["enabled"] and time.monotonic() >= self.next_check
                    if not task and automatic:
                        task = (None, "issue", False, self.settings["port"])
                    if task:
                        generation = self.generation
                        self.view["busy"] = True
                        self.next_check = time.monotonic() + 43200
                if task:
                    job, operation, agreed, port = task
                    try:
                        message = self._issue(agreed, port, automatic=job is None, generation=generation) if operation == "issue" else self._probe()
                        result = dict(ok=True, message=message)
                    except Exception as exc:
                        from web_acme_client import CertificateCancelled, CertificateIssueError
                        error_code = getattr(exc, "code", None)
                        if error_code not in {"rateLimited", "unauthorized", "dns", "connection", "badNonce",
                                              "rejectedIdentifier", "malformed", "serverInternal", "orderNotReady"}:
                            error_code = None
                        diagnostic(self.logger, "https_job_failed", operation=operation,
                                   error_type=type(exc).__name__, acme_code=error_code)
                        if isinstance(exc, CertificateIssueError):
                            message = str(exc)
                        elif error_code == "rateLimited":
                            message = "인증기관에서 발급을 일시 제한했습니다. 반복 발급을 멈추고 자동 재시도를 기다려주세요."
                        elif isinstance(exc, CertificateCancelled):
                            message = "웹서비스가 종료되어 HTTPS 작업을 중단했습니다."
                        elif isinstance(exc, ImportError):
                            message = "HTTPS 모듈이 없습니다. requirements-gui.txt 설치 후 다시 실행해주세요."
                        elif operation == "probe":
                            message = "로컬 HTTPS 확인 실패. 인증서·포트 상태와 진단 로그를 확인해주세요."
                            with self.lock:
                                self.view["probe_message"] = message
                        else:
                            message = "인증서 준비에 실패했습니다. 무료 주소 설정·인터넷 연결과 진단 로그를 확인해주세요."
                        result = dict(ok=False, message=message)
                    with self.lock:
                        if not self.closed.is_set():
                            self.view.update(busy=False, message=message, certificate=public_certificate(self.root))
                            if job:
                                if len(self.results) >= 32:
                                    self.results.pop(next(iter(self.results)))
                                self.results[job] = result
                self.wake.wait(60)
        finally:
            close_logger(self.logger)

    def close(self):
        with self.lock:
            self.closed.set()
        self.wake.set()

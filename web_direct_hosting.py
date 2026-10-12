"""Desktop-only IPv4 direct publication and cleanup, on an isolated thread."""
from __future__ import annotations

from copy import deepcopy
import http.client
import ipaddress
import json
from pathlib import Path
import socket
import sys
import threading
import time
import uuid

from https_transport import create_verified_ssl_context
from web_access_settings import validate_settings, valid_id
from web_certificate_store import load_certificate, hostname
from web_ddns_service import _public_ipv4
from web_firewall import FirewallError, apply as firewall_apply, rule_data
from web_network_diagnostics import _route, _text, _upnp, _windows_network
from web_port_mapping import MappingError, add, owned, remove, soap, specific, validate_mapping
from web_service_common import close_logger, diagnostic, diagnostic_logger, read_json, timestamp, write_json


class PublicError(Exception):
    pass


class PublicCancelled(Exception):
    pass


class HTTPSWaiting(Exception):
    pass


def check_local_https(domain, port):
    context = create_verified_ssl_context()
    class LocalConnection(http.client.HTTPSConnection):
        def connect(self):
            plain = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                self.sock = context.wrap_socket(plain, server_hostname=domain)
            except Exception:
                plain.close()
                raise
    connection = LocalConnection(domain, port, timeout=5)
    try:
        connection.request("GET", "/api/session", headers={"Host": f"{domain}:{port}", "Connection": "close"})
        response = connection.getresponse()
        body = response.read(2049)
        if response.status != 200 or len(body) > 2048:
            raise PublicError("로컬 HTTPS 응답을 확인하지 못했습니다.")
        result = json.loads(body)
        if result.get("available") is not True or result.get("configured") is not True:
            raise PublicError("비밀번호 탭에서 웹 비밀번호를 먼저 설정해주세요.")
    finally:
        connection.close()


class DirectHostingService:
    def __init__(self, root, context):
        self.root, self.context = root, context
        self.path, self.resources_path = root / "public_settings.json", root / "public_resources.json"
        self.connection_cache_path = root / "public_connection_cache.json"
        self.lock = threading.RLock()
        self.wake, self.closed = threading.Event(), threading.Event()
        self.thread = self.logger = None
        self.active = False
        self.generation = 0
        self.pending_save = False
        self.settings = dict(schema=1, enabled=True, owner=uuid.uuid4().hex)
        self.view = dict(enabled=True, busy=False, state="off", message="송출권한과 HTTPS 준비 후 외부 공개합니다.",
                         external_port=0,
                         address="", firewall="설정 전", mapping="설정 전", https="로컬 전용", external="외부 접속 미검증")
        self.resources = None
        self.blocked_generation = -1
        self.activation_generation = -1
        self.next_renewal = 0
        try:
            saved = read_json(self.path)
            if saved.get("schema") != 1 or type(saved.get("enabled")) is not bool or not valid_id(saved.get("owner")):
                raise ValueError("invalid public settings")
            self.settings = {key: saved[key] for key in self.settings}
            self.view["enabled"] = saved["enabled"]
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError):
            self.blocked_generation = self.generation
            self.view.update(state="error", message="외부 공개 설정을 읽지 못했습니다. 진단 로그를 확인해주세요.")

    def status(self):
        with self.lock:
            return deepcopy(self.view)

    def _ensure_thread(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, name="web-direct-hosting", daemon=True)
            self.thread.start()

    def set_active(self, active):
        with self.lock:
            if self.closed.is_set():
                return
            if bool(active) == self.active and self.thread and self.thread.is_alive():
                return
            if bool(active) != self.active:
                self.active = bool(active)
                self.generation += 1
            # Reconcile old owned resources even if the webservice is off.
            try:
                has_resources = self.resources_path.exists()
            except OSError:
                has_resources = True  # Report access failures on the isolated worker.
            if self.settings["enabled"] or has_resources:
                self._ensure_thread()
        self.wake.set()

    def configure(self, enabled):
        with self.lock:
            if self.closed.is_set():
                raise ValueError("웹서비스가 종료 중입니다.")
            self.settings["enabled"] = bool(enabled)
            self.generation += 1
            self.pending_save = True
            self.view.update(enabled=bool(enabled), busy=True, state="starting" if enabled else "stopping",
                             message="외부 공개를 준비합니다…" if enabled else "공개 규칙을 정리합니다…", address="")
            self._ensure_thread()
        self.wake.set()

    def _check(self, generation):
        with self.lock:
            if self.closed.is_set() or not self.active or not self.settings["enabled"] or generation != self.generation:
                raise PublicCancelled()

    def _progress(self, message, event, **values):
        with self.lock:
            self.view.update(message=message, **values)
        diagnostic(self.logger, event)

    def _load_resources(self):
        try:
            data = read_json(self.resources_path)
        except FileNotFoundError:
            return None
        if data.get("schema") != 1 or data.get("owner") != self.settings["owner"]:
            raise ValueError("public resource ownership mismatch")
        if data.get("mapping"):
            validate_mapping(data["mapping"])
            if data["mapping"]["description"] != "BossTimerWeb-" + self.settings["owner"]:
                raise ValueError("mapping owner mismatch")
        rule = data.get("firewall")
        if rule and rule != rule_data(self.settings["owner"], rule["program"], rule["address"], rule["port"]):
            raise ValueError("firewall owner mismatch")
        return data

    def _policy(self, enabled, resource=None):
        resource = resource or self.resources or {}
        write_json(self.root / "public_listener.json", dict(schema=1, enabled=bool(enabled),
                   run_id=resource.get("run_id", ""), generation=resource.get("activation", ""),
                   domain=resource.get("domain", ""), port=resource.get("port", 0),
                   external_port=(resource.get("mapping") or {}).get("external_port", 0),
                   valid_until=time.time() + 45 if enabled else 0))

    def _cleanup(self):
        self._policy(False)
        resource = self.resources or self._load_resources()
        if not resource:
            return
        failed = False
        for key, operation in (("mapping", remove), ("firewall", lambda value: firewall_apply(value, "remove"))):
            if resource.get(key):
                try:
                    operation(resource[key])
                    resource[key] = None
                except Exception as exc:
                    failed = True
                    diagnostic(self.logger, "public_cleanup_failed", resource=key, error_type=type(exc).__name__)
                write_json(self.resources_path, resource)
        if failed:
            self.resources = resource
            raise PublicError("공개는 중지했지만 일부 규칙을 정리하지 못했습니다. 관리자 권한·공유기 연결을 확인하고 다시 시도해주세요.")
        self.resources_path.unlink(missing_ok=True)
        self.resources = None
        diagnostic(self.logger, "public_resources_removed")

    def _start(self, generation):
        self._check(generation)
        context = self.context()
        if context.get("https_state") != "running":
            raise HTTPSWaiting()
        if not validate_settings(read_json(self.root / "access_settings.json"))["verifier"]:
            raise PublicError("비밀번호 탭에서 웹 비밀번호를 먼저 설정해주세요.")
        info, _, _, _ = load_certificate(self.root)
        settings = read_json(self.root / "https_settings.json")
        ddns = read_json(self.root / "ddns_settings.json")
        domain, port = hostname(settings["domain"]), settings["port"]
        if info["domain"] != domain or hostname(ddns["domain"]) != domain:
            raise PublicError("무료 주소와 HTTPS 인증서 주소가 다릅니다. HTTPS 탭에서 확인해주세요.")
        if not ddns.get("enabled"):
            raise PublicError("무료 주소 탭에서 자동 갱신을 켜고 저장해주세요.")
        check_local_https(domain, port)
        self._check(generation)
        cached = self._cached_network(generation, domain, port, context.get("preferred_external_port"))
        if cached:
            self._progress("이전에 성공한 연결 정보로 외부 공개를 시작합니다…", "public_fast_connect_begin",
                           state="starting", busy=True, address="")
            try:
                self._publish(generation, context, domain, port, *cached)
                return
            except (PublicCancelled, HTTPSWaiting, FirewallError):
                raise
            except Exception as exc:
                self._check(generation)
                diagnostic(self.logger, "public_fast_connect_failed", error_type=type(exc).__name__)
                self._cleanup()  # Failed cleanup must block a second attempt.
                self.connection_cache_path.unlink(missing_ok=True)
        self._check(generation)
        self._progress("PC·공유기와 HTTPS 상태를 확인합니다…", "public_preflight", state="starting", busy=True, address="")
        route = _route(_windows_network(), 4)
        if not route:
            raise PublicError("공유기로 연결되는 IPv4 경로를 찾지 못했습니다. 네트워크 진단을 확인해주세요.")
        router = _upnp(route, lambda: self._check(generation))
        if not router:
            raise PublicError("공유기의 UPnP 자동 개방 응답이 없습니다. 공유기 UPnP 설정과 네트워크 진단을 확인해주세요.")
        public_ip = _public_ipv4()
        if not router["wan"] or not ipaddress.ip_address(router["wan"]).is_global or router["wan"] != public_ip:
            raise PublicError("공유기 외부 IP와 인터넷 공인 IP가 일치하지 않습니다. 상위 공유기·통신사 NAT·VPN을 확인해주세요.")
        self._publish(generation, context, domain, port, route, router)

    def _cached_network(self, generation, domain, port, preferred):
        """Reuse this PC's successful IGD endpoint; no discovery or Windows inventory."""
        try:
            cached = read_json(self.connection_cache_path)
            if (cached.get("schema") != 1 or cached.get("owner") != self.settings["owner"]
                    or cached.get("domain") != domain or cached.get("port") != port):
                return None
            route, router, mapping = cached["route"], cached["router"], cached["mapping"]
            validate_mapping(mapping)
            if (mapping["description"] != "BossTimerWeb-" + self.settings["owner"]
                    or mapping["gateway"] != route["gateway"] or mapping["client"] != route["local"]
                    or mapping["service"] != router["service"] or mapping["control"] != router["control"]
                    or mapping["internal_port"] != port or (preferred and mapping["external_port"] != preferred)):
                return None
            # UDP connect chooses a route without sending packets or resolving DNS.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("1.1.1.1", 443))
                if probe.getsockname()[0] != route["local"]:
                    diagnostic(self.logger, "public_connection_cache_invalid", reason="local_route_changed")
                    return None
            self._check(generation)
            reply = soap(mapping, "GetExternalIPAddress", {})
            wan = ipaddress.ip_address(_text(reply, "NewExternalIPAddress"))
            if wan.version != 4 or not wan.is_global or str(wan) != router["wan"]:
                diagnostic(self.logger, "public_connection_cache_invalid", reason="router_wan_changed")
                return None
            self._check(generation)
            return route, router
        except PublicCancelled:
            raise
        except FileNotFoundError:
            return None
        except Exception as exc:
            self._check(generation)
            diagnostic(self.logger, "public_connection_cache_invalid", error_type=type(exc).__name__)
            return None

    def _publish(self, generation, context, domain, port, route, router):
        self._check(generation)
        rule = rule_data(self.settings["owner"], str(Path(sys.executable).resolve()), route["local"], port)
        resource = dict(schema=1, owner=self.settings["owner"], run_id=context["run_id"], activation=uuid.uuid4().hex,
                        domain=domain, port=port, firewall=rule, mapping=None, created_at=timestamp())
        self.resources = resource
        write_json(self.resources_path, resource)  # Journal before modifying Windows/router state.
        self._progress("Windows 방화벽에 HTTPS 허용 규칙을 등록합니다…", "public_firewall_begin")
        firewall_apply(rule, "allow")
        self._check(generation)
        self._progress("공유기 외부 포트를 자동으로 연결합니다…", "public_mapping_begin", firewall="HTTPS 허용 규칙 등록됨")
        permanent = False
        preferred = context.get("preferred_external_port")
        candidates = (preferred,) if preferred else (443, 8443, port)
        for candidate in dict.fromkeys(candidates):
            mapping = dict(service=router["service"], control=router["control"], gateway=route["gateway"],
                           client=route["local"], internal_port=port, external_port=candidate,
                           description="BossTimerWeb-" + self.settings["owner"])
            self._check(generation)
            existing = specific(mapping)
            if existing and not owned(mapping, existing):
                previous_owner = context.get("previous_mapping_owner")
                if valid_id(previous_owner) and existing["description"] == "BossTimerWeb-" + previous_owner:
                    # Only reclaim this guild's token-authenticated former web mapping.
                    prior = dict(mapping, client=existing["client"], internal_port=existing["port"],
                                 description=existing["description"])
                    self._check(generation)
                    remove(prior)  # Rechecks exact ownership before deletion.
                    self._check(generation)
                    diagnostic(self.logger, "public_previous_mapping_reclaimed", port=candidate)
                else:
                    diagnostic(self.logger, "public_port_busy", port=candidate)
                    continue
            resource["mapping"] = mapping
            write_json(self.resources_path, resource)
            try:
                permanent = add(mapping)
                break
            except MappingError as exc:
                if exc.code not in {718, 724}:
                    raise
                diagnostic(self.logger, "public_port_retry", port=candidate, router_code=exc.code)
        else:
            raise PublicError("사용 가능한 외부 포트를 확보하지 못했습니다. 기존 공유기 규칙은 유지했습니다.")
        self._check(generation)
        self._policy(True, resource)
        self._progress("HTTPS 외부 연결을 준비합니다…", "public_listener_begin", mapping=f"TCP {resource['mapping']['external_port']} → {port}")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            self._check(generation)
            current = self.context()
            if current.get("run_id") != resource["run_id"]:
                raise PublicCancelled()
            if current.get("https_public") is True and current.get("https_activation") == resource["activation"]:
                break
            if self.closed.wait(0.25):
                raise PublicCancelled()
        else:
            raise PublicError("HTTPS 공개 전환을 확인하지 못했습니다. 로컬 웹서비스는 계속 사용할 수 있습니다.")
        check_local_https(domain, port)
        self._check(generation)
        external_port = resource["mapping"]["external_port"]
        address = f"https://{domain}" + (f":{external_port}" if external_port != 443 else "")
        with self.lock:
            self._check(generation)
            self.view.update(state="prepared", busy=False, address=address, https="HTTPS 공개 연결 준비됨",
                             external_port=external_port,
                             message="공개 준비 완료 · 실제 외부 접속은 아직 확인하지 않았습니다.",
                             mapping=f"TCP {external_port} → {port}" + (" · 영구 규칙 (종료 시 정리)" if permanent else " · 자동 갱신"))
        self.activation_generation = generation
        self.next_renewal = time.monotonic() + 1200
        diagnostic(self.logger, "public_prepared", domain=domain, external_port=external_port, permanent=permanent)
        try:
            write_json(self.connection_cache_path, dict(schema=1, owner=self.settings["owner"], domain=domain,
                port=port, route=route, router=router, mapping=resource["mapping"], saved_at=timestamp()))
        except OSError as exc:
            diagnostic(self.logger, "public_connection_cache_save_failed", error_type=type(exc).__name__)

    def _maintain(self, generation):
        self._check(generation)
        current = self.context()
        if current.get("run_id") != self.resources["run_id"]:
            raise PublicCancelled()
        if current.get("https_state") in {"error", "waiting", "stopped"}:
            raise PublicError("HTTPS 연결이 중단되어 외부 공개를 멈췄습니다. HTTPS 상태를 확인하고 다시 시작해주세요.")
        https = read_json(self.root / "https_settings.json")
        ddns = read_json(self.root / "ddns_settings.json")
        if (not https.get("enabled") or not ddns.get("enabled") or hostname(ddns["domain"]) != self.resources["domain"]
                or hostname(https["domain"]) != self.resources["domain"] or https["port"] != self.resources["port"]):
            raise PublicError("웹 주소·HTTPS 설정이 바뀌어 공개를 중지했습니다. 외부 공개를 다시 시작해주세요.")
        self._policy(True)
        if time.monotonic() >= self.next_renewal:
            route = _route(_windows_network(), 4)
            mapping = self.resources["mapping"]
            if not route or route["local"] != mapping["client"] or route["gateway"] != mapping["gateway"]:
                raise PublicError("PC·공유기 주소가 바뀌어 공개를 중지했습니다. 외부 공개를 다시 시작해주세요.")
            self._check(generation)
            add(mapping)
            self.next_renewal = time.monotonic() + 1200
            diagnostic(self.logger, "public_mapping_renewed")

    def _loop(self):
        self.logger = diagnostic_logger(self.root, "public")
        try:
            while not self.closed.is_set():
                retry_wait = 10
                self.wake.clear()
                with self.lock:
                    generation, active, enabled = self.generation, self.active, self.settings["enabled"]
                    save, self.pending_save = self.pending_save, False
                    settings = dict(self.settings)
                try:
                    if save:
                        self.root.mkdir(parents=True, exist_ok=True)
                        write_json(self.path, settings)
                    if not active or not enabled:
                        if self.resources or self.resources_path.exists():
                            self._cleanup()
                        with self.lock:
                            if generation == self.generation:
                                self.view.update(busy=False, state="off", address="", firewall="공개 규칙 해제", mapping="공개 규칙 해제",
                                                 https="로컬 전용", message="웹서비스를 켜면 외부 공개를 준비합니다." if enabled else "외부 공개가 꺼져 있습니다.")
                    elif self.blocked_generation != generation:
                        if self.activation_generation != generation or not self.resources:
                            self._cleanup()
                            self._start(generation)
                        else:
                            self._maintain(generation)
                except HTTPSWaiting:
                    retry_wait = 0.5  # Local worker state only; no repeated network diagnostics.
                    with self.lock:
                        self.view.update(state="waiting", busy=False, message="HTTPS 실행을 기다립니다. HTTPS 탭에서 인증서와 로컬 확인을 완료해주세요.")
                except PublicCancelled:
                    try:
                        self._cleanup()
                    except Exception as exc:
                        diagnostic(self.logger, "public_cancel_cleanup_failed", error_type=type(exc).__name__)
                except Exception as exc:
                    code = getattr(exc, "code", None)
                    diagnostic(self.logger, "public_failed", error_type=type(exc).__name__, code=code if isinstance(code, (int, str)) else None)
                    message = str(exc) if isinstance(exc, PublicError) else (
                        "방화벽 설정에 관리자 권한이 필요합니다. 프로그램을 관리자 권한으로 실행한 뒤 다시 시도해주세요."
                        if isinstance(exc, FirewallError) and code == "administrator_required" else
                        "외부 공개 준비에 실패했습니다. 네트워크 상태와 진단 로그를 확인해주세요. 로컬 웹서비스는 유지합니다.")
                    try:
                        self._cleanup()
                    except Exception as cleanup_error:
                        diagnostic(self.logger, "public_rollback_failed", error_type=type(cleanup_error).__name__)
                        message += " 일부 공개 규칙 정리는 완료되지 않았습니다."
                    with self.lock:
                        if generation == self.generation:
                            self.blocked_generation = generation
                            self.view.update(state="error", busy=False, message=message, address="", https="로컬 전용")
                self.wake.wait(retry_wait)
        finally:
            try:
                self._cleanup()
            except Exception as exc:
                diagnostic(self.logger, "public_exit_cleanup_failed", error_type=type(exc).__name__)
            close_logger(self.logger)

    def close(self, *, wait=False):
        self.closed.set()
        self.wake.set()
        if wait and self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=20)

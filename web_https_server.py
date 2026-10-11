"""Independent, bounded production HTTPS listener inside the web child process."""
from __future__ import annotations

import os
import socket
import ssl
import math
import threading
import time

from web_certificate_store import hostname, load_certificate
from web_service_common import diagnostic, read_json
from web_authority_gate import external_allowed
from web_asset_store import load_assets


class HTTPSListener:
    def __init__(self, root, schedule, access, logger, http_port, run_id=""):
        self.root, self.schedule, self.access, self.logger, self.http_port = root, schedule, access, logger, http_port
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.thread = None
        self.server = self.app = None
        self.signature = None
        self.next_read = 0
        self.view = dict(https_state="stopped", https_port=0, https_domain="", https_message="HTTPS 설정 전")
        self.run_id = run_id
        self.assets = load_assets(root, logger)

    def _public_policy(self):
        if not external_allowed(self.root, self.run_id):
            return {}
        try:
            data = read_json(self.root / "public_listener.json")
            expires = data.get("valid_until")
            now = time.time()
            if (data.get("schema") != 1 or data.get("enabled") is not True or data.get("run_id") != self.run_id
                    or type(expires) not in {int, float} or not math.isfinite(expires) or not now < expires <= now + 90
                    or type(data.get("external_port")) is not int or not 1 <= data["external_port"] <= 65535
                    or not isinstance(data.get("generation"), str) or len(data["generation"]) != 32
                    or any(c not in "0123456789abcdef" for c in data["generation"])):
                return {}
            return data
        except (OSError, ValueError):
            return {}

    def status(self):
        with self.lock:
            state = dict(self.view)
            app = self.app
        if app:
            with app.lock:
                state["https_request_count"] = app.request_count
        return state

    def refresh(self):
        if self.closed.is_set() or time.monotonic() < self.next_read:
            return
        self.next_read = time.monotonic() + 1
        paths = [self.root / "https_settings.json", self.root / "https" / "current.json", self.root / "ddns_settings.json"]
        try:
            signature = tuple((path.stat().st_mtime_ns, path.stat().st_size) for path in paths)
        except OSError:
            signature = None
        # Expiration must also be checked even if files are unchanged.
        tick = int(time.time() // 3600)
        policy = self._public_policy()
        signature = (signature, tick, policy.get("generation"), policy.get("external_port"), policy.get("domain"), policy.get("port"))
        if signature == self.signature or self.thread and self.thread.is_alive():
            return
        self.signature = signature
        self.thread = threading.Thread(target=self._reload, name="web-https-listener", daemon=True)
        self.thread.start()

    def _stop(self):
        with self.lock:
            server, self.server = self.server, None
        if server:
            try:
                server.stop()
            except Exception as exc:
                diagnostic(self.logger, "https_stop_failed", error_type=type(exc).__name__)

    def _reload(self):
        server = None
        try:
            try:
                settings = read_json(self.root / "https_settings.json")
            except FileNotFoundError:
                self._stop()
                with self.lock:
                    self.view.update(https_state="stopped", https_message="HTTPS 설정 전")
                return
            if not settings.get("enabled"):
                self._stop()
                with self.lock:
                    self.view.update(https_state="stopped", https_message="HTTPS 꺼짐")
                return
            if not (self.root / "https" / "current.json").exists():
                self._stop()
                with self.lock:
                    self.view.update(https_state="waiting", https_message="인증서 발급 대기 중")
                return
            info, certificate, private_key, password = load_certificate(self.root)
            domain, port = hostname(settings["domain"]), settings["port"]
            ddns = read_json(self.root / "ddns_settings.json")
            if (domain != info["domain"] or domain != hostname(ddns["domain"])
                    or type(port) is not int or not 1024 <= port <= 65535 or port == self.http_port):
                raise ValueError("HTTPS settings/certificate mismatch")
            policy = self._public_policy()
            public = bool(policy and policy.get("domain") == domain and policy.get("port") == port)
            external_port = policy["external_port"] if public else None
            activation = policy["generation"] if public else ""
            bind_host = "0.0.0.0" if public else "127.0.0.1"
            with self.lock:
                if (self.server and self.view.get("https_revision") == info["revision"] and self.view["https_port"] == port
                        and self.view.get("https_public", False) == public and self.view.get("https_external_port") == external_port
                        and self.view.get("https_activation", "") == activation):
                    return
            from cheroot.wsgi import Server
            from cheroot.ssl.builtin import BuiltinSSLAdapter
            from web_https_app import HTTPSApplication
            class ExclusiveServer(Server):
                @staticmethod
                def bind_socket(sock, bind_addr):
                    if os.name == "nt":
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    return Server.bind_socket(sock, bind_addr)
            adapter = BuiltinSSLAdapter(str(certificate), str(private_key), private_key_password=password)
            adapter.context.minimum_version = ssl.TLSVersion.TLSv1_2
            adapter.context.set_alpn_protocols(["http/1.1"])
            def sni(connection, name, context):
                if name != domain:
                    return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
                connection.sni = name
            adapter.context.set_servername_callback(sni)
            app = HTTPSApplication(domain, port, self.schedule, self.access, self.logger, external_port=external_port,
                                   authority_check=lambda: external_allowed(self.root, self.run_id), assets=self.assets)
            server = ExclusiveServer((bind_host, port), app, numthreads=8, max=8, request_queue_size=32,
                            timeout=5, shutdown_timeout=0.5, accepted_queue_size=32, accepted_queue_timeout=0.1)
            server.ssl_adapter = adapter
            server.max_request_header_size = 8192
            server.max_request_body_size = 512
            server.error_log = lambda *args, **kwargs: None  # No raw requests/tracebacks/credentials.
            self._stop()
            if self.closed.is_set():
                return
            server.prepare()
            with self.lock:
                if self.closed.is_set():
                    server.stop()
                    return
                self.server, self.app = server, app
                self.view.update(https_state="running", https_port=port, https_domain=domain,
                                 https_revision=info["revision"], https_public=public, https_external_port=external_port,
                                 https_activation=activation, https_bind=bind_host,
                                 https_message="HTTPS 공개 연결 준비됨 · 외부 접속 미검증" if public else "로컬 HTTPS 실행 중 · 외부 공개 꺼짐")
            diagnostic(self.logger, "https_listener_ready", port=port, domain=domain, public=public)
            threading.Thread(target=self._serve, args=(server,), name="web-https-serve", daemon=True).start()
        except Exception as exc:
            if server:
                try:
                    server.stop()
                except Exception:
                    pass
            self._stop()
            with self.lock:
                self.view.update(https_state="error", https_message="HTTPS 시작 실패 · 인증서·포트와 진단 로그를 확인해주세요.")
            diagnostic(self.logger, "https_listener_failed", error_type=type(exc).__name__)

    def _serve(self, server):
        try:
            server.serve()
        except Exception as exc:
            diagnostic(self.logger, "https_serve_failed", error_type=type(exc).__name__)
        finally:
            with self.lock:
                if self.server is server and not self.closed.is_set():
                    self.server = None
                    self.view.update(https_state="error", https_message="HTTPS 연결이 중단됐습니다.")
            try:
                server.stop()
            except Exception:
                pass

    def close(self):
        self.closed.set()
        self._stop()

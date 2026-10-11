"""Read-only HTTPS WSGI endpoints, with Secure cookies and strict origins."""
from __future__ import annotations

from http import HTTPStatus
import json
import ipaddress
import hashlib
from urllib.parse import parse_qs

from web_service_common import PHASE, PROTOCOL, diagnostic, timestamp
from web_asset_store import bundled_assets
from web_request_limits import READ_PATHS


class HTTPSApplication:
    def __init__(self, domain, port, schedule, access, logger, external_port=None, authority_check=None, assets=None):
        self.domain, self.port = domain, port
        self.schedule, self.access, self.logger = schedule, access, logger
        self.request_count = 0
        self.started_at = timestamp()
        self.external_port = external_port
        self.authority_check = authority_check or (lambda: False)
        self.assets = assets if assets is not None else bundled_assets()
        self.asset_etags = {"/" + name: '"' + hashlib.sha256(self.assets[name]).hexdigest() + '"'
                            for name in ("schedule.js", "login.js", "style.css")}
        import threading
        self.lock = threading.Lock()

    def __call__(self, environ, start_response):
        cookies = []
        def send(code, content, mime="application/json; charset=utf-8", extra=()):
            if isinstance(content, dict):
                content = json.dumps(content, ensure_ascii=False).encode("utf-8")
            headers = [("Content-Type", mime),
                       ("Cache-Control", dict(extra).get("Cache-Control", "no-store")), ("X-Content-Type-Options", "nosniff"),
                       ("Referrer-Policy", "no-referrer"),
                       ("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
                        "connect-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")]
            headers.extend(("Set-Cookie", value) for value in cookies)
            if code != 304:
                headers.append(("Content-Length", str(len(content))))
            headers.extend((name, value) for name, value in extra if name.lower() != "cache-control")
            start_response(f"{code} {HTTPStatus(code).phrase}", headers)
            return [content]
        try:
            try:
                local = ipaddress.ip_address(environ.get("REMOTE_ADDR", "")).is_loopback
            except ValueError:
                local = False
            if not local and not self.authority_check():
                return send(503, dict(message="웹 담당자 전환 중입니다. 잠시 뒤 다시 접속해주세요."))
            host = environ.get("HTTP_HOST", "").lower()
            allowed = {f"{self.domain}:{self.port}"}
            if self.external_port:
                allowed.add(f"{self.domain}:{self.external_port}")
            if self.port == 443 or self.external_port == 443:
                allowed.add(self.domain)
            if host not in allowed or environ.get("wsgi.url_scheme") != "https":
                return send(403, dict(message="접속 주소를 확인해주세요."))
            with self.lock:
                self.request_count += 1
            context = self.access.context(environ.get("HTTP_COOKIE", ""), environ.get("REMOTE_ADDR", ""),
                                          environ.get("HTTP_USER_AGENT", ""), secure=True)
            cookies.extend(context["cookies"])
            if context["blocked"]:
                return send(403, dict(message="관리자가 이 접속을 차단했습니다."))
            method, path = environ.get("REQUEST_METHOD"), environ.get("PATH_INFO", "")
            if method == "GET":
                if path in READ_PATHS:
                    wait = self.access.read_wait(context, environ.get("REMOTE_ADDR", ""))
                    if wait:
                        message = "요청이 너무 많습니다. 잠시 뒤 다시 시도해주세요."
                        if path == "/":
                            return send(429, ("<!doctype html><html lang='ko'><meta charset='utf-8'>"
                                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                                f"<p>{message} ({wait}초)</p><a href='/'>다시 열기</a></html>").encode("utf-8"),
                                "text/html; charset=utf-8", (("Retry-After", str(wait)),))
                        return send(429, dict(message=message), extra=(("Retry-After", str(wait)),))
                assets = {"/": (self.assets["index.html" if context["authenticated"] else "login.html"], "text/html; charset=utf-8"),
                          "/schedule.js": (self.assets["schedule.js"], "text/javascript; charset=utf-8"),
                          "/login.js": (self.assets["login.js"], "text/javascript; charset=utf-8"),
                          "/style.css": (self.assets["style.css"], "text/css; charset=utf-8")}
                if path in assets:
                    if path in self.asset_etags:
                        etag = self.asset_etags[path]
                        headers = (("ETag", etag), ("Cache-Control", "private, no-cache"))
                        if etag in {value.strip() for value in environ.get("HTTP_IF_NONE_MATCH", "").split(",")}:
                            return send(304, b"", extra=headers)
                        return send(200, *assets[path], extra=headers)
                    return send(200, *assets[path])
                if path == "/favicon.ico":
                    return send(204, b"", "image/x-icon")
                if path == "/api/session":
                    return send(200, {key: context[key] for key in ("authenticated", "configured", "available", "reauth_required")})
                if path in {"/api/status", "/api/schedule"}:
                    if not context["authenticated"]:
                        code = 401 if context["available"] else 503
                        return send(code, dict(message="비밀번호를 입력해주세요." if code == 401 else "웹 비밀번호 설정을 확인해주세요."))
                    if path == "/api/schedule":
                        return send(*self.schedule.payload())
                    known = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=4).get("major_version", [None])[0]
                    schedule = self.schedule.status(known_major_version=known)
                    return send(200, dict(online=True, server_time=timestamp(), started_at=self.started_at,
                                         updated_at=schedule["schedule_updated_at"], phase=PHASE,
                                         protocol_version=PROTOCOL, **schedule))
                return send(404, dict(message="페이지를 찾지 못했습니다."))
            if method != "POST" or path not in {"/api/login", "/api/logout"}:
                return send(405, dict(message="조회 전용 웹서비스입니다."))
            if (environ.get("HTTP_ORIGIN", "").lower() != "https://" + host
                    or environ.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/json"
                    or environ.get("HTTP_TRANSFER_ENCODING")):
                return send(403, dict(message="페이지를 다시 열어주세요."))
            length = environ.get("CONTENT_LENGTH", "")
            if not length.isascii() or not length.isdigit() or not 1 <= int(length) <= 512:
                return send(400, dict(message="입력 값을 확인해주세요."))
            try:
                body = environ["wsgi.input"].read(int(length))
                if len(body) != int(length):
                    raise ValueError("short body")
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("invalid body")
            except (ValueError, UnicodeError, OSError):
                return send(400, dict(message="입력 값을 확인해주세요."))
            if path == "/api/logout":
                cookies.append(self.access.logout(context, secure=True))
                return send(200, dict(message="로그아웃했습니다."))
            code, message, issued = self.access.login(context, payload.get("pin"), environ.get("REMOTE_ADDR", ""),
                                                    secure=True, nickname=payload.get("nickname", "guest"))
            cookies.extend(issued)
            return send(code, dict(message=message))
        except Exception as exc:
            diagnostic(self.logger, "https_request_failed", error_type=type(exc).__name__)
            return send(503, dict(message="잠시 후 다시 시도해주세요."))

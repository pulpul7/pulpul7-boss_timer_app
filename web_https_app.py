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
from web_access_runtime import records_visit
from web_theme_bootstrap import SCRIPT_SOURCE, render_theme_page
from web_live_assets import AssetSnapshot
from web_reload_wait import SCRIPT_SOURCE as RELOAD_SCRIPT_SOURCE, render_reload_wait


class HTTPSApplication:
    def __init__(self, domain, port, schedule, access, logger, external_port=None, authority_check=None, assets=None, push=None, asset_source=None):
        self.domain, self.port = domain, port
        self.schedule, self.access, self.logger = schedule, access, logger
        self.push = push
        self.request_count = 0
        self.started_at = timestamp()
        self.external_port = external_port
        self.authority_check = authority_check or (lambda: False)
        self.asset_source = asset_source
        self._asset_snapshot = None if asset_source is not None else AssetSnapshot.create(
            assets if assets is not None else bundled_assets())
        import threading
        self.lock = threading.Lock()

    @property
    def asset_snapshot(self):
        return self.asset_source.snapshot if self.asset_source is not None else self._asset_snapshot

    @property
    def assets(self):
        return self.asset_snapshot.files

    @property
    def asset_etags(self):
        return self.asset_snapshot.etags

    def __call__(self, environ, start_response):
        cookies = []
        def send(code, content, mime="application/json; charset=utf-8", extra=()):
            if isinstance(content, dict):
                content = json.dumps(content, ensure_ascii=False).encode("utf-8")
            headers = [("Content-Type", mime),
                       ("Cache-Control", dict(extra).get("Cache-Control", "no-store")), ("X-Content-Type-Options", "nosniff"),
                       ("Referrer-Policy", "no-referrer"),
                       ("Content-Security-Policy", "default-src 'none'; script-src 'self' " + SCRIPT_SOURCE + " " + RELOAD_SCRIPT_SOURCE + "; style-src 'self'; "
                        "connect-src 'self'; img-src 'self' blob:; font-src 'self' blob:; media-src 'self' blob:; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'")]
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
            method, path = environ.get("REQUEST_METHOD"), environ.get("PATH_INFO", "")
            raw_cookie, ip = environ.get("HTTP_COOKIE", ""), environ.get("REMOTE_ADDR", "")
            record_visit = records_visit(method, path, ip, raw_cookie)
            context = self.access.context(raw_cookie, ip, environ.get("HTTP_USER_AGENT", ""),
                                          secure=True, record_visit=record_visit)
            cookies.extend(context["cookies"])
            if context["blocked"]:
                return send(403, dict(message="관리자가 이 접속을 차단했습니다.", access_blocked=True))
            if method == "GET":
                if path in READ_PATHS and (record_visit or path in {'/', '/api/boss-spectrum'}):
                    wait = self.access.read_wait(context, environ.get("REMOTE_ADDR", ""), path=path)
                    if wait:
                        message = "요청이 너무 많습니다. 잠시 뒤 다시 시도해주세요."
                        if path == "/":
                            return send(429, render_reload_wait(wait),
                                "text/html; charset=utf-8", (("Retry-After", str(wait)),))
                        return send(429, dict(message=message), extra=(("Retry-After", str(wait)),))
                snapshot = self.asset_snapshot
                files, etags = snapshot.files, snapshot.etags
                assets = {"/": (files["index.html" if context["authenticated"] else "login.html"], "text/html; charset=utf-8"),
                          "/schedule.js": (files["schedule.js"], "text/javascript; charset=utf-8"),
                          "/login.js": (files["login.js"], "text/javascript; charset=utf-8"),
                          "/style.css": (files["style.css"], "text/css; charset=utf-8")}
                if path in assets:
                    if path == '/':
                        from web_font_library import font_developer_allowed
                        page, mime = assets[path]
                        return send(200, render_theme_page(page, font_developer_allowed(self.access, context, ip)), mime)
                    if path in etags:
                        etag = etags[path]
                        headers = (("ETag", etag), ("Cache-Control", "private, no-cache"))
                        if etag in {value.strip() for value in environ.get("HTTP_IF_NONE_MATCH", "").split(",")}:
                            return send(304, b"", extra=headers)
                        return send(200, *assets[path], extra=headers)
                    return send(200, *assets[path])
                if path.startswith('/themes/'):
                    if not context['authenticated']:
                        return send(401, dict(message='다시 로그인해주세요.'))
                    try:
                        from web_limited_sakura import theme_asset
                        resource = theme_asset(path)
                        if resource is None:
                            return send(404, dict(message='테마 이미지가 목록에 없습니다.'))
                        etag = '"' + hashlib.sha256(resource[0]).hexdigest() + '"'
                        extra = (("ETag", etag), ("Cache-Control", "private, no-cache"))
                        if etag in {v.strip() for v in environ.get('HTTP_IF_NONE_MATCH', '').split(',')}:
                            return send(304, b'', extra=extra)
                        return send(200, *resource, extra=extra)
                    except Exception as exc:
                        diagnostic(self.logger, 'web_theme_asset_failed', error_type=type(exc).__name__)
                        return send(503, dict(message='꽃 장식을 불러오지 못했습니다. 스케줄은 계속 사용할 수 있습니다.'))
                if path.startswith('/sounds/'):
                    if not context['authenticated']:
                        return send(401, dict(message='다시 로그인해주세요.'))
                    try:
                        from web_notice_audio import sound_asset
                        resource = sound_asset(path)
                        if resource is None:
                            return send(404, dict(message='알림음이 목록에 없습니다.'))
                        etag = '"' + hashlib.sha256(resource[0]).hexdigest() + '"'
                        extra = (("ETag", etag), ("Cache-Control", "private, no-cache"))
                        if etag in {v.strip() for v in environ.get('HTTP_IF_NONE_MATCH', '').split(',')}:
                            return send(304, b'', extra=extra)
                        return send(200, *resource, extra=extra)
                    except Exception as exc:
                        diagnostic(self.logger, 'web_notice_sound_asset_failed', error_type=type(exc).__name__)
                        return send(503, dict(message='알림음을 불러오지 못했습니다. 배너는 계속 사용할 수 있습니다.'))
                if path.startswith('/fonts/'):
                    if not context['authenticated']:
                        return send(401, dict(message='다시 로그인해주세요.'))
                    try:
                        from web_font_library import font_asset
                        resource = font_asset(self.access.root, path, access=self.access, context=context, ip=environ.get('REMOTE_ADDR', ''))
                        if resource is None:
                            return send(404, dict(message='폰트가 목록에 없습니다.'))
                        etag = '"' + hashlib.sha256(resource[0]).hexdigest() + '"'
                        extra = (("ETag", etag), ("Cache-Control", "private, no-cache"))
                        if etag in {v.strip() for v in environ.get('HTTP_IF_NONE_MATCH', '').split(',')}:
                            return send(304, b'', extra=extra)
                        return send(200, *resource, extra=extra)
                    except Exception as exc:
                        diagnostic(self.logger, 'web_font_asset_failed', error_type=type(exc).__name__)
                        return send(503, dict(message='폰트를 불러오지 못했습니다. 기본 글꼴로 표시합니다.'))
                from web_push import push_asset
                resource = push_asset(path)
                if resource:
                    if path == "/nickname-defender.png":
                        etag = '"' + hashlib.sha256(resource[0]).hexdigest() + '"'
                        extra = (("ETag", etag), ("Cache-Control", "private, no-cache"))
                        if etag in {v.strip() for v in environ.get("HTTP_IF_NONE_MATCH", "").split(",")}:
                            return send(304, b"", extra=extra)
                        return send(200, *resource, extra=extra)
                    return send(200, *resource)
                if path == "/favicon.ico":
                    return send(204, b"", "image/x-icon")
                if path == "/api/session":
                    return send(200, {key: context[key] for key in ("authenticated", "configured", "available", "reauth_required")})
                if path == '/api/boss-spectrum':
                    if not context['authenticated']:
                        return send(401, dict(message='다시 로그인해주세요.'))
                    try:
                        from web_boss_spectrum import public_spectrum
                        return send(200, public_spectrum(self.access, context, ip))
                    except Exception as exc:
                        diagnostic(self.logger, 'web_boss_spectrum_load_failed', error_type=type(exc).__name__)
                        return send(503, dict(message='보스 색상을 불러오지 못했습니다. 기존 색상을 사용합니다.'))
                if path == "/api/push":
                    if not context["authenticated"]:
                        return send(401, dict(message="다시 로그인해주세요."))
                    return send(200, self.push.config(context) if self.push else
                                dict(ready=False, message="푸시 모듈을 준비하지 못했습니다. 스케줄은 계속 이용할 수 있습니다."))
                if path in {"/api/status", "/api/schedule"}:
                    if not context["authenticated"]:
                        code = 401 if context["available"] else 503
                        return send(code, dict(message="비밀번호를 입력해주세요." if code == 401 else "웹 비밀번호 설정을 확인해주세요."))
                    if path == "/api/schedule":
                        return send(*self.schedule.payload())
                    query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=4)
                    schedule = self.schedule.status(known_major_version=query.get("major_version", [None])[0],
                                                    known_catalog_version=query.get("catalog_version", [None])[0])
                    if self.push:
                        schedule["push_registered"] = self.push.registered(context)
                    return send(200, dict(online=True, server_time=timestamp(), started_at=self.started_at,
                                         updated_at=schedule["schedule_updated_at"], phase=PHASE,
                                         protocol_version=PROTOCOL, **schedule))
                return send(404, dict(message="페이지를 찾지 못했습니다."))
            if method != "POST" or path not in {"/api/login", "/api/logout", "/api/push", "/api/fonts/default-size", "/api/boss-spectrum"}:
                return send(405, dict(message="조회 전용 웹서비스입니다."))
            if (environ.get("HTTP_ORIGIN", "").lower() != "https://" + host
                    or environ.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/json"
                    or environ.get("HTTP_TRANSFER_ENCODING")):
                return send(403, dict(message="페이지를 다시 열어주세요."))
            length = environ.get("CONTENT_LENGTH", "")
            limit = 256000 if path == "/api/push" else 128000 if path == '/api/boss-spectrum' else 512
            if not length.isascii() or not length.isdigit() or not 1 <= int(length) <= limit:
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
            if path == '/api/boss-spectrum':
                if not context['authenticated']:
                    return send(401, dict(message='다시 로그인해주세요.'))
                wait = self.access.read_wait(context, ip)
                if wait:
                    return send(429, dict(message='잠시 뒤 다시 시도해주세요.'), extra=(('Retry-After', str(wait)),))
                try:
                    from web_boss_spectrum import save_spectrum
                    code, snapshot = self.schedule.payload()
                    if code != 200 or not isinstance(snapshot.get('boss_catalog'), list):
                        return send(503, dict(message='보스 목록을 준비한 뒤 다시 저장해주세요.'))
                    result = save_spectrum(self.access, context, ip, payload, snapshot['boss_catalog'])
                    diagnostic(self.logger, 'web_boss_spectrum_saved', theme=payload.get('theme'), scope='all_light_themes', number=payload.get('number'), count=len(payload.get('bosses', [])))
                    return send(200, result)
                except PermissionError as exc:
                    return send(403, dict(message=str(exc)))
                except ValueError as exc:
                    return send(400, dict(message=str(exc)))
                except Exception as exc:
                    diagnostic(self.logger, 'web_boss_spectrum_save_failed', error_type=type(exc).__name__)
                    return send(503, dict(message='보스 스펙트럼을 저장하지 못했습니다.'))
            if path == '/api/fonts/default-size':
                if not context['authenticated']:
                    return send(401, dict(message='다시 로그인해주세요.'))
                wait = self.access.read_wait(context, environ.get('REMOTE_ADDR', ''))
                if wait:
                    return send(429, dict(message='잠시 뒤 다시 시도해주세요.'), extra=(('Retry-After', str(wait)),))
                from web_font_library import save_font_size_default
                try:
                    result = save_font_size_default(self.access, context, environ.get('REMOTE_ADDR', ''), payload)
                    diagnostic(self.logger, 'web_font_size_default_saved', font_id=payload.get('font_id'), size=payload.get('size'))
                    return send(200, result)
                except PermissionError as exc:
                    return send(403, dict(message=str(exc)))
                except ValueError as exc:
                    return send(400, dict(message=str(exc)))
                except Exception as exc:
                    diagnostic(self.logger, 'web_font_size_default_failed', error_type=type(exc).__name__)
                    return send(503, dict(message='기본 크기를 저장하지 못했습니다.'))
            if path == "/api/logout":
                if self.push:
                    try:
                        self.push.disable(context)
                    except Exception as exc:
                        diagnostic(self.logger, "push_logout_cleanup_failed", error_type=type(exc).__name__)
                cookies.append(self.access.logout(context, secure=True))
                return send(200, dict(message="로그아웃했습니다.", retry_after=2))
            if path == "/api/push":
                if not context["authenticated"]:
                    return send(401, dict(message="다시 로그인해주세요."))
                wait = self.access.read_wait(context, environ.get("REMOTE_ADDR", ""))
                if wait:
                    return send(429, dict(message="잠시 뒤 다시 시도해주세요."), extra=(("Retry-After", str(wait)),))
                if self.push is None:
                    return send(503, dict(message="푸시 모듈을 준비하지 못했습니다."))
                try:
                    if payload.get("action") == "test":
                        return send(200, self.push.test(context))
                    self.push.update(context, payload)
                    return send(200, dict(message="푸시 설정을 저장했습니다."))
                except ValueError as exc:
                    return send(400, dict(message=str(exc)))
            code, message, issued = self.access.login(context, payload.get("pin"), environ.get("REMOTE_ADDR", ""),
                                                    secure=True, nickname=payload.get("nickname", "guest"))
            cookies.extend(issued)
            wait = (self.access.logout_wait(context) or 2) if code == 429 else 0
            return send(code, dict(message=message, retry_after=wait),
                        extra=(("Retry-After", str(wait)),) if wait else ())
        except Exception as exc:
            diagnostic(self.logger, "https_request_failed", error_type=type(exc).__name__)
            return send(503, dict(message="잠시 후 다시 시도해주세요."))

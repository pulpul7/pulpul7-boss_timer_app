"""Loopback-only read-only web process. Never imports the BossTimer GUI or bot.

The development HTTP transport stays loopback-only. The separate Cheroot HTTPS
listener accepts external connections only with a current owned publication lease.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
import time
from urllib.parse import urlsplit, parse_qs

from web_service_common import (PHASE, PROTOCOL, close_logger, diagnostic,
                                diagnostic_logger, timestamp, worker_paths, write_json)
from web_schedule_cache import ScheduleCache
from web_asset_store import load_assets, git_hash, version_id
from web_access_runtime import AccessRuntime, records_visit
from web_https_server import HTTPSListener



class ParentWatch:
    """Keep a Windows process handle so PID reuse cannot keep an orphan alive."""
    def __init__(self, pid):
        if pid <= 0 or pid == os.getpid():
            raise ValueError("invalid parent process")
        self.pid, self.handle, self.kernel = pid, None, None
        if os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            self.kernel.CloseHandle.restype = wintypes.BOOL
            self.handle = self.kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())

    def alive(self):
        if self.kernel:
            return self.kernel.WaitForSingleObject(self.handle, 0) == 0x102
        try:
            os.kill(self.pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class LocalWebServer(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"
    daemon_threads = True
    block_on_close = False

    def __init__(self, port, logger, schedule, access):
        self.logger = logger
        self.schedule = schedule
        self.access = access
        self.started_at = timestamp()
        self.request_count = 0
        self.counter_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(("127.0.0.1", port), LocalHandler)

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        diagnostic(self.logger, "http_request_failed")


class LocalHandler(BaseHTTPRequestHandler):
    server_version = "BossTimerWeb"
    sys_version = ""

    def setup(self):
        self.request.settimeout(2)
        super().setup()

    def log_message(self, *args):
        pass

    def _send(self, code, content, content_type, cookies=(), extra=()):
        from web_asset_store import git_hash
        path = urlsplit(self.path).path
        etag = '"' + git_hash(content) + '"' if (path in {"/schedule.js", "/login.js", "/style.css", "/nickname-defender.png"} or path.startswith(('/fonts/', '/sounds/', '/themes/'))) and code == 200 else ""
        if etag and etag in {value.strip() for value in self.headers.get("If-None-Match", "").split(",")}:
            code, content = 304, b""
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        if code != 304:
            self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, no-cache" if etag else "no-store")
        if etag:
            self.send_header("ETag", etag)
        for name, value in extra:
            self.send_header(name, value)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        from web_theme_bootstrap import SCRIPT_SOURCE
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self' " + SCRIPT_SOURCE + "; "
                         "style-src 'self'; connect-src 'self'; img-src 'self' blob:; font-src 'self' blob:; media-src 'self' blob:; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for value in cookies:
            self.send_header("Set-Cookie", value)
        self.end_headers()
        self.wfile.write(content)

    def _json(self, code, payload, cookies=(), extra=()):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", cookies, extra)

    def _context(self):
        port = self.server.server_port
        if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host", "").lower() not in {
                f"127.0.0.1:{port}", f"localhost:{port}"}:
            self._json(403, dict(message="접속 주소를 확인해주세요."))
            return None
        with self.server.counter_lock:
            self.server.request_count += 1
        self._record_visit = records_visit(self.command, urlsplit(self.path).path,
                                          self.client_address[0], self.headers.get("Cookie", ""))
        context = self.server.access.context(self.headers.get("Cookie", ""),
                    self.client_address[0], self.headers.get("User-Agent", ""),
                    record_visit=self._record_visit)
        if context["blocked"]:
            self._json(403, dict(message="관리자가 이 접속을 차단했습니다."), context["cookies"])
            return None
        return context

    def _authorized(self, context):
        if context["authenticated"]:
            return True
        code = 401 if context["available"] else 503
        self._json(code, dict(message="비밀번호를 입력해주세요." if code == 401 else
                   "관리자가 프로그램에서 웹 비밀번호를 먼저 설정해야 합니다."), context["cookies"])
        return False

    def do_GET(self):
        context = self._context()
        if context is None:
            return
        cookies = context["cookies"]
        assets = self.server.assets
        path = urlsplit(self.path).path
        from web_request_limits import READ_PATHS
        if path in READ_PATHS and (getattr(self, '_record_visit', True) or path == '/api/boss-spectrum'):
            wait = self.server.access.read_wait(context, self.client_address[0], path=path)
            if wait:
                message = "새로고침은 5초에 한 번 가능합니다. 잠시 뒤 다시 열어주세요." if path == '/' else "요청이 너무 많습니다. 잠시 뒤 다시 시도해주세요."
                content = (("<!doctype html><html lang='ko'><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                    f"<p>{message} ({wait}초)</p><a href='/'>다시 열기</a></html>").encode("utf-8") if path == "/"
                    else json.dumps(dict(message=message), ensure_ascii=False).encode("utf-8"))
                self._send(429, content, "text/html; charset=utf-8" if path == "/" else
                           "application/json; charset=utf-8", cookies, (("Retry-After", str(wait)),))
                return
        if path == "/":
            from web_font_library import font_developer_allowed
            from web_theme_bootstrap import render_theme_page
            page = render_theme_page(assets["index.html" if context["authenticated"] else "login.html"],
                                     font_developer_allowed(self.server.access, context, self.client_address[0]))
            self._send(200, page, "text/html; charset=utf-8", cookies)
        elif path == "/schedule.js":
            self._send(200, assets["schedule.js"], "text/javascript; charset=utf-8", cookies)
        elif path == "/login.js":
            self._send(200, assets["login.js"], "text/javascript; charset=utf-8", cookies)
        elif path == "/style.css":
            self._send(200, assets["style.css"], "text/css; charset=utf-8", cookies)
        elif path == '/api/boss-spectrum':
            if not self._authorized(context):
                return
            try:
                from web_boss_spectrum import public_spectrum
                self._json(200, public_spectrum(self.server.access, context, self.client_address[0]), cookies)
            except Exception as exc:
                diagnostic(self.server.logger, 'web_boss_spectrum_load_failed', error_type=type(exc).__name__)
                self._json(503, dict(message='보스 색상을 불러오지 못했습니다. 기존 색상을 사용합니다.'), cookies)
        elif path.startswith('/themes/'):
            if not self._authorized(context):
                return
            try:
                from web_limited_sakura import theme_asset
                resource = theme_asset(path)
                if resource is None:
                    self._json(404, dict(message='테마 이미지가 목록에 없습니다.'), cookies)
                else:
                    self._send(200, *resource, cookies)
            except Exception as exc:
                diagnostic(self.server.logger, 'web_theme_asset_failed', error_type=type(exc).__name__)
                self._json(503, dict(message='꽃 장식을 불러오지 못했습니다. 스케줄은 계속 사용할 수 있습니다.'), cookies)
        elif path.startswith('/sounds/'):
            if not self._authorized(context):
                return
            try:
                from web_notice_audio import sound_asset
                resource = sound_asset(path)
                if resource is None:
                    self._json(404, dict(message='알림음이 목록에 없습니다.'), cookies)
                else:
                    self._send(200, *resource, cookies)
            except Exception as exc:
                diagnostic(self.server.logger, 'web_notice_sound_asset_failed', error_type=type(exc).__name__)
                self._json(503, dict(message='알림음을 불러오지 못했습니다. 배너는 계속 사용할 수 있습니다.'), cookies)
        elif path.startswith('/fonts/'):
            if not self._authorized(context):
                return
            try:
                from web_font_library import font_asset
                resource = font_asset(self.server.access.root, path, access=self.server.access, context=context, ip=self.client_address[0])
                if resource is None:
                    self._json(404, dict(message='폰트가 목록에 없습니다.'), cookies)
                else:
                    self._send(200, *resource, cookies)
            except Exception as exc:
                diagnostic(self.server.logger, 'web_font_asset_failed', error_type=type(exc).__name__)
                self._json(503, dict(message='폰트를 불러오지 못했습니다. 기본 글꼴로 표시합니다.'), cookies)
        elif path == "/api/session":
            self._json(200, {key: context[key] for key in ("authenticated", "configured", "available", "reauth_required")}, cookies)
        elif path in {"/manifest.webmanifest", "/push-worker.js", "/nickname-defender.png"}:
            from web_push import push_asset
            raw, mime = push_asset(path)
            self._send(200, raw, mime, cookies)
        elif path == "/api/push":
            if self._authorized(context):
                self._json(200, dict(ready=False, message="푸시는 공개 HTTPS 주소에서 휴대폰으로 설정해주세요."), cookies)
        elif path == "/api/status":
            if not self._authorized(context):
                return
            query = parse_qs(urlsplit(self.path).query, max_num_fields=4)
            schedule = self.server.schedule.status(known_major_version=query.get("major_version", [None])[0],
                                                   known_catalog_version=query.get("catalog_version", [None])[0])
            payload = dict(online=True, server_time=timestamp(),
                           updated_at=schedule["schedule_updated_at"],
                           started_at=self.server.started_at, phase=PHASE, protocol_version=PROTOCOL,
                           **schedule)
            self._json(200, payload, cookies)
        elif path == "/api/schedule":
            if not self._authorized(context):
                return
            code, payload = self.server.schedule.payload()
            self._json(code, payload, cookies)
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        context = self._context()
        if context is None:
            return
        path = urlsplit(self.path).path
        if path not in {"/api/login", "/api/logout", "/api/fonts/default-size", "/api/boss-spectrum"}:
            self._json(405, dict(message="조회 전용 웹서비스입니다."), context["cookies"])
            return
        expected_origin = "http://" + self.headers.get("Host", "").lower()
        if (len(self.headers.get_all("Origin", [])) != 1 or self.headers.get("Origin", "").lower() != expected_origin
                or self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json"
                or self.headers.get("Transfer-Encoding") is not None):
            self._json(403, dict(message="페이지를 다시 열어주세요."), context["cookies"])
            return
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                raise ValueError("invalid length")
            length = int(lengths[0])
            if not 1 <= length <= (128000 if path == '/api/boss-spectrum' else 512):
                raise ValueError("invalid length")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid body")
        except (ValueError, UnicodeError, OSError):
            self._json(400, dict(message="입력 값을 확인해주세요."), context["cookies"])
            return
        if path == '/api/boss-spectrum':
            if not self._authorized(context):
                return
            wait = self.server.access.read_wait(context, self.client_address[0])
            if wait:
                self._json(429, dict(message='잠시 뒤 다시 시도해주세요.'), context['cookies'], extra=(('Retry-After', str(wait)),))
                return
            try:
                from web_boss_spectrum import save_spectrum
                code, snapshot = self.server.schedule.payload()
                if code != 200 or not isinstance(snapshot.get('boss_catalog'), list):
                    self._json(503, dict(message='보스 목록을 준비한 뒤 다시 저장해주세요.'), context['cookies'])
                    return
                result = save_spectrum(self.server.access, context, self.client_address[0], payload, snapshot['boss_catalog'])
                diagnostic(self.server.logger, 'web_boss_spectrum_saved', theme=payload.get('theme'), scope='all_light_themes', number=payload.get('number'), count=len(payload.get('bosses', [])))
                self._json(200, result, context['cookies'])
            except PermissionError as exc:
                self._json(403, dict(message=str(exc)), context['cookies'])
            except ValueError as exc:
                self._json(400, dict(message=str(exc)), context['cookies'])
            except Exception as exc:
                diagnostic(self.server.logger, 'web_boss_spectrum_save_failed', error_type=type(exc).__name__)
                self._json(503, dict(message='보스 스펙트럼을 저장하지 못했습니다.'), context['cookies'])
            return
        if path == '/api/fonts/default-size':
            if not self._authorized(context):
                return
            wait = self.server.access.read_wait(context, self.client_address[0])
            if wait:
                self._json(429, dict(message='잠시 뒤 다시 시도해주세요.'), context['cookies'], extra=(('Retry-After', str(wait)),))
                return
            from web_font_library import save_font_size_default
            try:
                result = save_font_size_default(self.server.access, context, self.client_address[0], payload)
                diagnostic(self.server.logger, 'web_font_size_default_saved', font_id=payload.get('font_id'), size=payload.get('size'))
                self._json(200, result, context['cookies'])
            except PermissionError as exc:
                self._json(403, dict(message=str(exc)), context['cookies'])
            except ValueError as exc:
                self._json(400, dict(message=str(exc)), context['cookies'])
            except Exception as exc:
                diagnostic(self.server.logger, 'web_font_size_default_failed', error_type=type(exc).__name__)
                self._json(503, dict(message='기본 크기를 저장하지 못했습니다.'), context['cookies'])
            return
        if path == "/api/logout":
            self._json(200, dict(message="로그아웃했습니다.", retry_after=2),
                       [*context["cookies"], self.server.access.logout(context)])
            return
        try:
            code, message, cookies = self.server.access.login(context, payload.get("pin"), self.client_address[0],
                                                            nickname=payload.get("nickname", "guest"))
            wait = (self.server.access.logout_wait(context) or 2) if code == 429 else 0
            self._json(code, dict(message=message, retry_after=wait), [*context["cookies"], *cookies],
                       extra=(("Retry-After", str(wait)),) if wait else ())
        except Exception as exc:
            diagnostic(self.server.logger, "login_processing_failed", error_type=type(exc).__name__)
            self._json(503, dict(message="로그인 처리 중 문제가 생겼습니다. 잠시 후 다시 시도해주세요."))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        return 2
    root = Path(args.root)
    status_path, stop_path = worker_paths(root, args.run_id)
    logger = diagnostic_logger(root, "worker")
    server = parent = access = https = push = None
    state = dict(run_id=args.run_id, pid=os.getpid(), protocol=PROTOCOL,
                 port=args.port, state="starting", heartbeat=0, request_count=0)
    try:
        parent = ParentWatch(args.parent_pid)
        if not parent.alive() or stop_path.exists():
            return 0
        diagnostic(logger, "worker_start", run_id=args.run_id, port=args.port)
        schedule = ScheduleCache(root, args.run_id, logger)
        access = AccessRuntime(root, args.run_id, logger)
        access.refresh()
        try:
            from web_push import PushRuntime
            push = PushRuntime(root, args.run_id, schedule, access, logger)
            push.start()
        except Exception as exc:
            diagnostic(logger, "push_start_failed", error_type=type(exc).__name__)
        server = LocalWebServer(args.port, logger, schedule, access)
        server.assets = load_assets(root, logger)
        state["web_page_version"] = version_id({name: git_hash(raw) for name, raw in server.assets.items()})
        https = HTTPSListener(root, schedule, access, logger, args.port, args.run_id, push=push)
        server.timeout = 0.25
        state["state"] = "running"
        diagnostic(logger, "local_listener_ready", run_id=args.run_id, port=args.port)
        next_heartbeat = 0.0
        next_schedule_read = 0.0
        heartbeat_write_failed = False
        while parent.alive() and not stop_path.exists():
            if time.monotonic() >= next_schedule_read:
                schedule.refresh()
                access.refresh()
                https.refresh()
                next_schedule_read = time.monotonic() + 1
            if time.monotonic() >= next_heartbeat:
                state["heartbeat"] += 1
                with server.counter_lock:
                    state["request_count"] = server.request_count
                state["checked_at"] = timestamp()
                state.update(schedule.status())
                state.update(access.summary())
                state.update(https.status())
                state["request_count"] += state.get("https_request_count", 0)
                try:
                    write_json(status_path, state)
                except OSError as exc:
                    if not heartbeat_write_failed:
                        diagnostic(logger, "heartbeat_write_failed", file=status_path.name,
                                   error_type=type(exc).__name__, errno=exc.errno,
                                   winerror=getattr(exc, "winerror", None))
                    heartbeat_write_failed = True
                    next_heartbeat = time.monotonic() + 1
                else:
                    if heartbeat_write_failed:
                        diagnostic(logger, "heartbeat_write_recovered")
                    heartbeat_write_failed = False
                    next_heartbeat = time.monotonic() + 2
            access.flush()
            server.handle_request()
        state["state"] = "stopped"
        diagnostic(logger, "worker_stop", run_id=args.run_id,
                   reason="stop_requested" if stop_path.exists() else "parent_exited")
        return 0
    except Exception as exc:
        state.update(state="error", error_code="port_in_use" if isinstance(exc, OSError)
                     and (getattr(exc, "winerror", None) == 10048 or exc.errno == 98) else "worker_failed")
        diagnostic(logger, state["error_code"], run_id=args.run_id, error_type=type(exc).__name__,
                   errno=getattr(exc, "errno", None), winerror=getattr(exc, "winerror", None))
        return 1
    finally:
        if push:
            push.close()
        if https:
            https.close()
        if server:
            server.server_close()
        if parent:
            parent.close()
        if access:
            access.flush(force=True)
        try:
            write_json(status_path, state)
        except OSError:
            pass
        close_logger(logger)


if __name__ == "__main__":
    raise SystemExit(main())

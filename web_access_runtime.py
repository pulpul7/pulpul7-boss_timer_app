"""Worker-only authentication, bounded visitor history and login throttling."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import hashlib
import hmac
from http.cookies import SimpleCookie
import math
import re
import secrets
import threading
import time

from web_access_settings import COOKIE_AGE, ITERATIONS, normalize_ip, valid_id, validate_settings
from web_request_limits import CooldownLimiter, ReadLimiter
from web_service_common import (PROTOCOL, ACCESS_DISPLAY_DAYS, ACCESS_RETENTION_DAYS,
                                access_state_path, diagnostic, read_json, timestamp, write_json)

VISITOR_COOKIE = "bt_visitor"
SESSION_COOKIE = "bt_session"
MAX_VISITORS = 1000
VISITOR_PATHS = frozenset({'/', '/api/login', '/api/logout', '/api/session', '/api/status', '/api/schedule', '/api/push'})


def records_visit(method, path, ip, raw_cookie):
    """Keep static assets, unknown paths and cookie-free local TLS probes out of visitor history."""
    if method not in {'GET', 'POST'} or path not in VISITOR_PATHS:
        return False
    if method == 'GET' and path in {'/', '/api/session'} and not raw_cookie:
        import ipaddress
        try:
            local = ipaddress.ip_address(ip).is_loopback
        except ValueError:
            local = False
        # Strict cookies can be withheld on cross-site entry. The following
        # same-site session fetch must see them before any visitor is replaced.
        # Loopback session probes remain stateless; local browsers start at /.
        if (path == '/' and not local) or (path == '/api/session' and local):
            return False
    return True


def _session_role(settings, epoch):
    for role, prefix in (("member", ""), ("guest", "guest_")):
        if settings[prefix + "verifier"] and epoch == settings[prefix + "epoch"]:
            return role
    return ""


def _nickname(value):
    if not isinstance(value, str) or len(value) > 40 or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("닉네임은 40자 이내로 입력해주세요.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("닉네임에 사용할 수 없는 문자가 있습니다.") from None
    return value.strip() or "guest"


def cookie_header(name, value, secure=False, *, age=COOKIE_AGE):
    return f"{name}={value}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict" + ("; Secure" if secure else "")


def _browser(agent):
    browser = "Edge" if "Edg/" in agent else "Firefox" if "Firefox/" in agent else \
              "Chrome" if "Chrome/" in agent else "Safari" if "Safari/" in agent else "기타 브라우저"
    device = "Android" if "Android" in agent else "iPhone/iPad" if any(x in agent for x in ("iPhone", "iPad")) else \
             "Windows" if "Windows" in agent else "Mac" if "Macintosh" in agent else "기타 기기"
    return browser + " · " + device


class AccessRuntime:
    def __init__(self, root, run_id, logger):
        self.root, self.run_id, self.logger = root, run_id, logger
        self.lock = threading.RLock()
        self.settings = None
        self.available = False
        self.settings_signature = None
        self.visitors = {}
        self.failures = deque(maxlen=200)
        self.attempts = deque(maxlen=120)
        self.inflight = 0
        self.dirty = True
        self.next_flush = 0.0
        self.flush_failed = False
        self.policy_failed = False
        self.last_throttle_notice = 0.0
        self.read_limiter = ReadLimiter()
        self.cooldowns = CooldownLimiter()
        self.last_read_throttle_notice = 0.0
        try:
            saved = read_json(root / "visitors.json")
            rows = saved.get("visitors", [])
            failures = saved.get("failures", [])
            if (saved.get("schema") != 1 or not isinstance(rows, list) or len(rows) > MAX_VISITORS
                    or not isinstance(failures, list) or len(failures) > 200):
                raise ValueError("invalid visitor history")
            for row in rows:
                self._validate_visitor(row)
                self.visitors[row["id"]] = row
            for row in failures:
                if (not isinstance(row, dict) or not self._valid_time(row.get("at"))
                        or not isinstance(row.get("ip"), str) or not valid_id(row.get("visitor"))):
                    raise ValueError("invalid login history")
                normalize_ip(row["ip"])
                self.failures.append(dict(at=row["at"], ip=row["ip"], visitor=row["visitor"]))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            self.visitors.clear()
            self.failures.clear()
            diagnostic(logger, "visitor_history_read_failed", error_type=type(exc).__name__)
        with self.lock:
            self._prune_history(time.time())

    def _prune_history(self, now):
        """Call under the lock; visitor retention starts at its latest visit."""
        cutoff = now - ACCESS_RETENTION_DAYS * 86400
        expired = [key for key, row in self.visitors.items() if row["last_seen"] < cutoff]
        for key in expired:
            del self.visitors[key]
        retained = [row for row in self.failures if row["at"] >= cutoff]
        if expired or len(retained) != len(self.failures):
            self.failures.clear()
            self.failures.extend(retained)
            self.dirty = True

    @staticmethod
    def _valid_time(value):
        return type(value) in {int, float} and math.isfinite(value) and value >= 0

    def _validate_visitor(self, row):
        if not isinstance(row, dict) or not valid_id(row.get("id")):
            raise ValueError("invalid visitor")
        normalize_ip(row.get("ip"))
        for key in ("first_seen", "last_seen", "last_auth", "last_login"):
            if not self._valid_time(row.get(key)):
                raise ValueError("invalid visitor time")
        if (not isinstance(row.get("browser"), str) or len(row["browser"]) > 80
                or type(row.get("failures")) is not int or row["failures"] < 0
                or not isinstance(row.get("epoch"), str) or (row["epoch"] and not valid_id(row["epoch"]))):
            raise ValueError("invalid visitor state")
        _nickname(row.get("nickname", "guest"))
        if row.get("role", "") not in {"", "member", "guest"}:
            raise ValueError("invalid visitor role")

    def refresh(self):
        try:
            path = self.root / "access_settings.json"
            stat = path.stat()
            if stat.st_size > 1024 * 1024:
                raise ValueError("access settings too large")
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature != self.settings_signature or not self.available:
                data = validate_settings(read_json(path))
                with self.lock:
                    self.settings = data
                    self.available = True
                    self.dirty = True
                self.settings_signature = signature
                diagnostic(self.logger, "access_policy_loaded", revision=data["revision"],
                           configured=bool(data["verifier"] or data["guest_verifier"]), guest_configured=bool(data["guest_verifier"]))
            self.policy_failed = False
        except Exception as exc:
            with self.lock:
                self.available = False
            if not self.policy_failed:
                diagnostic(self.logger, "access_policy_read_failed", error_type=type(exc).__name__)
            self.policy_failed = True

    def _sign(self, label, payload):
        mac = hmac.new(bytes.fromhex(self.settings["key"]), (label + ":" + payload).encode("ascii"), hashlib.sha256).hexdigest()
        return payload + "." + mac

    def _verify(self, label, value):
        if not isinstance(value, str) or len(value) > 200:
            return ""
        payload, separator, mac = value.rpartition(".")
        if not separator or not re.fullmatch(r"[0-9a-f:]+", payload) or not re.fullmatch(r"[0-9a-f]{64}", mac):
            return ""
        expected = self._sign(label, payload).rpartition(".")[2]
        return payload if hmac.compare_digest(mac, expected) else ""

    def context(self, raw_cookie, ip, agent, secure=False, *, record_visit=True):
        """Authenticate every request from the socket IP; proxy headers are ignored."""
        with self.lock:
            result = dict(authenticated=False, configured=False, blocked=False,
                          available=self.available, visitor="", cookies=[], reauth_required=False)
            if not self.available:
                return result
            result["configured"] = bool(self.settings["verifier"] or self.settings["guest_verifier"])
            ip = normalize_ip(ip)
            if ip in self.settings["banned_ips"]:
                result["blocked"] = True
                return result
            cookies = SimpleCookie()
            try:
                if len(raw_cookie) <= 4096:
                    cookies.load(raw_cookie)
            except Exception:
                pass
            def value(name):
                return cookies[name].value if name in cookies else ""
            visitor = self._verify("visitor", value(VISITOR_COOKIE))
            new_visitor = not valid_id(visitor)
            result["new_visitor"] = new_visitor
            if new_visitor:
                if not record_visit:
                    return result
                visitor = secrets.token_hex(16)
                result["cookies"].append(cookie_header(VISITOR_COOKIE, self._sign("visitor", visitor), secure))
            result["visitor"] = visitor
            if visitor in self.settings["banned_visitors"]:
                result["blocked"] = True
                return result
            now = time.time()
            row = self.visitors.get(visitor)
            new_record = row is None and record_visit
            if new_record:
                if len(self.visitors) >= MAX_VISITORS:
                    oldest = min(self.visitors, key=lambda key: self.visitors[key]["last_seen"])
                    del self.visitors[oldest]
                row = dict(id=visitor, first_seen=now, last_seen=now, last_auth=0,
                           last_login=0, epoch="", failures=0, ip=ip, browser=_browser(agent), nickname="guest")
                self.visitors[visitor] = row
            if record_visit:
                row.update(last_seen=now, ip=ip, browser=_browser(agent))
            session = self._verify("session", value(SESSION_COOKIE))
            identity, separator, epoch = session.partition(":")
            role = _session_role(self.settings, epoch) if separator and identity == visitor else ""
            result["authenticated"] = bool(role) and not self.logout_wait(result)
            if result["authenticated"] and record_visit:
                row.update(last_auth=now, epoch=epoch, role=role)
                # Renew only daily; no Set-Cookie churn on 3-second polling.
                if now - row["last_login"] >= 86400:
                    row["last_login"] = now
                    result["cookies"].extend((cookie_header(VISITOR_COOKIE, self._sign("visitor", visitor), secure),
                                              cookie_header(SESSION_COOKIE, self._sign("session", session), secure)))
            elif not result["authenticated"] and value(SESSION_COOKIE):
                # A changed PIN must not leave a stale remembered login behind.
                result["reauth_required"] = True
                if record_visit:
                    row.update(last_auth=0, epoch="")
                result["cookies"].append(cookie_header(SESSION_COOKIE, "", secure, age=0))
            if new_record:
                diagnostic(self.logger, "visitor_connected", visitor=visitor[:8], ip=ip,
                           browser=_browser(agent), authenticated=result["authenticated"])
            if record_visit:
                self.dirty = True
            return result

    def read_wait(self, context, ip, *, path=None):
        ip = normalize_ip(ip)
        wait = self.read_limiter.wait(context["visitor"], ip)
        if not wait and path == '/':
            keys = [('reload', context['visitor'] or ip, bool(context['authenticated']))]
            if context.get('new_visitor'):
                keys.append(('reload-new-ip', ip))
            wait = self.cooldowns.claim(keys, 5)
        if wait:
            with self.lock:
                if time.monotonic() - self.last_read_throttle_notice >= 30:
                    diagnostic(self.logger, "web_page_reload_limited" if path == '/' else "web_read_rate_limited",
                               retry_after=wait)
                    self.last_read_throttle_notice = time.monotonic()
        return wait

    def logout_wait(self, context):
        return self.cooldowns.wait(('login', context.get('visitor', '')))

    def login(self, context, pin, ip, secure=False, *, nickname="guest"):
        ip = normalize_ip(ip)
        try:
            nickname = _nickname(nickname)
        except ValueError as exc:
            return 400, str(exc), []
        with self.lock:
            if not self.available or not (self.settings["verifier"] or self.settings["guest_verifier"]):
                return 503, "관리자가 프로그램에서 웹 비밀번호를 먼저 설정해야 합니다.", []
            if context["blocked"] or not context["visitor"]:
                return 403, "접속이 차단되었습니다.", []
            if self.logout_wait(context):
                return 429, "로그아웃 후 최소 2초가 지난 뒤 접속할 수 있습니다.", []
            if context["authenticated"]:
                return 200, "로그인되었습니다.", []
            now = time.time()
            recent = [row for row in self.failures if now - row["at"] < 300]
            while self.attempts and now - self.attempts[0] >= 60:
                self.attempts.popleft()
            if (len(recent) >= 30 or sum(row["ip"] == ip for row in recent) >= 5
                    or len(self.attempts) >= 30 or self.inflight >= 2):
                if time.monotonic() - self.last_throttle_notice >= 30:
                    diagnostic(self.logger, "login_rate_limited")
                    self.last_throttle_notice = time.monotonic()
                return 429, "로그인 시도가 많습니다. 잠시 후 다시 시도해주세요.", []
            self.attempts.append(now)
            self.inflight += 1
            settings = self.settings
        try:
            role, epoch = "", ""
            if isinstance(pin, str) and re.fullmatch(r"[0-9]{4}", pin) is not None:
                for candidate, prefix in (("member", ""), ("guest", "guest_")):
                    if not settings[prefix + "verifier"]:
                        continue
                    verifier = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"),
                        bytes.fromhex(settings[prefix + "salt"]), ITERATIONS).hex()
                    if hmac.compare_digest(verifier, settings[prefix + "verifier"]):
                        role, epoch = candidate, settings[prefix + "epoch"]
                        break
        finally:
            with self.lock:
                self.inflight -= 1
        with self.lock:
            visitor = context["visitor"]
            if self.logout_wait(context):
                return 429, "로그아웃 후 최소 2초가 지난 뒤 접속할 수 있습니다.", []
            if (not self.available or (role and _session_role(self.settings, epoch) != role)
                    or self.settings["key"] != settings["key"]
                    or visitor in self.settings["banned_visitors"] or ip in self.settings["banned_ips"]):
                return 403, "접속 설정이 변경되었습니다. 페이지를 다시 열어주세요.", []
            row = self.visitors.get(visitor)
            if row is None:
                return 503, "페이지를 다시 열어주세요.", []
            if not role:
                row["failures"] += 1
                self.failures.append(dict(at=time.time(), ip=ip, visitor=visitor))
                self.dirty = True
                diagnostic(self.logger, "login_failed", visitor=visitor[:8])
                return 401, "비밀번호가 맞지 않습니다.", []
            row.update(last_auth=time.time(), last_login=time.time(), epoch=epoch, nickname=nickname, role=role)
            self.cooldowns.clear([('reload', visitor, False), ('reload', visitor, True)])
            self.dirty = True
            diagnostic(self.logger, "login_succeeded", visitor=visitor[:8], role=role)
            return 200, "로그인되었습니다.", [cookie_header(
                SESSION_COOKIE, self._sign("session", visitor + ":" + epoch), secure)]

    def logout(self, context, secure=False):
        with self.lock:
            if context.get('visitor'):
                self.cooldowns.start(('login', context['visitor']), 2)
                self.cooldowns.clear([('reload', context['visitor'], False), ('reload', context['visitor'], True)])
            row = self.visitors.get(context["visitor"])
            if row:
                row.update(last_auth=0, epoch="")
                self.dirty = True
        return cookie_header(SESSION_COOKIE, "", secure, age=0)

    def summary(self):
        with self.lock:
            return dict(pin_configured=self.available and bool(self.settings["verifier"] or self.settings["guest_verifier"]),
                        access_available=self.available,
                        access_revision=self.settings["revision"] if self.available else 0)

    def flush(self, *, force=False):
        now = time.time()
        if not force and time.monotonic() < self.next_flush:
            return
        self.next_flush = time.monotonic() + 5
        with self.lock:
            self._prune_history(now)
            save_history = self.dirty or force
            rows = deepcopy(list(self.visitors.values()))
            failures = deepcopy(list(self.failures))
            self.dirty = False
            policy = self.settings if self.available else None
            public = []
            for row in rows:
                if row["last_seen"] < now - ACCESS_DISPLAY_DAYS * 86400:
                    continue
                blocked = policy and (row["id"] in policy["banned_visitors"] or row["ip"] in policy["banned_ips"])
                role = _session_role(policy, row["epoch"]) if policy else ""
                online = not blocked and bool(role) and now - row["last_auth"] < 60
                public.append(dict(id=row["id"], ip=row["ip"], browser=row["browser"],
                                   nickname=row.get("nickname", "guest") if row["last_login"] else "미인증 방문", role=row.get("role", role),
                                   last_seen=row["last_seen"], failures=row["failures"],
                                   state="차단됨" if blocked else "조회 중" if online else
                                         "접속 중 · 로그인 필요" if now - row["last_seen"] < 60 else "최근 접속"))
        try:
            if save_history:
                write_json(self.root / "visitors.json", dict(schema=1, visitors=rows, failures=failures))
            write_json(access_state_path(self.root, self.run_id), dict(protocol=PROTOCOL, run_id=self.run_id,
                       updated_at=timestamp(), visitors=public,
                       failures=[row for row in failures if row["at"] >= now - ACCESS_DISPLAY_DAYS * 86400][-30:],
                       **self.summary()))
            self.flush_failed = False
        except Exception as exc:
            with self.lock:
                self.dirty = True
            if not self.flush_failed:
                diagnostic(self.logger, "visitor_history_write_failed", error_type=type(exc).__name__)
            self.flush_failed = True

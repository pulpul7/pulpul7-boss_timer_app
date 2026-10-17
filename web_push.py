"""Optional mobile Web Push. No GUI, voice or Discord dependencies."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
from urllib.parse import urlsplit

from web_service_common import diagnostic, read_json, write_json

MAX_STATE = 300000
MAX_SUBSCRIBERS = 100
NOTICE_LEAD = 300
GROUP_SECONDS = 121  # UI calls this a two-minute gap; include the extra second.
DEFAULT_NOTICE_TIMES = [2, None, None]


def validate_notice_times(value):
    if (not isinstance(value, list) or len(value) != 3
            or any(v is not None and (type(v) is not int or not 0 <= v <= 10) for v in value)):
        raise ValueError("알림 시각은 사용안함·10~1분 전·젠 중 세 칸으로 설정해주세요.")
    active = [v for v in value if v is not None]
    if not active or len(active) != len(set(active)):
        raise ValueError("알림은 서로 다른 시각으로 최소 한 번, 최대 세 번 선택해주세요.")
    return list(value)
MANIFEST = json.dumps(dict(name="보스 스케줄", short_name="보스 스케줄", id="/", start_url="/",
                          scope="/", display="standalone", background_color="#151b24",
                          theme_color="#151b24"), ensure_ascii=False).encode("utf-8")
SERVICE_WORKER = r"""'use strict';
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('push',event=>{
  let value={};try {value=event.data?.json() || {};} catch(_) {}
  const title=typeof value.title==='string'?value.title.slice(0,120):'보스 스케줄';
  const body=typeof value.body==='string'?value.body.slice(0,600):'새 안내가 있습니다. 스케줄을 확인해주세요.';
  event.waitUntil(self.registration.showNotification(title,{body,
    tag:typeof value.tag==='string'?value.tag.slice(0,80):'boss-schedule',renotify:false,data:{url:'/'}}));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  event.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(async clients=>{
    const client=clients.find(client=>new URL(client.url).origin===self.location.origin);
    if(client) return client.focus();
    return self.clients.openWindow('/');
  }));
});
""".encode("utf-8")


def push_asset(path):
    if path == "/nickname-defender.png":
        from web_nickname_badge import BADGE_PNG
        return BADGE_PNG, "image/png"
    if path == "/manifest.webmanifest":
        return MANIFEST, "application/manifest+json; charset=utf-8"
    if path == "/push-worker.js":
        return SERVICE_WORKER, "text/javascript; charset=utf-8"
    return None


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}={0,2}", value):
        raise ValueError("잘못된 푸시 키입니다.")
    value = value.rstrip("=")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_subscription(value):
    if not isinstance(value, dict):
        raise ValueError("푸시 신청 정보를 확인해주세요.")
    endpoint = value.get("endpoint")
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise ValueError("잘못된 푸시 주소입니다.")
    url = urlsplit(endpoint)
    host = url.hostname or ""
    allowed = (host == "fcm.googleapis.com" or host == "updates.push.services.mozilla.com"
               or host.endswith(".push.apple.com"))
    if (not allowed or url.scheme != "https" or url.username or url.password
            or url.port not in {None, 443} or url.fragment or not url.path.startswith("/")
            or any(ord(c) < 33 for c in endpoint)):
        raise ValueError("지원되는 휴대폰 푸시 주소가 아닙니다.")
    keys = value.get("keys")
    if not isinstance(keys, dict):
        raise ValueError("푸시 키가 없습니다.")
    point, auth = _unb64(keys.get("p256dh")), _unb64(keys.get("auth"))
    from cryptography.hazmat.primitives.asymmetric import ec
    if len(point) != 65 or len(auth) != 16:
        raise ValueError("잘못된 푸시 키 길이입니다.")
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    return dict(endpoint=endpoint, keys=dict(p256dh=_b64(point), auth=_b64(auth)))


def validate_preferences(value):
    if not isinstance(value, dict) or type(value.get("defaultOn")) is not bool:
        raise ValueError("알람 선택을 확인해주세요.")
    overrides = value.get("overrides")
    if not isinstance(overrides, dict) or len(overrides) > 2000:
        raise ValueError("알람 선택이 너무 많습니다.")
    if any(not isinstance(k, str) or len(k) > 240 or type(v) is not bool for k, v in overrides.items()):
        raise ValueError("잘못된 알람 선택입니다.")
    return dict(defaultOn=value["defaultOn"], overrides=dict(overrides),
                noticeTimes=validate_notice_times(value.get("noticeTimes", DEFAULT_NOTICE_TIMES)))


def validate_push_state(data):
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("subscribers"), dict):
        raise ValueError("invalid push state")
    if len(data["subscribers"]) > MAX_SUBSCRIBERS or len(json.dumps(data).encode()) > MAX_STATE:
        raise ValueError("푸시 신청이 많습니다. 관리자에게 문의해주세요.")
    result = dict(schema=1, subscribers={})
    for visitor, row in data["subscribers"].items():
        if not re.fullmatch(r"[0-9a-f]{32}", visitor) or not isinstance(row, dict):
            raise ValueError("invalid push visitor")
        if (not re.fullmatch(r"[0-9a-f]{32}", row.get("epoch", ""))
                or row.get("role") not in {"member", "guest"} or not isinstance(row.get("ip"), str)):
            raise ValueError("invalid push authentication")
        import ipaddress
        ipaddress.ip_address(row["ip"])
        scope = row.get("scope")
        if not isinstance(scope, list) or len(scope) != 3 or any(not isinstance(s, str) or len(s) > 200 for s in scope):
            raise ValueError("invalid push scope")
        device = row.get('device', '')
        if not isinstance(device,str) or (device and not re.fullmatch(r'[a-f0-9]{32}',device)):
            raise ValueError('invalid push device')
        result["subscribers"][visitor] = dict(subscription=validate_subscription(row.get("subscription")),
            prefs=validate_preferences(row.get("prefs")), epoch=row["epoch"], role=row["role"], ip=row["ip"], scope=scope)
        if device:
            result['subscribers'][visitor]['device'] = device
    return result


def _storage_key(access):
    return hmac.new(bytes.fromhex(access["key"]), b"BossTimer.webpush.storage.v1", hashlib.sha256).digest()


def read_push_state(root, access):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    path = root / "push_subscriptions.json"
    if not path.exists():
        return dict(schema=1, subscribers={})
    if path.stat().st_size > MAX_STATE * 2:
        raise ValueError("push state too large")
    envelope = read_json(path)
    if envelope.get("key_id") and envelope["key_id"] != hashlib.sha256(_storage_key(access)).hexdigest()[:24]:
        return dict(schema=1, subscribers={})
    raw = base64.b64decode(envelope["encrypted"], validate=True)
    return validate_push_state(json.loads(AESGCM(_storage_key(access)).decrypt(raw[:12], raw[12:], b"BossTimer.push.v1")))


def write_push_state(root, access, data):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    data = validate_push_state(data)
    nonce = os.urandom(12)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > MAX_STATE:
        raise ValueError("푸시 신청 정보가 너무 많습니다.")
    encrypted = nonce + AESGCM(_storage_key(access)).encrypt(nonce, raw, b"BossTimer.push.v1")
    write_json(root / "push_subscriptions.json", dict(schema=1, key_id=hashlib.sha256(_storage_key(access)).hexdigest()[:24],
                                                     encrypted=base64.b64encode(encrypted).decode()))


def vapid_keys(access):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    # The existing encrypted web handover preserves access.key, so no new key input is needed.
    order = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
    seed = hmac.new(bytes.fromhex(access["key"]), b"BossTimer.webpush.vapid.v1", hashlib.sha256).digest()
    private = ec.derive_private_key(int.from_bytes(seed, "big") % (order - 1) + 1, ec.SECP256R1())
    public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    der = private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return _b64(public), _b64(der)


def push_groups(events, prefs):
    """Adjacent enabled bosses up to 121 seconds apart form a transitive chain."""
    eligible = []
    for row in events:
        if row["kind"] not in {"boss", "fixed", "invasion"}:
            continue
        when = datetime.fromisoformat(row["scheduled_at"])
        key = row["kind"] + ":" + row["name"]
        dated = "day:" + when.date().isoformat() + ":" + key
        if prefs["overrides"].get(dated, prefs["overrides"].get(key, prefs["defaultOn"])):
            eligible.append((when.timestamp(), row))
    eligible.sort(key=lambda item: (item[0], item[1]["name"]))
    groups = []
    previous = None
    for when, row in eligible:
        if previous is None or when - previous > GROUP_SECONDS:
            groups.append((when, [row]))
        else:
            groups[-1][1].append(row)
        previous = when
    return groups


def notice_candidates(events, prefs, now, *, groups=None):
    times = validate_notice_times(prefs.get('noticeTimes', DEFAULT_NOTICE_TIMES))
    for when, group in push_groups(events, prefs) if groups is None else groups:
        for minutes in sorted((v for v in times if v is not None), reverse=True):
            lead = minutes * 60
            if when - lead <= now < when - lead + 30:
                yield when, group, lead


class PushRuntime:
    def __init__(self, root, run_id, schedule, access, logger, *, sender=None):
        self.root, self.run_id, self.schedule, self.access, self.logger = root, run_id, schedule, access, logger
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.thread = None
        self.pool = None
        self.pending = set()
        self.retry_after = {}
        self.test_times = {}
        self.state = dict(schema=1, subscribers={})
        self.signature = None
        self.failed = False
        self.sent_signature = None
        self.sender = sender
        self.group_cache = {}
        self.group_snapshot = None
        if sender is None:
            try:
                from pywebpush import webpush
                self.sender = webpush
            except ImportError:
                diagnostic(logger, "push_module_missing")
        try:
            sent = read_json(root / "push_sent.json").get("sent", {})
            self.sent = {k: v for k, v in sent.items() if isinstance(k, str) and len(k) < 100
                         and type(v) in {int, float} and math.isfinite(v) and v > time.time() - 3600}
        except (OSError, ValueError, AttributeError):
            self.sent = {}

    def start(self):
        if self.sender is not None:
            self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="web-push-send")
            self.thread = threading.Thread(target=self._loop, name="web-push-schedule", daemon=True)
            self.thread.start()

    def refresh(self):
        with self.access.lock:
            policy = deepcopy(self.access.settings) if self.access.available else None
        if policy is None:
            return None
        path = self.root / "push_subscriptions.json"
        signature = (path.stat().st_mtime_ns, path.stat().st_size, policy["key"]) if path.exists() else (None, policy["key"])
        with self.lock:
            if signature != self.signature:
                self.state = read_push_state(self.root, policy)
                self.signature = signature
        return policy

    def config(self, context):
        try:
            policy = self.refresh()
            if policy is None:
                raise ValueError("웹 비밀번호 설정을 확인해주세요.")
            public, _ = vapid_keys(policy)
            with self.lock:
                row = self.state["subscribers"].get(context["visitor"])
            return dict(ready=self.sender is not None, public_key=public, subscribed=bool(row),
                        message="" if self.sender is not None else "관리자가 푸시 모듈을 포함한 새 GUI를 설치해야 합니다.")
        except Exception as exc:
            diagnostic(self.logger, "push_settings_failed", error_type=type(exc).__name__)
            return dict(ready=False, message="푸시 설정을 확인하지 못했습니다. 스케줄은 계속 이용할 수 있습니다.")

    def registered(self, context):
        with self.lock:
            return context["visitor"] in self.state["subscribers"]

    def test(self, context):
        if not context.get("authenticated"):
            raise ValueError("다시 로그인해주세요.")
        policy = self.refresh()
        with self.lock:
            visitor = context["visitor"]
            row = deepcopy(self.state["subscribers"].get(visitor))
            if row is None:
                return dict(ok=False, renew=True, message="푸시 신청을 다시 등록해야 합니다.")
            now = time.monotonic()
            if now < self.test_times.get(visitor, 0):
                return dict(ok=False, message="테스트 푸시는 30초 간격으로 사용할 수 있습니다.")
            if visitor in self.pending:
                return dict(ok=False, message="푸시를 보내는 중입니다. 잠시 뒤 시도해주세요.")
            self.test_times = {k: v for k, v in self.test_times.items() if v > now}
            self.test_times[visitor] = now + 30
            self.pending.add(visitor)
        if policy is None or self.sender is None:
            with self.lock:
                self.pending.discard(visitor)
            return dict(ok=False, message="푸시 모듈을 확인해주세요.")
        message = dict(title="보스 스케줄 · 테스트", body="푸시 연결 확인용 알림입니다.",
                       tag=hashlib.sha256(f"test:{visitor}:{time.time()}".encode()).hexdigest()[:32],
                       expires_at=time.time() + 30)
        return self._send(visitor, row, policy, message, test=True)

    def update(self, context, payload):
        if type(payload.get("enabled")) is not bool:
            raise ValueError("푸시 ON/OFF 값을 확인해주세요.")
        policy = self.refresh()
        if policy is None or not context.get("authenticated"):
            raise ValueError("다시 로그인해주세요.")
        visitor = context["visitor"]
        with self.access.lock:
            client = dict(self.access.visitors.get(visitor, {}))
        with self.lock:
            data = deepcopy(self.state)
            if payload.get("enabled") is False:
                data["subscribers"].pop(visitor, None)
            else:
                if self.sender is None:
                    raise ValueError("관리자가 푸시 모듈을 포함한 새 GUI를 설치해야 합니다.")
                if not client.get("epoch") or client.get("role") not in {"member", "guest"}:
                    raise ValueError("다시 로그인해주세요.")
                scope = payload.get("scope")
                subscription = validate_subscription(payload.get('subscription'))
                device = payload.get('device', '')
                if not isinstance(device,str) or (device and not re.fullmatch(r'[a-f0-9]{32}',device)):
                    raise ValueError('푸시 기기 정보를 확인해주세요.')
                # Cookies identify a login; the browser installation and push
                # endpoint identify delivery. A new login must not leave its
                # previous ON registration sending alongside the new OFF one.
                for old, record in list(data['subscribers'].items()):
                    if (old != visitor and (record['subscription']['endpoint'] == subscription['endpoint']
                            or device and record.get('device') == device)):
                        del data['subscribers'][old]
                data["subscribers"][visitor] = dict(subscription=subscription,
                    prefs=validate_preferences(payload.get("prefs")), scope=scope,
                    epoch=client["epoch"], role=client["role"], ip=client["ip"])
                if device:
                    data['subscribers'][visitor]['device'] = device
            validate_push_state(data)
            if data != self.state:
                write_push_state(self.root, policy, data)
                self.state = data
                self.signature = None
                prefs = data['subscribers'].get(visitor, {}).get('prefs', {})
                diagnostic(self.logger, "push_subscription_changed", enabled=payload.get("enabled") is not False,
                           visitor=visitor[:8], subscriber_count=len(data["subscribers"]),
                           alarm_default_on=prefs.get('defaultOn'),
                           alarm_overrides_on=sum(value is True for value in prefs.get('overrides', {}).values()),
                           alarm_overrides_off=sum(value is False for value in prefs.get('overrides', {}).values()))

    def disable(self, context):
        if context.get("authenticated"):
            self.update(context, dict(enabled=False))

    def _allowed(self, visitor, row, policy):
        prefix = "guest_" if row["role"] == "guest" else ""
        if (not policy.get(prefix + "verifier") or row["epoch"] != policy[prefix + "epoch"]
                or visitor in policy["banned_visitors"] or row["ip"] in policy["banned_ips"]):
            return False
        with self.access.lock:
            latest = self.access.visitors.get(visitor)
            if latest is not None and (latest.get("epoch") != row["epoch"]
                                       or latest.get("ip") in policy["banned_ips"]):
                return False
        return True

    def tick(self, now=None):
        from web_authority_gate import external_allowed
        if self.closed.is_set() or self.sender is None or not external_allowed(self.root, self.run_id):
            return
        policy = self.refresh()
        if policy is None:
            return
        with self.schedule.lock:
            snapshot = self.schedule.data
            if snapshot is None or self.schedule._stale(datetime.now(timezone.utc)):
                return
        now = time.time() if now is None else now
        sent_path = self.root / "push_sent.json"
        if sent_path.exists():
            info = sent_path.stat()
            signature = (info.st_mtime_ns, info.st_size)
            if signature != self.sent_signature and info.st_size <= 600000:
                saved = read_json(sent_path).get("sent", {})
                if isinstance(saved, dict) and len(saved) <= 5000:
                    with self.lock:
                        self.sent.update({k: v for k, v in saved.items() if isinstance(k, str) and len(k) == 64
                                          and type(v) in {int, float} and math.isfinite(v) and v > now - 3600})
                    self.sent_signature = signature
        events = snapshot["events"]
        if not events:
            return
        scope = [snapshot["server_name"], snapshot["guild_name"], snapshot["season"]]
        with self.lock:
            clients = list(self.state["subscribers"].items())
        marker = (id(snapshot), snapshot.get('schedule_version'), len(events))
        if marker != self.group_snapshot:
            self.group_cache.clear()
            self.group_snapshot = marker
        active_visitors = {visitor for visitor, _ in clients}
        self.group_cache = {v: plan for v, plan in self.group_cache.items() if v in active_visitors}
        for visitor, row in clients:
            if row["scope"] != scope or not self._allowed(visitor, row, policy):
                continue
            if visitor in self.pending:
                continue
            if time.monotonic() < self.retry_after.get(visitor, 0):
                continue
            cached = self.group_cache.get(visitor)
            if cached is None or cached[0] is not row['prefs']:
                cached = (row['prefs'], push_groups(events, row['prefs']))
                self.group_cache[visitor] = cached
            for when, group, lead in notice_candidates(events, row["prefs"], now, groups=cached[1]):
                def event_key(event):
                    # Alias edits, server restarts and a host change must not re-send the same occurrence.
                    from web_schedule_snapshot import _catalog_id
                    identity = event.get("catalog_id") or event["name"]
                    if identity in {_catalog_id("boss", "길던"), _catalog_id("boss", "사용자이벤트")}:
                        identity += "|" + re.sub(r"\s", "", event["name"])
                    # Keep the original five-minute keys so an upgrade cannot repeat that notice.
                    value = [scope, visitor, identity, event["scheduled_at"]]
                    if lead != NOTICE_LEAD:
                        value.append(lead)
                    value = json.dumps(value, ensure_ascii=False)
                    return hashlib.sha256(value.encode()).hexdigest()
                fresh = [event for event in group if event_key(event) not in self.sent]
                if not fresh:
                    continue
                from boss_view_labels import row_label
                names = list(dict.fromkeys(row_label(event) for event in fresh))
                tag = hashlib.sha256("|".join(event_key(e) for e in fresh).encode()).hexdigest()[:32]
                body = " + ".join(names)
                if len(body) > 600:
                    shown = []
                    for name in names:
                        if len(" + ".join(shown + [name])) > 550:
                            break
                        shown.append(name)
                    body = " + ".join(shown) + f" 외 {len(names) - len(shown)}개"
                label = "젠" if lead == 0 else f"{lead // 60}분 전"
                message = dict(title=label + " · " + " ".join(scope[:2]), body=body, tag=tag,
                               expires_at=when - lead + 30, lead_seconds=lead)
                with self.lock:
                    self.pending.add(visitor)
                occurrence_keys = [event_key(event) for event in fresh]
                if self.pool:
                    try:
                        self.pool.submit(self._send, visitor, deepcopy(row), policy, message, occurrence_keys)
                    except Exception:
                        with self.lock:
                            self.pending.discard(visitor)
                        raise
                else:
                    self._send(visitor, deepcopy(row), policy, message, occurrence_keys)
                break

    def _send(self, visitor, row, policy, message, occurrence_keys=(), *, test=False):
        try:
            from web_authority_gate import external_allowed
            # Check again after waiting for a sender slot; a standby PC never sends.
            if self.closed.is_set() or time.time() >= message["expires_at"] or not external_allowed(self.root, self.run_id):
                return dict(ok=False, message="웹 공개 상태와 담당자 권한을 확인해주세요.")
            current = self.refresh()
            with self.lock:
                valid = self.state["subscribers"].get(visitor) == row
            if not valid or current is None or not self._allowed(visitor, row, current) or current["key"] != policy["key"]:
                return dict(ok=False, message="푸시 설정이나 로그인 상태가 바뀌었습니다. 다시 켜주세요.")
            _, private = vapid_keys(policy)
            # VAPID's subject is contact information, not the push service audience.
            domain = read_json(self.root / "ddns_settings.json")["domain"]
            from web_certificate_store import hostname
            self.sender(subscription_info=row["subscription"], data=json.dumps(message, ensure_ascii=False),
                vapid_private_key=private, vapid_claims={"sub": "https://" + hostname(domain)},
                ttl=60, timeout=4, headers={"Urgency": "normal", "Topic": message["tag"]})
            # Only provider acceptance is a completed send; failed attempts remain retryable.
            with self.lock:
                self.retry_after.pop(visitor, None)
                if occurrence_keys:
                    completed = time.time()
                    self.sent = {k: v for k, v in self.sent.items() if v > completed - 3600}
                    self.sent.update({k: completed for k in occurrence_keys})
                    if len(self.sent) > 5000:
                        self.sent = dict(sorted(self.sent.items(), key=lambda p: p[1])[-5000:])
                    try:
                        write_json(self.root / "push_sent.json", dict(schema=1, sent=self.sent))
                    except OSError as exc:
                        diagnostic(self.logger, "push_sent_save_failed", error_type=type(exc).__name__)
            diagnostic(self.logger, "push_test_sent" if test else "push_notice_sent", visitor=visitor[:8],
                       lead_seconds=0 if test else message.get('lead_seconds', NOTICE_LEAD))
            return dict(ok=True, message="푸시 서비스에 전달했습니다. 휴대폰 알림을 확인해주세요.")
        except Exception as exc:
            response = getattr(exc, "response", None)
            code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
            diagnostic(self.logger, "push_test_failed" if test else "push_notice_failed", visitor=visitor[:8], error_type=type(exc).__name__, status=code)
            with self.lock:
                self.retry_after[visitor] = time.monotonic() + 10
            if code in {404, 410}:
                # Keep the current policy when the request finished during a key/host change.
                current = self.refresh()
                with self.lock:
                    if current and current["key"] == policy["key"] and self.state["subscribers"].get(visitor) == row:
                        updated = deepcopy(self.state)
                        updated["subscribers"].pop(visitor, None)
                        write_push_state(self.root, policy, updated)
                        self.state = updated
                        self.signature = None
                return dict(ok=False, renew=True, message="푸시 등록이 만료됐습니다. 새 등록이 필요합니다.")
            return dict(ok=False, message="푸시 서비스 전달에 실패했습니다. 관리자 진단 로그를 확인해주세요.")
        finally:
            with self.lock:
                self.pending.discard(visitor)

    def _loop(self):
        while not self.closed.wait(1):
            try:
                self.tick()
                self.failed = False
            except Exception as exc:
                if not self.failed:
                    diagnostic(self.logger, "push_runtime_failed", error_type=type(exc).__name__)
                self.failed = True

    def close(self):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=1.2)
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)

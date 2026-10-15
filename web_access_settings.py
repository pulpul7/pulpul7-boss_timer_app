"""Private access policy. Only the desktop coordinator may write this file."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import ipaddress
import re
import secrets

from web_service_common import read_json, timestamp, write_json
from web_secret_store import SecretStorageError, protect_secret, unprotect_secret

ITERATIONS = 600000
COOKIE_AGE = 365 * 86400


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def validate_pin(pin):
    if not isinstance(pin, str) or re.fullmatch(r"[0-9]{4}", pin) is None:
        raise ValueError("비밀번호는 숫자 네 자리로 입력해주세요.")


def normalize_ip(value):
    return str(ipaddress.ip_address(value))


def pin_matches(data, prefix, pin):
    validate_pin(pin)
    return bool(data[prefix + "verifier"]) and hmac.compare_digest(
        hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"),
                            bytes.fromhex(data[prefix + "salt"]), ITERATIONS).hex(),
        data[prefix + "verifier"])


def readable_pins(data):
    """Private desktop/transfer use only. Never put these values in public status."""
    result = {}
    for role, prefix in (("member", ""), ("guest", "guest_")):
        if not data[prefix + "verifier"]:
            result[role] = dict(value="", state="unset")
            continue
        protected = data.get(prefix + "protected_pin", "")
        if not protected:
            result[role] = dict(value="", state="legacy")
            continue
        try:
            pin = unprotect_secret(protected)
            if not pin_matches(data, prefix, pin):
                raise ValueError("stored PIN does not match policy")
            result[role] = dict(value=pin, state="ready")
        except (SecretStorageError, OSError, ValueError, UnicodeError):
            # A failed display must never replace the policy or expire sessions.
            result[role] = dict(value="", state="unavailable")
    return result


def export_access(data):
    """Return policy and readable PINs for the existing AES-encrypted envelope."""
    data = validate_settings(data)
    pins = {role: item["value"] for role, item in readable_pins(data).items() if item["state"] == "ready"}
    for prefix in ("", "guest_"):
        data.pop(prefix + "protected_pin", None)
    return data, pins


def import_access(data, pins=None):
    """Re-protect display values for this Windows user; preserve all session keys."""
    data = validate_settings(data)
    if pins is None:
        pins = {}  # Hash-only exports from previous versions still work.
    if not isinstance(pins, dict) or set(pins) - {"member", "guest"}:
        raise ValueError("공동 웹 비밀번호 표시 정보를 확인하지 못했습니다.")
    for role, prefix in (("member", ""), ("guest", "guest_")):
        data.pop(prefix + "protected_pin", None)
        if role not in pins:
            continue
        if not pin_matches(data, prefix, pins[role]):
            raise ValueError("공동 웹 비밀번호와 접속 설정이 일치하지 않습니다.")
        try:
            data[prefix + "protected_pin"] = protect_secret(pins[role])
        except (SecretStorageError, OSError):
            pass  # Display unavailable; authentication and handover remain usable.
    return data


def validate_settings(data):
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("invalid access settings")
    for key, length in (("key", 64), ("epoch", 32)):
        if not isinstance(data.get(key), str) or not re.fullmatch(r"[0-9a-f]{%d}" % length, data[key]):
            raise ValueError("invalid access key")
    if data.get("iterations") != ITERATIONS or type(data.get("revision")) is not int or data["revision"] < 1:
        raise ValueError("invalid access revision")
    data = deepcopy(data)
    # Old policies remain usable without changing the member password or sessions.
    data.setdefault("guest_salt", "")
    data.setdefault("guest_verifier", "")
    data.setdefault("guest_epoch", hashlib.sha256((data["key"] + ":guest").encode("ascii")).hexdigest()[:32])
    if not valid_id(data["guest_epoch"]) or data["guest_epoch"] == data["epoch"]:
        raise ValueError("invalid guest epoch")
    for prefix in ("", "guest_"):
        salt, verifier = data.get(prefix + "salt"), data.get(prefix + "verifier")
        if not isinstance(salt, str) or not isinstance(verifier, str):
            raise ValueError("invalid access verifier")
        if bool(salt) != bool(verifier) or (verifier and (
                not re.fullmatch(r"[0-9a-f]{32}", salt) or not re.fullmatch(r"[0-9a-f]{64}", verifier))):
            raise ValueError("invalid access verifier")
        protected = data.get(prefix + "protected_pin", "")
        if not isinstance(protected, str) or len(protected) > 16384:
            raise ValueError("invalid protected PIN")
    for kind in ("visitors", "ips"):
        entries = data.get("banned_" + kind)
        if not isinstance(entries, dict) or len(entries) > 2000:
            raise ValueError("invalid access bans")
        for identity, value in entries.items():
            if (kind == "visitors" and not valid_id(identity)) or (kind == "ips" and normalize_ip(identity) != identity):
                raise ValueError("invalid ban identity")
            if not isinstance(value, str) or len(value) > 64:
                raise ValueError("invalid ban time")
    return deepcopy(data)


class AccessSettings:
    def __init__(self, root):
        self.path = root / "access_settings.json"
        self.error = None
        self.saved = False
        self.data = validate_settings(dict(schema=1, revision=1, key=secrets.token_hex(32), epoch=secrets.token_hex(16),
                         iterations=ITERATIONS, salt="", verifier="", banned_visitors={}, banned_ips={}))
        try:
            self.data = validate_settings(read_json(self.path))
            self.saved = True
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            # Never silently replace an unreadable key, password or ban list.
            self.error = exc

    def ensure_saved(self):
        if self.error is not None:
            raise ValueError("웹 접속 설정을 읽지 못했습니다. 진단 로그를 확인해주세요.")
        if not self.saved:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json(self.path, self.data)
            self.saved = True

    def require_member_pin(self):
        if self.error is not None:
            raise ValueError("웹 접속 설정을 읽지 못했습니다. 비밀번호 탭과 진단 로그를 확인해주세요.")
        if not self.data["verifier"]:
            raise ValueError("비밀번호 탭에서 회원용 웹 비밀번호를 먼저 저장해주세요. 공동 관리자는 기존 웹 비밀번호를 인계받아 사용합니다.")

    def change(self, operation, value=""):
        self.ensure_saved()
        data = deepcopy(self.data)
        if operation in {"pin", "guest_pin"}:
            validate_pin(value)
            prefix = "guest_" if operation == "guest_pin" else ""
            other = "" if prefix else "guest_"
            if pin_matches(data, other, value):
                raise ValueError("일반·게스트 비밀번호는 서로 다르게 설정해주세요.")
            same = pin_matches(data, prefix, value)
            data[prefix + "protected_pin"] = protect_secret(value)
            if same:
                message = "같은 비밀번호를 저장했습니다. 기존 로그인은 유지됩니다."
            else:
                salt = secrets.token_bytes(16)
                data[prefix + "salt"] = salt.hex()
                data[prefix + "verifier"] = hashlib.pbkdf2_hmac(
                    "sha256", value.encode("ascii"), salt, ITERATIONS).hex()
                data[prefix + "epoch"] = secrets.token_hex(16)
                message = ("게스트 비밀번호를 저장했습니다. 기존 게스트는 다시 로그인해야 합니다." if prefix else
                           "일반 비밀번호를 저장했습니다. 일반 접속자는 다시 로그인해야 합니다.")
        elif operation == "guest_disable":
            data.update(guest_salt="", guest_verifier="", guest_epoch=secrets.token_hex(16))
            data.pop("guest_protected_pin", None)
            message = "게스트 입장을 중지하고 기존 게스트 로그인을 해제했습니다."
        elif operation == "logout_all":
            data["epoch"] = secrets.token_hex(16)
            data["guest_epoch"] = secrets.token_hex(16)
            message = "모든 브라우저의 로그인을 해제했습니다."
        elif operation in {"ban_visitor", "unban_visitor"}:
            if not valid_id(value):
                raise ValueError("접속자를 다시 선택해주세요.")
            if operation == "ban_visitor":
                data["banned_visitors"][value] = timestamp()
            else:
                data["banned_visitors"].pop(value, None)
            message = "브라우저 차단을 적용했습니다." if operation == "ban_visitor" else "브라우저 차단을 해제했습니다."
        elif operation in {"ban_ip", "unban_ip"}:
            value = normalize_ip(value)
            if operation == "ban_ip":
                data["banned_ips"][value] = timestamp()
            else:
                data["banned_ips"].pop(value, None)
            message = "IP 차단을 적용했습니다." if operation == "ban_ip" else "IP 차단을 해제했습니다."
        else:
            raise ValueError("지원하지 않는 접속 관리 작업입니다.")
        data["revision"] += 1
        validate_settings(data)
        write_json(self.path, data)
        self.data = data
        return message

    def public_view(self):
        return dict(configured=bool(self.data["verifier"] or self.data["guest_verifier"]) and self.error is None,
                    member_configured=bool(self.data["verifier"]) and self.error is None,
                    guest_configured=bool(self.data["guest_verifier"]) and self.error is None,
                    settings_error=self.error is not None, revision=self.data["revision"],
                    bans=[dict(kind=kind, value=value, at=at) for kind, values in (
                        ("visitor", self.data["banned_visitors"]), ("ip", self.data["banned_ips"]))
                        for value, at in values.items()])

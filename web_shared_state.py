"""Token-encrypted guild web state. No plaintext credentials enter GitHub."""
import base64
import hashlib
import json
import os
from contextlib import ExitStack

from web_access_settings import export_access, import_access, valid_id
from web_certificate_store import hostname, load_certificate, publish_certificate, public_certificate
from web_secret_store import unprotect_secret
from web_service_common import read_json, write_json

LIMIT = 512000


def encrypt_state(data, token, guild, domain):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    context = f"BossTimer.web.v1:{guild}:{hostname(domain)}".encode()
    nonce = os.urandom(12)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > LIMIT:
        raise ValueError("웹 공동 설정이 너무 큽니다.")
    key = hashlib.sha256(context + b":" + token.encode()).digest()
    return dict(schema=1, domain=hostname(domain), guild=str(guild),
                encrypted=base64.b64encode(nonce + AESGCM(key).encrypt(nonce, raw, context)).decode())


def decrypt_state(data, token, guild, domain):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if (not isinstance(data, dict) or data.get("schema") != 1 or data.get("guild") != str(guild)
            or data.get("domain") != hostname(domain) or not isinstance(data.get("encrypted"), str)
            or len(data["encrypted"]) > LIMIT * 2):
        raise ValueError("공동 웹 주소·Discord 서버가 일치하지 않습니다.")
    context = f"BossTimer.web.v1:{guild}:{hostname(domain)}".encode()
    raw = base64.b64decode(data["encrypted"], validate=True)
    key = hashlib.sha256(context + b":" + token.encode()).digest()
    try:
        value = json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], context))
    except InvalidTag as exc:
        raise ValueError("공동 웹 설정을 확인하지 못했습니다. 두 PC의 DuckDNS 주소와 Token이 같은지 확인해주세요.") from exc
    if not isinstance(value, dict):
        raise ValueError("잘못된 공동 웹 설정입니다.")
    return value


def export_state(manager, stamp):
    from cryptography.hazmat.primitives import serialization
    root = manager.root
    settings = read_json(root / "ddns_settings.json")
    domain = hostname(settings["domain"])
    info, chain, key_path, password = load_certificate(root)
    if info["domain"] != domain:
        raise ValueError("공동 웹 주소와 인증서가 다릅니다.")
    key = serialization.load_pem_private_key(key_path.read_bytes(), password.encode())
    access, pins = export_access(read_json(root / "access_settings.json"))
    if not (access["verifier"] or access["guest_verifier"]):
        raise ValueError("웹 비밀번호를 먼저 설정해주세요.")
    try:
        txt_owner = read_json(root / "https" / "txt_owner.json")
    except FileNotFoundError:
        txt_owner = {}
    port = manager.settings.get("external_port") or manager.public.status().get("external_port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("공동 웹 공개 포트가 준비되지 않았습니다.")
    data = dict(owner=list(stamp), access=access, pins=pins, chain=chain.read_text(encoding="ascii"),
                mapping_owner=manager.public.settings["owner"],
                terms_agreed_at=manager.https.settings.get("terms_agreed_at", ""),
                key=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()).decode("ascii"),
                txt_owner=txt_owner, external_port=port,
                public_enabled=manager.public.status()["enabled"])
    return encrypt_state(data, unprotect_secret(settings["protected_token"]), stamp[0], domain)


def import_state(manager, envelope, stamp, guard):
    from cryptography.hazmat.primitives import serialization
    from web_certificate_store import inspect_certificate
    root = manager.root
    settings = read_json(root / "ddns_settings.json")
    domain = hostname(settings["domain"])
    data = decrypt_state(envelope, unprotect_secret(settings["protected_token"]), stamp[0], domain)
    owner = data.get("owner")
    if (not isinstance(owner, list) or len(owner) != 4 or owner[0] != stamp[0]
            or type(owner[3]) is not int or owner[3] > stamp[3]):
        raise ValueError("공동 웹 설정의 권한 세대가 올바르지 않습니다.")
    if owner[1] == stamp[1]:
        return  # This installation's saved edits take precedence over its old upload.
    access = import_access(data["access"], data.get("pins"))
    chain = data["chain"].encode("ascii")
    key = serialization.load_pem_private_key(data["key"].encode("ascii"), None)
    reusable = True
    try:
        inspect_certificate(chain, key, domain)
    except ValueError as exc:
        if str(exc) != "certificate expired or not yet valid":
            raise
        from cryptography import x509
        from datetime import datetime, timezone
        # Hostname/key checks precede the date check. Never serve an expired
        # imported certificate; retain the verified TXT ownership for renewal.
        if x509.load_pem_x509_certificates(chain)[0].not_valid_after_utc >= datetime.now(timezone.utc):
            raise
        reusable = False
    port = data.get("external_port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("공동 웹 외부 포트를 확인하지 못했습니다.")
    mapping_owner = data.get("mapping_owner")
    if not valid_id(mapping_owner):
        raise ValueError("이전 웹 공개 규칙의 소유자를 확인하지 못했습니다.")
    txt = data.get("txt_owner") or {}
    if not isinstance(txt, dict) or len(json.dumps(txt)) > 16000:
        raise ValueError("공동 인증서 갱신 기록을 확인하지 못했습니다.")
    terms = data.get("terms_agreed_at", "")
    if not isinstance(terms, str) or len(terms) > 64:
        raise ValueError("공동 HTTPS 설정을 확인하지 못했습니다.")
    # The UI takes the relay/bridge locks before the manager lock. Taking the
    # manager first and then calling guard() inverted that order and could
    # deadlock an incoming settings import against a UI status/lease update.
    relay = getattr(manager, 'delegation', None)
    bridge = getattr(relay, 'bridge', None)
    with ExitStack() as locks:
        for lock in (getattr(relay, 'lock', None), getattr(bridge, 'lock', None), manager.lock):
            if lock is not None:
                locks.enter_context(lock)
        guard()
        # The old listener is gated during preparation. Publish only validated state.
        if reusable:
            publish_certificate(root, chain, key, domain, commit=lambda path, info: (guard(), write_json(path, info)))
        if txt.get("domain") == domain.removesuffix(".duckdns.org"):
            write_json(root / "https" / "txt_owner.json", txt)
        write_json(root / "access_settings.json", access)
        manager.access.data, manager.access.saved, manager.access.error = access, True, None
        manager.access_view.update(manager.access.public_view())
        https = dict(manager.https.settings, enabled=True, domain=domain)
        if not https.get("terms_agreed_at"):
            https["terms_agreed_at"] = terms
        write_json(root / "https_settings.json", https)
        with manager.https.lock:
            manager.https.settings = https
            manager.https.view["certificate"] = public_certificate(root)
        manager.pin_public_port(port)
        manager.previous_mapping_owner = mapping_owner
    if data.get("public_enabled") and not manager.public_preference_touched:
        manager.public.configure(True)

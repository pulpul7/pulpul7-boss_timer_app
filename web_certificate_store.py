"""Immutable certificate generations. Private keys are encrypted at rest."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import secrets
import uuid

from web_ddns_service import normalize_domain
from web_secret_store import protect_secret, unprotect_secret
from web_service_common import read_json, write_json, timestamp

DEFAULT_HTTPS_PORT = 8766


def hostname(value):
    return normalize_domain(value) + ".duckdns.org"


def inspect_certificate(chain, private_key, domain):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    certificates = x509.load_pem_x509_certificates(chain)
    if not certificates or len(certificates) > 8:
        raise ValueError("invalid certificate chain")
    leaf = certificates[0]
    names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if hostname(domain) not in names.get_values_for_type(x509.DNSName):
        raise ValueError("certificate hostname mismatch")
    encode = lambda key: key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if encode(leaf.public_key()) != encode(private_key.public_key()):
        raise ValueError("certificate key mismatch")
    now = datetime.now(timezone.utc)
    if not leaf.not_valid_before_utc <= now < leaf.not_valid_after_utc:
        raise ValueError("certificate expired or not yet valid")
    lifetime = leaf.not_valid_after_utc - leaf.not_valid_before_utc
    from datetime import timedelta
    renew_at = leaf.not_valid_after_utc - min(timedelta(days=30), lifetime / 3)
    return dict(expires_at=leaf.not_valid_after_utc.isoformat(), renew_at=renew_at.isoformat())


def load_certificate(root):
    """Read only known generation filenames, never a path from the manifest."""
    from cryptography.hazmat.primitives import serialization
    directory = Path(root) / "https"
    info = read_json(directory / "current.json")
    revision = info.get("revision", "")
    if not isinstance(revision, str) or len(revision) != 32 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("invalid certificate generation")
    domain = hostname(info["domain"])
    password = unprotect_secret(info["protected_password"])
    chain_path, key_path = directory / (revision + ".pem"), directory / (revision + ".key")
    if chain_path.stat().st_size > 65536 or key_path.stat().st_size > 16384:
        raise ValueError("certificate file too large")
    chain = chain_path.read_bytes()
    key = serialization.load_pem_private_key(key_path.read_bytes(), password.encode("ascii"))
    dates = inspect_certificate(chain, key, domain)
    return dict(info, **dates), chain_path, key_path, password


def publish_certificate(root, chain, key, domain, commit=None):
    from cryptography.hazmat.primitives import serialization
    if not isinstance(chain, bytes) or len(chain) > 65536:
        raise ValueError("invalid certificate size")
    dates = inspect_certificate(chain, key, domain)
    directory = Path(root) / "https"
    directory.mkdir(parents=True, exist_ok=True)
    revision, password = uuid.uuid4().hex, secrets.token_hex(32)
    protected = protect_secret(password)
    encrypted_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.BestAvailableEncryption(password.encode("ascii")))
    # Publish the pointer last. A failed renewal leaves the previous generation intact.
    (directory / (revision + ".pem")).write_bytes(chain)
    (directory / (revision + ".key")).write_bytes(encrypted_key)
    info = dict(schema=1, revision=revision, domain=hostname(domain), protected_password=protected,
                issued_at=timestamp(), **dates)
    if commit:
        commit(directory / "current.json", info)
    else:
        write_json(directory / "current.json", info)
    return info


def public_certificate(root):
    try:
        info = read_json(Path(root) / "https" / "current.json")
        return {key: info.get(key, "") for key in ("domain", "revision", "issued_at", "expires_at", "renew_at")}
    except (OSError, ValueError):
        return {}

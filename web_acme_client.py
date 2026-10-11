"""Let’s Encrypt DNS-01 using the saved DuckDNS token; no desktop imports."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
from urllib.parse import urlencode, urlsplit

from https_transport import create_verified_ssl_context
from web_ddns_service import _https, normalize_domain, validate_token
from web_secret_store import protect_secret, unprotect_secret
from web_service_common import read_json, write_json

DIRECTORY = "https://acme-v02.api.letsencrypt.org/directory"
TERMS_PAGE = "https://letsencrypt.org/repository/"


class CertificateCancelled(Exception):
    pass


class CertificateIssueError(Exception):
    """Only fixed, user-safe messages may be shown by the service."""


class DuckTXT:
    def __init__(self, root, domain, token, cancelled):
        import dns.resolver
        self.root, self.domain, self.token, self.cancelled = root, domain, token, cancelled
        self.name = "_acme-challenge." + domain + ".duckdns.org"
        self.resolver = dns.resolver.Resolver(configure=False)
        self.resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
        self.resolver.timeout, self.resolver.lifetime = 2, 5
        names = self.resolver.resolve("duckdns.org", "NS", lifetime=5)
        self.servers = []
        for name in list(names)[:2]:
            self.check()
            self.servers.append(str(self.resolver.resolve(str(name.target), "A", lifetime=5)[0]))
        if not self.servers:
            raise CertificateIssueError("DuckDNS 인증용 DNS 서버를 확인하지 못했습니다.")

    def check(self):
        if self.cancelled():
            raise CertificateCancelled()

    def values(self, address):
        import dns.flags
        import dns.message
        import dns.query
        import dns.rcode
        import dns.rdatatype
        self.check()
        query = dns.message.make_query(self.name, dns.rdatatype.TXT)
        reply = dns.query.udp(query, address, timeout=3)
        if reply.flags & dns.flags.TC:
            reply = dns.query.tcp(query, address, timeout=3)
        if not reply.flags & dns.flags.AA or reply.rcode() not in {dns.rcode.NOERROR, dns.rcode.NXDOMAIN}:
            raise CertificateIssueError("DuckDNS 인증용 DNS 응답을 확인하지 못했습니다.")
        values = {b"".join(row.strings).decode("ascii") for group in reply.answer
                  if group.rdtype == dns.rdatatype.TXT for row in group}
        # DuckDNS can represent an unset TXT as a present record containing "".
        # Empty content cannot be an ACME proof or a foreign ownership record.
        # Keep all non-empty values intact, including whitespace-only content.
        values.discard("")
        return values

    def present(self, value):
        path = self.root / "https" / "txt_owner.json"
        try:
            previous = read_json(path)
        except FileNotFoundError:
            previous = {}
        owned = set(previous.get("values", [])) if previous.get("domain") == self.domain else set()
        if previous.get("domain") == self.domain and previous.get("value"):
            owned.add(previous["value"])
        observed = set()
        for address in self.servers:
            existing = self.values(address)
            if existing - owned:
                raise CertificateIssueError("기존 DNS 인증 기록을 이 PC의 기록으로 확인하지 못했습니다. 다른 인증서 서비스가 사용한 주소인지 확인해주세요.")
            observed.update(existing)
        self.check()
        # Remember ownership before the request: a timeout may still have applied it.
        write_json(path, dict(domain=self.domain, values=sorted(observed | {value})))
        query = urlencode(dict(domains=self.domain, token=self.token, txt=value, verbose="true"))
        lines = _https("https://www.duckdns.org/update?" + query).splitlines()
        if len(lines) < 3 or lines[0] != "OK" or lines[1] != value or lines[2] not in {"UPDATED", "NOCHANGE"}:
            raise CertificateIssueError("DuckDNS 인증 기록 저장에 실패했습니다. 무료 주소의 Token을 확인해주세요.")
        # Keep this harmless challenge hash. Never clear another PC's TXT record.
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            self.check()
            try:
                if all(value in self.values(address) for address in self.servers):
                    return
            except CertificateCancelled:
                raise
            except Exception:
                pass
            for _ in range(5):
                self.check()
                time.sleep(1)
        raise CertificateIssueError("DNS 인증 반영 시간이 초과되었습니다. 잠시 뒤 다시 시도해주세요.")


def issue_certificate(root, cancelled, progress):
    from acme import challenges, client, errors, messages
    import josepy
    import requests
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    ddns = read_json(root / "ddns_settings.json")
    domain = normalize_domain(ddns["domain"])
    token = unprotect_secret(ddns["protected_token"])
    validate_token(token)
    directory = root / "https"
    directory.mkdir(parents=True, exist_ok=True)
    account_path = directory / "account.json"
    try:
        account = read_json(account_path)
        if account.get("directory") != DIRECTORY:
            raise ValueError("account directory mismatch")
        account_key = serialization.load_pem_private_key(unprotect_secret(account["protected_key"]).encode("ascii"), None)
    except FileNotFoundError:
        account_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = account_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()).decode("ascii")
        account = dict(directory=DIRECTORY, protected_key=protect_secret(pem), registration=None)
        write_json(account_path, account)

    class VerifiedAdapter(requests.adapters.HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            self.verified_context = create_verified_ssl_context()
            kwargs["ssl_context"] = self.verified_context
            return super().init_poolmanager(*args, **kwargs)

        def build_connection_pool_key_attributes(self, request, verify, cert=None):
            host, options = super().build_connection_pool_key_attributes(request, verify, cert)
            options["ssl_context"] = self.verified_context
            options["cert_reqs"] = "CERT_REQUIRED"
            return host, options

    class Network(client.ClientNetwork):
        def _send_request(self, method, url, *args, **kwargs):
            if cancelled():
                raise CertificateCancelled()
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname != "acme-v02.api.letsencrypt.org" or parts.port not in {None, 443}:
                raise ValueError("unexpected ACME endpoint")
            kwargs["allow_redirects"] = False
            return super()._send_request(method, url, *args, **kwargs)

    # The SDK debug logger contains account data; our diagnostics contain stage names only.
    logging.getLogger("acme.client").setLevel(logging.WARNING)
    net = Network(josepy.JWKRSA(key=account_key), verify_ssl=True, timeout=10, user_agent="BossTimer-Web-HTTPS")
    net.session.trust_env = False
    net.session.mount("https://", VerifiedAdapter())
    try:
        progress("무료 인증서 서버에 연결합니다…", "acme_directory")
        api = client.ClientV2(messages.Directory.from_json(net.get(DIRECTORY).json()), net)
        if account.get("registration"):
            net.account = messages.RegistrationResource.from_json(account["registration"])
        else:
            try:
                registration = api.new_account(messages.NewRegistration.from_data(terms_of_service_agreed=True))
            except errors.ConflictError as exc:
                registration = api.query_registration(messages.RegistrationResource(uri=exc.location,
                                               body=messages.Registration()))
            account["registration"] = registration.to_json()
            write_json(account_path, account)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        full_domain = domain + ".duckdns.org"
        csr = (x509.CertificateSigningRequestBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, full_domain)]))
               .add_extension(x509.SubjectAlternativeName([x509.DNSName(full_domain)]), critical=False)
               .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
        progress("주소 소유권 인증을 준비합니다…", "acme_order")
        order = api.new_order(csr)
        for authorization in order.authorizations:
            if authorization.body.status == messages.STATUS_VALID:
                continue
            challenge = next((row for row in authorization.body.challenges if isinstance(row.chall, challenges.DNS01)), None)
            if challenge is None:
                raise CertificateIssueError("무료 인증서 서버가 DNS 인증을 제공하지 않았습니다.")
            progress("DuckDNS 인증 기록을 반영합니다… (최대 3분)", "dns_challenge")
            provider = DuckTXT(root, domain, token, cancelled)
            provider.present(challenge.validation(net.key))
            api.answer_challenge(challenge, challenge.response(net.key))
        progress("인증서를 발급받습니다…", "acme_finalize")
        result = api.poll_and_finalize(order, deadline=datetime.now() + timedelta(seconds=120))
        if cancelled():
            raise CertificateCancelled()
        current = read_json(root / "ddns_settings.json")
        if (current.get("domain"), current.get("protected_token")) != (ddns.get("domain"), ddns.get("protected_token")):
            raise CertificateIssueError("무료 주소 설정이 바뀌었습니다. 새 주소로 다시 발급해주세요.")
        return result.fullchain_pem.encode("ascii"), key, full_domain
    finally:
        net.session.close()

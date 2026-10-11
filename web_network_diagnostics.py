"""Desktop-only, read-only network discovery. Never opens ports or changes rules."""
from __future__ import annotations

import base64
from copy import deepcopy
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from https_transport import create_verified_ssl_context
from web_service_common import close_logger, diagnostic, diagnostic_logger, read_json, timestamp, write_json

STEPS = 7
SHARED_RANGE = ipaddress.ip_network("100.64.0.0/10")
PRIVATE_RANGES = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))

# Static command: no user data, passwords, executable paths or shell fragments.
# All cmdlets are reads; the default-route metric includes its interface metric.
WINDOWS_NETWORK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$interfaces = @(Get-NetIPInterface -ErrorAction SilentlyContinue)
$routes = @(Get-NetRoute -PolicyStore ActiveStore -ErrorAction SilentlyContinue |
    Where-Object { $_.DestinationPrefix -in @('0.0.0.0/0','::/0') })
$addresses = @(Get-NetIPAddress -ErrorAction SilentlyContinue |
    Where-Object { $_.AddressState -eq 'Preferred' -and -not $_.SkipAsSource })
$profiles = @(Get-NetFirewallProfile -ErrorAction SilentlyContinue |
    Select-Object Name, @{Name='Enabled';Expression={[string]$_.Enabled}}, DefaultInboundAction)
$routeRows = @($routes | ForEach-Object {
    $route = $_
    $iface = $interfaces | Where-Object { $_.InterfaceIndex -eq $route.InterfaceIndex -and
        $_.AddressFamily -eq $route.AddressFamily } | Select-Object -First 1
    [pscustomobject]@{ family=[string]$route.AddressFamily; index=[int]$route.InterfaceIndex;
        gateway=[string]$route.NextHop; metric=[int]$route.RouteMetric + [int]$iface.InterfaceMetric }
})
[pscustomobject]@{ routes=$routeRows; addresses=@($addresses | Select-Object InterfaceIndex,IPAddress,PrefixLength);
    firewall=$profiles } | ConvertTo-Json -Depth 5 -Compress
"""


class Cancelled(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _fetch(url, *, context=None, body=None, headers=None, limit=131072):
    # Bypass proxies only for these diagnostics, not globally. A proxy's public
    # IP must not be mistaken for the router's public IP. No router redirects.
    handlers = [urllib.request.ProxyHandler({}), NoRedirect()]
    if urlsplit(url).scheme == "https":
        handlers.append(urllib.request.HTTPSHandler(context=context or create_verified_ssl_context()))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, data=body, headers={"User-Agent": "BossTimer-Web-Diagnostics", **(headers or {})})
    with opener.open(request, timeout=3) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("oversized diagnostic response")
        return data


def _xml(data):
    # Router XML is untrusted. Reject DTD/entities even in UTF-16 responses.
    upper = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("unsupported XML declaration")
    return ET.fromstring(data)


def _text(node, name):
    return next((child.text or "" for child in node.iter() if child.tag.rpartition("}")[2] == name), "").strip()


def _router_url(value, gateway):
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or parts.username or parts.password or parts.fragment
            or parts.hostname != gateway or len(value) > 2048 or not 1 <= (parts.port or 80) <= 65535):
        raise ValueError("router endpoint is not the selected gateway")
    return value


def _windows_network():
    if os.name != "nt":
        raise OSError("Windows network discovery required")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = base64.b64encode(WINDOWS_NETWORK_SCRIPT.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=12, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
    if len(result.stdout) > 262144:
        raise ValueError("network inventory too large")
    data = json.loads(result.stdout.decode("utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("routes"), list) or not isinstance(data.get("addresses"), list):
        raise ValueError("invalid network inventory")
    return data


def _route(data, version):
    family = "IPv4" if version == 4 else "IPv6"
    for route in sorted((r for r in data["routes"] if r.get("family") == family), key=lambda r: r["metric"]):
        try:
            gateway = ipaddress.ip_address(route["gateway"])
            if gateway.version != version or gateway.is_unspecified or gateway.is_loopback:
                continue
            for row in data["addresses"]:
                address = ipaddress.ip_address(row["IPAddress"])
                if row["InterfaceIndex"] != route["index"] or address.version != version:
                    continue
                if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
                    continue
                if version == 6 and not address.is_global:
                    continue
                return dict(local=str(address), gateway=str(gateway), prefix=int(row["PrefixLength"]), index=route["index"])
        except (ValueError, KeyError, TypeError):
            continue
    return None


def _upnp(route, check_cancel):
    if not route:
        return None
    gateway, local = route["gateway"], route["local"]
    locations = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind((local, 0))
        udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
        udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        for version in (1, 2):
            request = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                       'MAN: "ssdp:discover"\r\nMX: 1\r\n'
                       f"ST: urn:schemas-upnp-org:device:InternetGatewayDevice:{version}\r\n\r\n").encode("ascii")
            for target in (("239.255.255.250", 1900), (gateway, 1900)):
                try:
                    udp.sendto(request, target)
                except OSError:
                    pass
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            check_cancel()
            udp.settimeout(min(0.25, max(0.01, deadline - time.monotonic())))
            try:
                packet, peer = udp.recvfrom(8192)
            except socket.timeout:
                continue
            if peer[0] != gateway:
                continue
            match = re.search(r"^location:\s*(\S+)\s*$", packet.decode("ascii", errors="ignore"), re.I | re.M)
            if match:
                try:
                    location = _router_url(match[1], gateway)
                except ValueError:
                    continue
                if location not in locations:
                    locations.append(location)
                    if len(locations) >= 3:
                        break
    for location in locations:
        check_cancel()
        try:
            device = _xml(_fetch(location))
            base = _router_url(_text(device, "URLBase") or location, gateway)
            for service in device.iter():
                if service.tag.rpartition("}")[2] != "service":
                    continue
                kind = _text(service, "serviceType")
                if not re.fullmatch(r"urn:schemas-upnp-org:service:WAN(?:IP|PPP)Connection:[12]", kind):
                    continue
                control = _router_url(urljoin(base, _text(service, "controlURL")), gateway)
                model = _text(device, "modelName")[:80]
                try:
                    body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                            f'<u:GetExternalIPAddress xmlns:u="{kind}"/></s:Body></s:Envelope>').encode("utf-8")
                    reply = _xml(_fetch(control, body=body, headers={"Content-Type": "text/xml; charset=utf-8",
                        "SOAPAction": f'"{kind}#GetExternalIPAddress"'}))
                    wan = ipaddress.ip_address(_text(reply, "NewExternalIPAddress"))
                    wan = str(wan) if wan.version == 4 and not wan.is_unspecified else ""
                except (OSError, ValueError, ET.ParseError, RuntimeError):
                    wan = ""
                return dict(service=kind, gateway=gateway, wan=wan, model=model, control=control)
        except (OSError, ValueError, ET.ParseError, RuntimeError):
            continue
    return None


def _natpmp(route, check_cancel):
    if not route:
        return None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind((route["local"], 0))
        udp.connect((route["gateway"], 5351))
        # RFC 6886 section 3.2: opcode zero queries the external address only.
        for timeout in (0.25, 0.5, 1):
            check_cancel()
            udp.settimeout(timeout)
            udp.send(b"\x00\x00")
            try:
                data = udp.recv(64)
            except socket.timeout:
                continue
            if len(data) == 12:
                version, opcode, result, epoch, raw_ip = struct.unpack("!BBHI4s", data)
                if version == 0 and opcode == 128 and result == 0:
                    return dict(wan=socket.inet_ntoa(raw_ip), epoch=epoch)
        return None


class NetworkDiagnostics:
    """One cancellable diagnostic job, independent of the server coordinator."""
    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.thread = None
        self.logger = None
        self.view = dict(state="idle", progress=0, checked_at=None, checks=[],
                         message="네트워크 탭에서 외부 공개에 필요한 조건을 확인합니다.")
        try:
            saved = read_json(root / "network_diagnostics.json")
            if saved.get("schema") == 1 and isinstance(saved.get("checks"), list) and len(saved["checks"]) <= STEPS:
                for row in saved["checks"]:
                    if not all(isinstance(row.get(key), str) and len(row[key]) <= 1024 for key in ("key", "label", "value", "detail", "health")):
                        raise ValueError("invalid network result")
                self.view.update(checks=saved["checks"], checked_at=saved.get("checked_at"),
                    message="이전 진단 결과입니다. 현재 연결 상태는 다시 확인해주세요.")
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def status(self):
        with self.lock:
            return deepcopy(self.view)

    def start(self, service_state):
        with self.lock:
            if self.closed.is_set() or (self.thread and self.thread.is_alive()):
                return False
            self.view.update(state="running", progress=0, checked_at=None, checks=[], message="PC 네트워크를 확인합니다.")
            self.thread = threading.Thread(target=self._run, args=(dict(service_state),), name="boss-web-network", daemon=True)
            self.thread.start()
        return True

    def _cancel(self):
        if self.closed.is_set():
            raise Cancelled()

    def _record(self, key, label, value, detail, health="info"):
        self._cancel()
        row = dict(key=key, label=label, value=value, detail=detail, health=health)
        with self.lock:
            self.view["checks"].append(row)
            self.view.update(progress=len(self.view["checks"]), message=label + " 확인 완료")
        diagnostic(self.logger, "network_check", key=key, health=health, value=value)

    def _error(self, key, label, exc):
        reason = getattr(exc, "reason", exc)
        certificate = isinstance(reason, ssl.SSLCertVerificationError)
        detail = "HTTPS 인증서를 검증하지 못했습니다. PC 시간·Windows 인증서 상태를 확인해주세요." if certificate else \
                 "응답이 없거나 확인하지 못했습니다. 미지원이나 외부 접속 불가로 단정할 수는 없습니다."
        self._record(key, label, "확인 못함", detail, "warning")
        diagnostic(self.logger, "network_check_failed", key=key, error_type=type(exc).__name__)

    def _run(self, service):
        started = time.monotonic()
        try:
            self.logger = diagnostic_logger(self.root, "network")
            diagnostic(self.logger, "network_diagnostics_started")
            try:
                self._cancel()
                inventory = _windows_network()
                ipv4, ipv6 = _route(inventory, 4), _route(inventory, 6)
                self._record("local", "PC · 기본 공유기", f"{ipv4['local']} → {ipv4['gateway']}" if ipv4 else "사용 가능한 IPv4 경로 없음",
                    "기본 경로의 우선순위로 선택했습니다. VPN·여러 랜카드가 있으면 실제 연결 경로가 다를 수 있습니다.", "ok" if ipv4 else "warning")
                firewall = inventory.get("firewall", [])
                enabled = [str(row["Name"]) for row in firewall if row.get("Enabled") is True or str(row.get("Enabled")) == "True"]
                self._record("firewall", "Windows 방화벽", "사용 중: " + ", ".join(enabled) if enabled else "사용 프로필 확인 필요",
                    "방화벽 설정을 읽었습니다. 외부 공개용 허용 규칙은 HTTPS·포트 개방 단계에서 확인합니다.")
            except Cancelled:
                raise
            except Exception as exc:
                ipv4 = ipv6 = None
                self._error("local", "PC · 기본 공유기", exc)
                self._record("firewall", "Windows 방화벽", "확인 못함", "Windows 네트워크 정보를 읽지 못했습니다.", "warning")
            self._cancel()
            try:
                if service.get("state") == "running":
                    with socket.create_connection(("127.0.0.1", service["port"]), timeout=1):
                        pass
                    self._record("server", "로컬 웹서버", f"127.0.0.1:{service['port']} 응답", "같은 PC에서 사용할 수 있습니다. 아직 외부 주소가 아닙니다.", "ok")
                else:
                    self._record("server", "로컬 웹서버", "웹서비스 꺼짐 또는 준비 중", "서비스 탭에서 켠 뒤 다시 진단하면 포트 응답을 확인합니다.", "warning")
            except Exception as exc:
                self._error("server", "로컬 웹서버", exc)
            public = None
            self._cancel()
            try:
                public = ipaddress.ip_address(_fetch("https://api.ipify.org", limit=128).decode("ascii").strip())
                if public.version != 4 or not public.is_global:
                    raise ValueError("invalid public IPv4")
                self._record("public", "인터넷 공인 IPv4", str(public), "ipify HTTPS 조회 결과입니다. 외부에서 들어오는 연결 성공을 뜻하지는 않습니다.", "ok")
            except Exception as exc:
                public = None
                self._error("public", "인터넷 공인 IPv4", exc)
            self._cancel()
            try:
                if ipv6:
                    public6 = ipaddress.ip_address(_fetch("https://api6.ipify.org", limit=128).decode("ascii").strip())
                    if public6.version != 6 or not public6.is_global:
                        raise ValueError("invalid public IPv6")
                    self._record("ipv6", "IPv6 직접 연결 후보", str(public6),
                        "IPv6 인터넷 통신을 확인했습니다. 공유기·PC 방화벽과 이용자의 IPv6 지원을 확인해야 합니다.", "ok")
                else:
                    self._record("ipv6", "IPv6 직접 연결 후보", "공인 IPv6 기본 경로 없음", "현재 정보로는 IPv4 자동 포트 개방을 우선 검토합니다.")
            except Exception as exc:
                self._error("ipv6", "IPv6 직접 연결 후보", exc)
            self._cancel()
            upnp = pmp = None
            try:
                upnp = _upnp(ipv4, self._cancel)
                self._record("upnp", "UPnP 자동 개방 지원", "공유기 IGD 발견" + (" · " + upnp["model"] if upnp["model"] else "") if upnp else "응답 없음 / 확인 못함",
                    "포트 개방용 서비스를 발견했습니다. 실제 개방 권한과 성공 여부는 다음 공개 단계에서 확인합니다." if upnp else
                    "UPnP 꺼짐·공유기 미지원·VPN·방화벽 등이 원인일 수 있습니다. 포트를 개방하지 않았습니다.", "ok" if upnp else "warning")
            except Cancelled:
                raise
            except Exception as exc:
                self._error("upnp", "UPnP 자동 개방 지원", exc)
            self._cancel()
            try:
                pmp = _natpmp(ipv4, self._cancel)
            except Cancelled:
                raise
            except Exception as exc:
                diagnostic(self.logger, "natpmp_query_failed", error_type=type(exc).__name__)
            wan = (upnp or {}).get("wan") or (pmp or {}).get("wan") or ""
            topology = "공유기 외부 IP 확인 못함"
            detail = "상위 공유기·통신사 NAT 여부는 아직 확인되지 않았습니다. 외부 접속 검증은 공개 단계에서 진행합니다."
            health = "warning"
            if wan:
                address = ipaddress.ip_address(wan)
                if address in SHARED_RANGE or any(address in block for block in PRIVATE_RANGES):
                    topology = f"상위 NAT 가능성 · 공유기 외부 IP {wan}"
                    detail = "공유기가 공인 IPv4를 직접 받지 않았습니다. 상위 공유기 또는 통신사 NAT 때문에 이 공유기의 포트 개방만으로 공개되지 않을 수 있습니다."
                elif public and address == public:
                    topology = f"공유기 외부 IP와 공인 IP 일치 · {wan}"
                    detail = "직접 공개의 IP 조건을 확인했습니다. 아직 외부에서 접속한 것은 아닙니다."
                    health = "ok"
                elif public:
                    topology = f"공유기 외부 IP와 조회 IP 다름 · {wan}"
                    detail = "상위 NAT·VPN·여러 인터넷 회선 등의 가능성이 있습니다. 외부 공개 전에 추가 확인이 필요합니다."
            self._record("topology", "NAT-PMP · 외부 공개 조건", ("NAT-PMP 응답 · " if pmp else "NAT-PMP 응답 없음 · ") + topology, detail, health)
            self._cancel()
            checked_at = timestamp()
            with self.lock:
                self.view.update(state="ready", checked_at=checked_at,
                    message="진단 완료 · 실제 외부 접속은 아직 확인하지 않았습니다.")
                saved = dict(schema=1, checked_at=checked_at, checks=deepcopy(self.view["checks"]))
            try:
                write_json(self.root / "network_diagnostics.json", saved)
            except OSError as exc:
                diagnostic(self.logger, "network_result_write_failed", error_type=type(exc).__name__)
            diagnostic(self.logger, "network_diagnostics_completed", duration_sec=round(time.monotonic() - started, 2))
        except Cancelled:
            diagnostic(self.logger, "network_diagnostics_cancelled")
        except Exception as exc:
            with self.lock:
                self.view.update(state="error", message="네트워크 진단을 마치지 못했습니다. 웹 전용 진단 로그를 확인해주세요.")
            diagnostic(self.logger, "network_diagnostics_failed", error_type=type(exc).__name__)
        finally:
            if self.logger:
                close_logger(self.logger)

    def close(self):
        self.closed.set()

"""Isolated HTTPS + headless Chrome PWA probe. No install, live server or user profile.

Uses the production WSGI handler with fixture access/schedule data and a temporary
certificate pinned only to this Chrome process. Does not trust certificates in OS.
The --app run verifies app-window display mode, not an Android WebAPK install.
"""
import base64
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
from socketserver import ThreadingMixIn
import ssl
import struct
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace as NS
from urllib.parse import urlsplit
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from web_asset_store import bundled_assets
from web_https_app import HTTPSApplication


class Cdp:
    """Minimal loopback CDP websocket, keeping the probe dependency-free."""
    def __init__(self, url):
        target = urlsplit(url)
        self.socket = socket.create_connection((target.hostname, target.port), timeout=15)
        self.buffer = b''
        self.serial = 0
        nonce = base64.b64encode(os.urandom(16)).decode()
        self.socket.sendall((f'GET {target.path} HTTP/1.1\r\nHost: {target.netloc}\r\n'
                             f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {nonce}\r\n'
                             'Sec-WebSocket-Version: 13\r\n\r\n').encode())
        while b'\r\n\r\n' not in self.buffer:
            self.buffer += self.socket.recv(4096)
        header, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
        expected = base64.b64encode(hashlib.sha1((nonce + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        if header.splitlines()[0].split()[1] != b'101' or expected not in header:
            self.socket.close()
            raise RuntimeError('CDP websocket rejected: ' + header.splitlines()[0].decode())

    def take(self, count):
        while len(self.buffer) < count:
            chunk = self.socket.recv(65536)
            if not chunk:
                raise EOFError('CDP closed')
            self.buffer += chunk
        raw, self.buffer = self.buffer[:count], self.buffer[count:]
        return raw

    def frame(self, payload, opcode=1):
        size = len(payload)
        head = bytes([0x80 | opcode, 0x80 | (size if size < 126 else 126 if size < 65536 else 127)])
        if size >= 126:
            head += struct.pack('!H' if size < 65536 else '!Q', size)
        mask = os.urandom(4)
        self.socket.sendall(head + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(payload)))

    def receive(self):
        pieces = []
        while True:
            first, second = self.take(2)
            size = second & 127
            if size in (126, 127):
                size = struct.unpack('!H' if size == 126 else '!Q', self.take(2 if size == 126 else 8))[0]
            raw = self.take(size)
            if first & 15 == 9:
                self.frame(raw, opcode=10)
                continue
            if first & 15 == 8:
                raise EOFError('CDP closing')
            pieces.append(raw)
            if first & 0x80:
                return json.loads(b''.join(pieces))

    def call(self, method, params=None, session=None):
        self.serial += 1
        request = dict(id=self.serial, method=method, params=params or {})
        if session:
            request['sessionId'] = session
        self.frame(json.dumps(request).encode())
        while True:
            reply = self.receive()
            if reply.get('id') == self.serial:
                if 'error' in reply:
                    raise RuntimeError(str(reply['error']))
                return reply.get('result', {})


def certificate(folder):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
                           x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = folder/'certificate.pem', folder/'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return cert_path, key_path, base64.b64encode(hashlib.sha256(spki).digest()).decode()


def probe_chrome(folder, url, spki, *, app_mode=False, restricted_runner=False):
    chrome = Path(os.environ.get('PROGRAMFILES', r'C:\Program Files'))/'Google/Chrome/Application/chrome.exe'
    profile = folder/('app-profile' if app_mode else 'browser-profile')
    args = [str(chrome), '--headless=new', '--remote-debugging-port=0', f'--user-data-dir={profile}',
            '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
            '--disable-component-update', f'--ignore-certificate-errors-spki-list={spki}',
            '--disable-extensions', '--disable-default-apps', '--disable-gpu', '--window-size=480,850',
            f'--app={url}' if app_mode else url]
    if restricted_runner:
        # Only this disposable browser visiting our loopback fixture. Never a
        # setting for the user's browser or for browsing external websites.
        args[1:1] = ['--in-process-gpu', '--no-sandbox']
    log = (folder/('app-chrome.log' if app_mode else 'browser-chrome.log')).open('wb')
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=log)
    cdp = None
    succeeded = False
    try:
        deadline = time.monotonic() + 15
        port_file = profile/'DevToolsActivePort'
        while not port_file.exists():
            if time.monotonic() > deadline:
                raise RuntimeError('Isolated Chrome failed to start')
            time.sleep(.05)
        port, endpoint = port_file.read_text().splitlines()[:2]
        cdp = Cdp(f'ws://127.0.0.1:{port}{endpoint}')
        version = cdp.call('Browser.getVersion')['product']
        while True:
            pages = [row for row in cdp.call('Target.getTargets')['targetInfos'] if row['type'] == 'page']
            if pages:
                break
            if time.monotonic() > deadline:
                raise RuntimeError('No Chrome page target')
            time.sleep(.05)
        session = cdp.call('Target.attachToTarget', dict(targetId=pages[0]['targetId'], flatten=True))['sessionId']
        cdp.call('Page.enable', session=session)
        expression = "({ready:document.readyState,secure:isSecureContext,url:location.href,standalone:matchMedia('(display-mode: standalone)').matches,manifest:document.querySelector('link[rel=manifest]')?.href,worker:!!navigator.serviceWorker.controller})"
        while True:
            report = cdp.call('Runtime.evaluate', dict(expression=expression, returnByValue=True), session)['result'].get('value', {})
            if report.get('ready') == 'complete' and report.get('manifest'):
                break
            if time.monotonic() > deadline:
                raise RuntimeError('HTTPS document did not load: ' + str(report))
            time.sleep(.1)
        manifest = cdp.call('Page.getAppManifest', session=session)
        report.update(browser=version, manifest_errors=manifest['errors'],
                      manifest_data=json.loads(manifest.get('data') or '{}'),
                      installability=cdp.call('Page.getInstallabilityErrors', session=session)['installabilityErrors'])
        assert report['secure'], report
        assert report['standalone'] == app_mode, report
        assert report['manifest_errors'] == [], report
        assert report['installability'] == [], report
        assert report['manifest_data']['display'] == 'standalone', report
        # Decode both icons in the real renderer, not only their PNG headers.
        check_icons = "Promise.all([192,512].map(async n=>{const i=new Image();i.src='/pwa/icon-'+n+'.png';await i.decode();return [i.naturalWidth,i.naturalHeight]}))"
        icons = cdp.call('Runtime.evaluate', dict(expression=check_icons, awaitPromise=True, returnByValue=True), session)
        assert icons['result'].get('value') == [[192, 192], [512, 512]], icons
        report['decoded_icons'] = icons['result']['value']
        if not app_mode:
            cdp.call('Emulation.setUserAgentOverride', dict(userAgent='Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Mobile Safari/537.36'), session)
            cdp.call('Page.reload', session=session)
            deadline=time.monotonic()+10
            while True:
                ui=cdp.call('Runtime.evaluate', dict(expression="({ready:document.readyState,entry:!!document.querySelector('#bt-pwa-install'),count:document.querySelectorAll('#bt-pwa-install').length,visible:!!document.querySelector('#bt-pwa-install')&&!document.querySelector('#bt-pwa-install').hidden,styled:document.querySelector('#bt-pwa-install')&&getComputedStyle(document.querySelector('#bt-pwa-install')).display==='flex'})",returnByValue=True),session)['result'].get('value',{})
                if ui.get('ready')=='complete' and ui.get('count')==1 and ui.get('visible') and ui.get('styled'):
                    break
                if time.monotonic()>deadline:
                    raise RuntimeError('Android install entry did not load: '+str(ui))
                time.sleep(.1)
            report['android_entry']=ui
        succeeded = True
        return report
    finally:
        if cdp:
            try:
                cdp.call('Browser.close')
            except (OSError, EOFError):
                pass
            cdp.socket.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate(); process.wait(timeout=5)
        log.close()
        if not succeeded:
            print(ascii((folder/('app-chrome.log' if app_mode else 'browser-chrome.log')).read_text(errors='replace')[-2000:]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restricted-runner', action='store_true',
                        help='Allow the disposable loopback-only browser in a restricted test runner')
    options = parser.parse_args()
    class ThreadedServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True
    class SilentHandler(WSGIRequestHandler):
        def log_message(self, *_):
            pass
    with tempfile.TemporaryDirectory(prefix='boss-pwa-browser-unit-') as temporary:
        folder = Path(temporary)
        cert, key, spki = certificate(folder)
        access = NS(context=lambda *_args, **_kw: dict(authenticated=False, cookies=[], blocked=False,
                    configured=True, available=True, reauth_required=False), read_wait=lambda *_args, **_kw: 0)
        application = HTTPSApplication('localhost', 0, NS(), access, None, assets=bundled_assets())
        server = make_server('127.0.0.1', 0, application, server_class=ThreadedServer, handler_class=SilentHandler)
        server.base_environ['HTTPS'] = 'on'
        application.port = server.server_port
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'https://localhost:{server.server_port}/'
            browser = probe_chrome(folder, url, spki, restricted_runner=options.restricted_runner)
            app = probe_chrome(folder, url, spki, app_mode=True, restricted_runner=options.restricted_runner)
            print(json.dumps(dict(browser=browser, app_window=app), ensure_ascii=True, indent=2))
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == '__main__':
    main()

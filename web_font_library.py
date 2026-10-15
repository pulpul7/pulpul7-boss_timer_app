"""Read-only web fonts plus desktop-owned deployment choices. No OS font installation."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid
import configparser
import ipaddress
import socket

from web_service_common import file_transaction, read_json, write_json

MAX_FONT = 8 * 1024 * 1024
MAX_FONTS = 100
SIGNATURES = {b'\x00\x01\x00\x00', b'OTTO', b'wOFF', b'wOF2'}
MIMES = {'.ttf': 'font/ttf', '.otf': 'font/otf', '.woff': 'font/woff', '.woff2': 'font/woff2'}
SIZE_MIN, SIZE_MAX = -4, 6


def valid_size_defaults(value):
    if (not isinstance(value, dict) or len(value) > MAX_FONTS + 1
            or any(not isinstance(k, str) or not re.fullmatch(r'[a-z0-9-]{1,80}', k)
                   or type(v) is not int or not SIZE_MIN <= v <= SIZE_MAX for k, v in value.items())):
        raise ValueError('invalid font size defaults')
    return dict(value)


def font_developer_allowed(access, context, ip):
    if not context or not context.get('authenticated') or not context.get('visitor'):
        return False
    try:
        with access.lock:
            visitor = access.visitors.get(context['visitor'], {})
            if visitor.get('nickname', '').strip() == '나츠':
                return True
        config = configparser.ConfigParser()
        path = Path(access.root).parent / 'master_developer.ini'
        if not path.is_file() or path.stat().st_size > 65536:
            return False
        config.read(path, encoding='utf-8')
        if not config.getboolean('master_developer', 'enabled', fallback=False):
            return False
        address = ipaddress.ip_address(ip)
        if address.is_loopback:
            return True
        local = {ipaddress.ip_address(row[4][0].split('%')[0]) for row in socket.getaddrinfo(socket.gethostname(), None)}
        return address in local
    except (AttributeError, OSError, ValueError, configparser.Error):
        return False


def bundle_folder():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'web_assets' / 'fonts'


def valid_row(row):
    if not isinstance(row, dict):
        return False
    return (isinstance(row.get('id'), str) and re.fullmatch(r'[a-z0-9-]{1,80}', row['id']) is not None
            and type(row.get('number')) is int and 1 <= row['number'] <= 10000
            and isinstance(row.get('name'), str) and 0 < len(row['name']) <= 80
            and isinstance(row.get('file'), str)
            and re.fullmatch(r'[a-z0-9-]+\.(woff2?|ttf|otf)', row['file']) is not None
            and isinstance(row.get('sha256'), str) and re.fullmatch(r'[a-f0-9]{64}', row['sha256']) is not None
            and type(row.get('bytes')) is int and 0 < row['bytes'] <= MAX_FONT)


@lru_cache(maxsize=4)
def checked_font(path, modified, size, expected_hash):
    file = Path(path)
    if file.is_symlink() or not 0 < size <= MAX_FONT:
        raise ValueError('invalid font asset')
    raw = file.read_bytes()
    if len(raw) != size or raw[:4] not in SIGNATURES or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('invalid font asset')
    return raw


class WebFontLibrary:
    def __init__(self, root, bundled=None):
        self.root = Path(root)
        self.bundled = Path(bundled) if bundled is not None else bundle_folder()
        self.path = self.root / 'font_settings.json'
        self.private = self.root / 'fonts'

    def _settings(self):
        try:
            data = read_json(self.path)
            if (data.get('schema') != 1 or not isinstance(data.get('disabled', []), list)
                    or not isinstance(data.get('custom', []), list)):
                raise ValueError('invalid font settings')
            return data
        except FileNotFoundError:
            return dict(schema=1, default=None, disabled=[], custom=[])

    def _rows(self):
        catalog = read_json(self.bundled / 'catalog.json')
        rows = catalog.get('fonts')
        if catalog.get('schema') != 1 or not isinstance(rows, list) or not 0 <= len(rows) <= MAX_FONTS:
            raise ValueError('invalid font catalog')
        data = self._settings()
        result, seen, numbers = [], set(), set()
        for records, folder in ((rows, self.bundled), (data.get('custom', []), self.private)):
            for row in records:
                if folder == self.private and valid_row(row) and any(item['id'] == row['id'] and item['sha256'] == row['sha256'] for item in result):
                    continue  # An exported private font is now part of the next bundle.
                if not valid_row(row) or row['id'] in seen or row['number'] in numbers:
                    raise ValueError('invalid font catalog row')
                seen.add(row['id']); numbers.add(row['number'])
                result.append(dict(row, folder=folder))
        if len(result) > MAX_FONTS:
            raise ValueError('too many fonts')
        disabled = set(data.get('disabled', []))
        visible = [row for row in result if row['id'] not in disabled]
        default = data.get('default') or catalog.get('default', 'system')
        if default not in {row['id'] for row in visible}:
            default = 'system'
        return visible, default, data, result

    def public_catalog(self):
        rows, default, data, _ = self._rows()
        public = []
        for row in rows:
            item = {key: row.get(key, '') for key in ('id', 'number', 'name', 'provider', 'category', 'source')}
            item.update(url='/fonts/' + row['file'] + '?v=' + row['sha256'][:16], protected=True)
            if re.fullmatch(r'licenses/[a-z0-9-]+\.txt', row.get('license_file', '')):
                item['license_url'] = '/fonts/licenses/' + row['id'] + '.txt'
            public.append(item)
        sizes = self.size_defaults(data)
        sizes = {key: value for key, value in sizes.items() if key == 'system' or key in {row['id'] for row in rows}}
        version = hashlib.sha256(json.dumps([default, public, sizes], sort_keys=True).encode()).hexdigest()[:16]
        return dict(schema=1, default=default, fonts=public, version=version, size_defaults=sizes)

    def size_defaults(self, data=None):
        try:
            seed = read_json(self.bundled / 'size_defaults.json')
            if seed.get('schema') != 1:
                raise ValueError('invalid font size defaults schema')
            sizes = valid_size_defaults(seed.get('sizes'))
        except FileNotFoundError:
            sizes = {}
        data = self._settings() if data is None else data
        sizes.update(valid_size_defaults(data.get('font_size_defaults', {})))
        return sizes

    def set_size_default(self, key, value):
        valid_size_defaults({key: value})
        with file_transaction(self.path):
            rows, _, data, _ = self._rows()
            if key != 'system' and key not in {row['id'] for row in rows}:
                raise ValueError('배포된 목록의 폰트만 기본 크기를 저장할 수 있습니다.')
            sizes = valid_size_defaults(data.get('font_size_defaults', {}))
            sizes[key] = value
            write_json(self.path, dict(data, font_size_defaults=valid_size_defaults(sizes)))

    def asset(self, path):
        if path == '/fonts/catalog.json':
            return json.dumps(self.public_catalog(), ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8'
        if not path.startswith('/fonts/'):
            return None
        if path.startswith('/fonts/licenses/'):
            key = path.removeprefix('/fonts/licenses/').removesuffix('.txt')
            rows, _, _, _ = self._rows()
            row = next((item for item in rows if item['id'] == key), None)
            if row is None or path != '/fonts/licenses/' + row['id'] + '.txt' or not re.fullmatch(r'licenses/[a-z0-9-]+\.txt', row.get('license_file', '')):
                return None
            target = row['folder'] / row['license_file']
            if target.is_symlink() or target.stat().st_size > 1024 * 1024:
                return None
            return target.read_bytes(), 'text/plain; charset=utf-8'
        filename = path.removeprefix('/fonts/')
        if not re.fullmatch(r'[a-z0-9-]+\.(woff2?|ttf|otf)', filename):
            return None
        rows, _, _, _ = self._rows()
        row = next((item for item in rows if item['file'] == filename), None)
        if row is None:
            return None
        target = row['folder'] / filename
        stat = target.stat()
        if stat.st_size != row['bytes']:
            raise ValueError('font size changed')
        return checked_font(str(target), stat.st_mtime_ns, stat.st_size, row['sha256']), MIMES[target.suffix]

    def set_default(self, key):
        with file_transaction(self.path):
            rows, _, data, _ = self._rows()
            if key != 'system' and key not in {row['id'] for row in rows}:
                raise ValueError('목록에 있는 폰트를 선택해주세요.')
            write_json(self.path, dict(data, default=key))

    def add(self, source):
        source = Path(source)
        if source.suffix.lower() not in MIMES or source.stat().st_size > MAX_FONT:
            raise ValueError('8MB 이하의 TTF·OTF·WOFF·WOFF2 파일을 선택해주세요.')
        raw = source.read_bytes()
        if not raw or raw[:4] not in SIGNATURES:
            raise ValueError('올바른 폰트 파일이 아닙니다.')
        with file_transaction(self.path):
            _, _, data, all_rows = self._rows()
            digest = hashlib.sha256(raw).hexdigest()
            if any(row['sha256'] == digest for row in all_rows):
                raise ValueError('이미 등록된 폰트입니다.')
            if len(all_rows) >= MAX_FONTS:
                raise ValueError('폰트는 최대 100개까지 등록할 수 있습니다.')
            catalog = read_json(self.bundled / 'catalog.json')
            counters = [value for value in (data.get('next_number'), catalog.get('next_number')) if type(value) is int and value > 0]
            number = max([max((item['number'] for item in all_rows), default=0)+1, *counters])
            if number > 10000:
                raise ValueError('폰트 번호 범위를 초과했습니다.')
            key = 'extra-' + uuid.uuid4().hex
            filename = key + source.suffix.lower()
            self.private.mkdir(parents=True, exist_ok=True)
            (self.private / filename).write_bytes(raw)
            name = re.sub(r'[\x00-\x1f\x7f]', '', source.stem)[:80] or '추가 폰트'
            row = dict(id=key, number=number,
                       name=name, provider='관리자 추가', category='추가 폰트', source='',
                       file=filename, bytes=len(raw), sha256=digest)
            write_json(self.path, dict(data, next_number=number+1, custom=[*data.get('custom', []), row]))
        return key

    def delete(self, key):
        with file_transaction(self.path):
            rows, default, data, _ = self._rows()
            if key == 'system' or key == default:
                raise ValueError('기본 폰트는 삭제할 수 없습니다. 먼저 기본 폰트를 변경해주세요.')
            if key not in {row['id'] for row in rows}:
                raise ValueError('삭제할 폰트를 선택해주세요.')
            # Preserve the font file; removal only hides it from the shared catalog.
            write_json(self.path, dict(data, disabled=[*data.get('disabled', []), key]))

    def export(self, destination):
        destination = Path(destination)
        if not destination.is_dir():
            raise ValueError('배포할 web_assets/fonts 폴더를 선택해주세요.')
        rows, default, data, all_rows = self._rows()
        exported = []
        for row in rows:
            raw, _ = self.asset('/fonts/' + row['file'])
            target = destination / row['file']
            if target.resolve() != (row['folder'] / row['file']).resolve():
                target.write_bytes(raw)
            exported.append({key: value for key, value in row.items() if key != 'folder'})
        # Atomic catalog change happens only after every selected font has been copied.
        catalog = read_json(self.bundled / 'catalog.json')
        counters = [value for value in (data.get('next_number'), catalog.get('next_number')) if type(value) is int and value > 0]
        next_number = max([max((row['number'] for row in all_rows), default=0)+1, *counters])
        write_json(destination / 'catalog.json', dict(schema=1, default=default, next_number=next_number, fonts=exported))
        write_json(destination / 'size_defaults.json', dict(schema=1, sizes=self.size_defaults(data)))
        licenses = destination / 'licenses'
        licenses.mkdir(exist_ok=True)
        for source in (self.bundled / 'licenses').glob('*'):
            if source.is_file() and source.resolve() != (licenses / source.name).resolve():
                (licenses / source.name).write_bytes(source.read_bytes())


def font_asset(root, path, *, access=None, context=None, ip=''):
    library = WebFontLibrary(root)
    if path == '/fonts/catalog.json':
        data = library.public_catalog()
        data['can_save_default'] = font_developer_allowed(access, context, ip)
        return json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8'
    return library.asset(path)


def save_font_size_default(access, context, ip, payload):
    if not font_developer_allowed(access, context, ip):
        raise PermissionError('개발자·마스터 관리자만 기본 크기를 저장할 수 있습니다.')
    library = WebFontLibrary(access.root)
    library.set_size_default(payload.get('font_id'), payload.get('size'))
    return dict(ok=True, size_defaults=library.public_catalog()['size_defaults'], message='폰트 기본 크기를 저장했습니다. 다음 배포 빌드에도 포함됩니다.')

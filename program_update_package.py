"""Full release contract; validates in memory without extracting executable paths."""
import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile

from ai_module_updater import REPOSITORY

GUI = 'boss_timer_gui.exe'
BOT = 'boss_timer_discord_bot.exe'
FILES = (GUI, BOT)  # Resources/default declarations are bundled by the GUI spec.
MANIFEST = 'program_update.json'
MAX_ZIP = 1024 * 1024 * 1024
MAX_UNPACKED = 2 * MAX_ZIP
_VERSION = re.compile(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[.-]fix(\d*))?\Z', re.I)
ASSET = re.compile(r'bosstimer_update_(.+)\.zip\Z')


def version_key(value):
    match = _VERSION.fullmatch(str(value))
    if not match:
        raise ValueError('전체 업데이트 버전을 확인하세요: ' + str(value))
    return (*map(int, match.group(1, 2, 3)), 0 if match[4] is None else int(match[4] or 1))


def filename(version):
    version_key(version)
    return 'bosstimer_update_' + str(version).removeprefix('v') + '.zip'


def digest_file(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def manifest_for(version, files):
    version_key(version)
    if set(files) != set(FILES):
        raise ValueError('본체와 Discord 봇이 모두 필요합니다.')
    return dict(schema=1, product='boss_timer_full', updater_protocol=1,
                version=str(version), files={name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()})


def validate_manifest(value):
    if (not isinstance(value, dict) or value.get('schema') != 1
            or value.get('product') != 'boss_timer_full' or value.get('updater_protocol') != 1
            or not isinstance(value.get('files'), dict) or set(value['files']) != set(FILES)
            or any(not isinstance(v, str) or re.fullmatch(r'[0-9a-f]{64}', v) is None
                   for v in value['files'].values())):
        raise ValueError('전체 업데이트 구성 정보가 올바르지 않습니다.')
    version_key(value['version'])
    return value


def verify_archive(path, row):
    if Path(path).stat().st_size != row['size'] or digest_file(path) != row['sha256']:
        raise ValueError('전체 업데이트 ZIP의 크기 또는 SHA-256 검증에 실패했습니다.')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if (len(entries) != 3 or {item.filename for item in entries} != {*FILES, MANIFEST}
                or sum(item.file_size for item in entries) > MAX_UNPACKED
                or any(item.is_dir() or stat.S_ISLNK(item.external_attr >> 16)
                       or item.flag_bits & 1 for item in entries)
                or archive.getinfo(MANIFEST).file_size > 65536):
            raise ValueError('압축 경로·필수 실행파일·해제 용량을 확인하세요.')
        manifest = validate_manifest(json.loads(archive.read(MANIFEST)))
        if version_key(manifest['version']) != version_key(row['version']):
            raise ValueError('파일명과 내부 버전이 다릅니다.')
        for name in FILES:
            sha = hashlib.sha256()
            with archive.open(name) as stream:
                if stream.read(2) != b'MZ':
                    raise ValueError('Windows 실행파일 형식이 아닙니다: ' + name)
                sha.update(b'MZ')
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    sha.update(chunk)
            if sha.hexdigest() != manifest['files'][name]:
                raise ValueError('실행파일 SHA-256 검증 실패: ' + name)
    return manifest


def release_rows(releases):
    from urllib.parse import quote
    if not isinstance(releases, list):
        raise ValueError('전체 배포 목록 형식이 올바르지 않습니다.')
    result, seen = [], set()
    for release in releases:
        if release.get('draft') or release.get('prerelease'):
            continue
        for asset in release.get('assets', []):
            match = ASSET.fullmatch(str(asset.get('name', '')))
            if not match:
                continue
            key = version_key(match[1])
            if key in seen:
                raise ValueError('같은 버전의 전체 배포가 여러 개입니다.')
            seen.add(key)
            expected = f"https://github.com/{REPOSITORY}/releases/download/{quote(str(release.get('tag_name', '')), safe='')}/{asset['name']}"
            digest = str(asset.get('digest') or '')
            if (asset.get('browser_download_url') != expected
                    or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest)
                    or type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_ZIP):
                raise ValueError('공식 전체 배포 주소·SHA-256·용량을 확인할 수 없습니다.')
            result.append(dict(version=match[1], url=expected, sha256=digest[7:].lower(),
                               size=asset['size'], name=asset['name'], title=release.get('name') or asset['name']))
    return sorted(result, key=lambda row: version_key(row['version']), reverse=True)

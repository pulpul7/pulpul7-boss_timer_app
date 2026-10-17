"""Stage immutable full versions, switch a pointer, retain the running web binary."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

from https_transport import urlopen_verified
from runtime_storage import atomic_write
from web_service_common import file_transaction
from ai_module_updater import REPOSITORY
from program_update_package import (GUI, BOT, FILES, MANIFEST, MAX_ZIP, version_key,
                                    release_rows, verify_archive, digest_file, validate_manifest)
import program_update_guard as guard

KST = timezone(timedelta(hours=9))
API = f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100'
APPLY_FLAG = '--boss-timer-apply-program-update'
POINTER = 'program_active.json'
RESUME_ENV = 'BOSS_TIMER_UPDATE_JOB'
INSTALL_ENV = 'BOSS_TIMER_INSTALL_ROOT'
UPDATE_ENABLED = True  # User explicitly approved full source integration on 2026-10-17.


def write(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def contained(root, relative):
    root = Path(root).resolve()
    target = root / relative
    if not target.resolve().is_relative_to(root) or any(p.is_symlink() for p in (target, *target.parents) if p != root):
        raise ValueError('업데이트 경로가 지정한 폴더를 벗어났습니다.')
    return target


def request(url):
    return urllib.request.Request(url, headers={'User-Agent': 'BossTimer-Full-Updater/1',
                                               'Accept': 'application/vnd.github+json'})


def open_response(url):
    response = urlopen_verified(request(url), timeout=30, service='전체 업데이트')
    host = urllib.parse.urlsplit(response.url)
    if host.scheme != 'https' or not (host.hostname in {'github.com', 'api.github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}):
        response.close()
        raise ValueError('전체 업데이트 다운로드가 공식 HTTPS 주소를 벗어났습니다.')
    return response


def fetch_catalog():
    with open_response(API) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('배포 목록 용량이 너무 큽니다.')
    return release_rows(json.loads(raw))


class ProgramUpdater:
    def __init__(self, data_root, install_root, version):
        self.data = Path(data_root).resolve()
        self.root = self.data / 'update_program'
        self.install = Path(install_root).resolve()
        self.version = str(version)
        self.lock = threading.Lock()
        self.message = '전체 업데이트 확인 전'

    def check(self):
        rows = fetch_catalog()
        return next((row for row in rows if version_key(row['version']) > version_key(self.version)), None)

    def claim_daily(self, now=None):
        now = now or datetime.now(KST)
        path = self.root / 'schedule.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_transaction(path):
            settings = read(path) if path.exists() else dict(enabled=True, hour=3, minute=0, last_date='')
            if not settings['enabled'] or (now.hour, now.minute) < (settings['hour'], settings['minute']) or settings['last_date'] == now.date().isoformat():
                return False
            settings['last_date'] = now.date().isoformat()
            write(path, settings)
            return True

    def prepare(self, *, automatic=False):
        if not self.lock.acquire(blocking=False):
            raise ValueError('이미 전체 업데이트 파일을 준비하고 있습니다.')
        archive_path = destination = None
        prepared = False
        destination_created = False
        try:
            deadline = time.monotonic() + 480
            guard.require_idle(self.data)
            row = self.check()
            if row is None:
                self.message = '현재 프로그램이 최신 버전입니다.'
                return None
            failures = self.root / 'failed_versions.json'
            if automatic and failures.exists() and row['version'] in read(failures):
                self.message = '이전에 실패한 전체 버전은 자동 재설치하지 않습니다. 수동 적용으로 재시도할 수 있습니다.'
                return None
            for candidate in sorted((self.root / 'jobs').glob('*/transaction.json')):
                try:
                    old = load_job(candidate)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                if (old['phase'] == 'prepared' and old['version'] == row['version']
                        and old['install'] == str(self.install) and old['data'] == str(self.data)
                        and old.get('release_sha256') == row['sha256']):
                    try:
                        verify_version(old['destination'], old['manifest'])
                    except (OSError, ValueError):
                        continue
                    self.message = '이미 검증한 전체 업데이트 파일을 사용합니다.'
                    return candidate
            ticket = uuid.uuid4().hex
            folder = contained(self.data, 'update_program/jobs/' + ticket)
            folder.mkdir(parents=True, exist_ok=False)
            archive_path = folder / 'download.zip.part'
            if shutil.disk_usage(folder).free < row['size'] + 128 * 1024 * 1024:
                raise ValueError('AppData 드라이브의 다운로드·백업 공간이 부족합니다.')
            if shutil.disk_usage(self.install).free < row['size'] * 3 + 128 * 1024 * 1024:
                raise ValueError('다운로드와 새 버전을 보관할 디스크 공간이 부족합니다.')
            # Detect access errors before asking the application to stop.
            probe = contained(self.install, '.update-write-' + ticket)
            with probe.open('xb') as stream:
                stream.write(b'')
            probe.unlink()
            self.message = '전체 업데이트 다운로드 중'
            total = 0
            with open_response(row['url']) as response, archive_path.open('xb') as output:
                for chunk in iter(lambda: response.read(1024 * 1024), b''):
                    if time.monotonic() > deadline:
                        raise ValueError('전체 업데이트 다운로드 시간이 초과됐습니다. 기존 프로그램은 유지합니다.')
                    total += len(chunk)
                    if total > row['size'] or total > MAX_ZIP:
                        raise ValueError('배포 파일의 다운로드 용량이 다릅니다.')
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            self.message = '전체 업데이트 파일 검증 중'
            manifest = verify_archive(archive_path, row)
            with zipfile.ZipFile(archive_path) as archive:
                unpacked = sum(item.file_size for item in archive.infolist())
            if shutil.disk_usage(self.install).free < unpacked + 128 * 1024 * 1024:
                raise ValueError('새 실행파일을 준비할 디스크 공간이 부족합니다.')
            destination = contained(self.install, 'program_versions/' + ticket)
            destination.mkdir(parents=True, exist_ok=False)
            destination_created = True
            with zipfile.ZipFile(archive_path) as archive:
                for name in (*FILES, MANIFEST):
                    with archive.open(name) as source, (destination / name).open('xb') as target:
                        shutil.copyfileobj(source, target)
                        target.flush()
                        os.fsync(target.fileno())
            verify_version(destination, manifest)
            from program_update_helper import config_files
            backup_size = sum(contained(self.data, relative).stat().st_size for relative in config_files(self.data))
            if shutil.disk_usage(self.data).free < backup_size * 2 + 64 * 1024 * 1024:
                raise ValueError('AppData 설정 백업 공간이 부족해 프로그램을 종료하지 않았습니다.')
            job = dict(schema=1, ticket=ticket, phase='prepared', version=row['version'],
                       release_sha256=row['sha256'],
                       data=str(self.data), install=str(self.install), destination=str(destination),
                       previous=str(Path(sys.executable).resolve()), manifest=manifest,
                       previous_pointer=read(self.install / POINTER) if (self.install / POINTER).exists() else None,
                       parent=None, bots=[], new_processes=[], resume={}, message='검증 완료 · 적용 준비')
            path = folder / 'transaction.json'
            write(path, job)
            prepared = True
            try:
                archive_path.unlink()
            except OSError:
                pass
            self.message = '전체 업데이트 검증 완료'
            return path
        except Exception:
            if not prepared:
                if archive_path is not None:
                    try:
                        archive_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                if destination_created:
                    # Only this uncommitted UUID's three owned files, never a
                    # running/previous version or an arbitrary recursive path.
                    for name in (*FILES, MANIFEST):
                        try:
                            contained(destination, name).unlink(missing_ok=True)
                        except OSError:
                            pass
                    try:
                        destination.rmdir()
                    except OSError:
                        pass
            raise
        finally:
            self.lock.release()


def verify_version(folder, manifest):
    validate_manifest(manifest)
    for name, digest in manifest['files'].items():
        if digest_file(contained(folder, name)) != digest:
            raise ValueError('준비된 실행파일이 변경되었습니다: ' + name)


def load_job(path):
    path = Path(path).resolve()
    job = read(path)
    ticket = job.get('ticket', '')
    if (job.get('schema') != 1 or len(ticket) != 32 or any(c not in '0123456789abcdef' for c in ticket)
            or path != Path(job['data']).resolve() / 'update_program' / 'jobs' / ticket / 'transaction.json'
            or Path(job['destination']).resolve() != contained(job['install'], 'program_versions/' + ticket)):
        raise ValueError('전체 업데이트 작업 경로가 올바르지 않습니다.')
    validate_manifest(job['manifest'])
    if job.get('parent') is not None:
        parent = job['parent']
        if (not isinstance(parent, dict) or type(parent.get('pid')) is not int or parent['pid'] <= 0
                or type(parent.get('created')) is not int or parent['created'] <= 0
                or Path(parent.get('path', '')).resolve() != Path(job['previous']).resolve()
                or not Path(job['previous']).resolve().is_relative_to(Path(job['install']).resolve())):
            raise ValueError('이전 본체의 프로세스와 설치 경로가 일치하지 않습니다.')
    return job


def pointer_for(job):
    relative = Path(job['destination']).relative_to(Path(job['install'])).as_posix()
    previous = Path(job['previous'])
    entry_name = previous.name if previous.parent == Path(job['install']) else (job.get('previous_pointer') or {}).get('entry_file', GUI)
    entry = contained(job['install'], entry_name)
    return dict(schema=1, directory=relative, version=job['version'], files=job['manifest']['files'],
                entry_file=entry_name, entry_sha256=digest_file(entry) if entry.is_file() else '')


def target_from_pointer(install):
    path = Path(install) / POINTER
    if not path.exists():
        return None
    value = read(path)
    if value.get('schema') != 1 or not str(value.get('directory', '')).startswith('program_versions/'):
        raise ValueError('프로그램 버전 선택 기록이 올바르지 않습니다.')
    folder = contained(install, value['directory'])
    verify_version(folder, dict(schema=1, product='boss_timer_full', updater_protocol=1,
                                version=value['version'], files=value['files']))
    return folder / GUI


def spawn_gui(executable, install, *, job_path=None):
    import subprocess
    environment = os.environ.copy()
    environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    environment['PYINSTALLER_SUPPRESS_SPLASH_SCREEN'] = '1'
    environment[INSTALL_ENV] = str(install)
    if job_path is not None:
        job = load_job(job_path)
        environment[RESUME_ENV] = str(job_path)
        environment[guard.ENV_TICKET] = job['ticket']
        environment[guard.ENV_ATTEMPT] = job['attempt']
        environment[guard.ENV_ROOT] = job['data']
    else:
        environment.pop(RESUME_ENV, None)
        environment.pop(guard.ENV_TICKET, None)
        environment.pop(guard.ENV_ATTEMPT, None)
    return subprocess.Popen([str(executable)], cwd=str(Path(executable).parent), env=environment,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)


def route_startup():
    """The original EXE stays a stable entry point, including existing shortcuts."""
    if not getattr(sys, 'frozen', False):
        return True
    install = Path(os.environ.get(INSTALL_ENV) or Path(sys.executable).parent).resolve()
    if not os.environ.get(RESUME_ENV):
        state = guard.read()
        if state is not None:
            from program_update_helper import recover_entry
            if not recover_entry(state):
                return False
    pointer = install / POINTER
    if pointer.exists():
        selection = read(pointer)
        entry = contained(install, selection.get('entry_file', GUI))
        if (selection.get('entry_sha256') and Path(sys.executable).resolve() == entry.resolve()
                and digest_file(entry) != selection['entry_sha256']):
            # A user manually copied a replacement entry EXE. Preserve the old
            # selection as a record instead of silently routing to an older EXE.
            pointer.replace(contained(install, 'program_active.manual_' + uuid.uuid4().hex + '.json'))
            return True
    target = target_from_pointer(install)
    if target is not None and target.resolve() != Path(sys.executable).resolve():
        spawn_gui(target, install)
        return False
    return True

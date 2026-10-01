"""Season metadata and explicit management of season-owned directories."""
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from archive_storage import season_directory
from runtime_storage import atomic_write


def _check_delete_path(path, target, root):
    path = Path(path)
    if not path.resolve().is_relative_to(target.resolve()) or not path.resolve().is_relative_to(root):
        raise ValueError('선택한 시즌 밖의 경로는 삭제할 수 없습니다.')
    attributes = path.lstat()
    if stat.S_ISLNK(attributes.st_mode) or (
            getattr(attributes, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
        raise ValueError('링크가 포함된 시즌 폴더는 삭제할 수 없습니다.')
    return attributes


def _reset_delete_access(path, target, root, check_active):
    """Repair only a denied item inside an explicitly selected deletion tree."""
    _check_delete_path(path, target, root)
    check_active()
    if os.name != 'nt':
        raise PermissionError(13, '시즌 폴더 접근 권한이 없습니다.', str(path))
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'icacls.exe'
    try:
        # No recursive flag: inspect each child for links before touching its ACL.
        result = subprocess.run(
            [str(executable), str(path), '/reset', '/Q', '/L'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PermissionError(13, '시즌 폴더 권한 복구에 실패했습니다.', str(path)) from exc
    if result.returncode:
        raise PermissionError(13, '시즌 폴더 권한 복구에 관리자 권한이 필요합니다.', str(path))


def _prepare_delete_tree(target, root, check_active):
    """Use scandir so inaccessible children cannot be silently skipped by rglob."""
    pending = [target]
    while pending:
        path = pending.pop()
        attributes = _check_delete_path(path, target, root)
        if not stat.S_ISDIR(attributes.st_mode):
            continue
        try:
            with os.scandir(path) as entries:
                children = [Path(entry.path) for entry in entries]
        except PermissionError:
            _reset_delete_access(path, target, root, check_active)
            with os.scandir(path) as entries:
                children = [Path(entry.path) for entry in entries]
        pending.extend(children)


def remove_season_directory(target, data_root, *, check_active=lambda: None, prepared=False):
    """Delete after confirmation; handle read-only files and legacy Windows ACLs."""
    target, root = Path(target), Path(data_root).resolve()
    if target.resolve() == root or not target.resolve().is_relative_to(root):
        raise ValueError('사용자 데이터 밖의 시즌 폴더는 삭제할 수 없습니다.')
    if not prepared:
        _prepare_delete_tree(target, root, check_active)

    def retry_delete(function, path, error_info):
        error = error_info[1]
        if not isinstance(error, PermissionError) or function not in (os.unlink, os.remove, os.rmdir):
            raise error
        if getattr(error, 'winerror', None) not in (None, 5):
            raise error
        attributes = _check_delete_path(path, target, root)
        check_active()
        readonly = os.name == 'nt' and getattr(attributes, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_READONLY
        if readonly:
            try:
                os.chmod(path, stat.S_IWRITE)
                function(path)
                return
            except PermissionError as retry_error:
                if getattr(retry_error, 'winerror', None) not in (None, 5):
                    raise
        _reset_delete_access(path, target, root, check_active)
        if readonly:
            os.chmod(path, stat.S_IWRITE)
        function(path)

    check_active()
    shutil.rmtree(target, onerror=retry_delete)


def season_number(value):
    text = str(value if value is not None else '').strip()
    if not re.fullmatch(r'\d+', text):
        raise ValueError('시즌 번호는 숫자로 입력하세요.')
    return str(int(text))


def profile_season_directory(data_root, number):
    root = Path(data_root).resolve()
    profiles = root / 'server_profiles'
    if not profiles.resolve().is_relative_to(root):
        raise ValueError('시즌 설정 폴더가 사용자 데이터 밖을 가리킵니다.')
    return Path(season_directory(profiles, 'season_' + season_number(number)))


def read_season_entry(data_root, number):
    path = profile_season_directory(data_root, number) / 'season_info.json'
    if not path.is_file():
        return {}
    if path.is_symlink() or not path.resolve().is_relative_to(path.parent.resolve()):
        raise ValueError('시즌 정보 파일 경로를 확인할 수 없습니다.')
    entry = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(entry, dict) or season_number(entry.get('season_no')) != season_number(number):
        raise ValueError('저장된 시즌 정보 형식을 확인할 수 없습니다.')
    return entry


def save_season_entry(data_root, entry, label, *, server_id=''):
    number = season_number(entry.get('season_no'))
    root = Path(data_root).resolve()
    profile = profile_season_directory(root, number)
    archive_root = root / 'archive_logs'
    if not archive_root.resolve().is_relative_to(root):
        raise ValueError('시즌 기록 폴더가 사용자 데이터 밖을 가리킵니다.')
    archive = Path(season_directory(archive_root, label))
    previous = read_season_entry(root, number)
    profile.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    payload = dict(previous)
    payload.update(entry)
    payload['season_no'] = number
    aliases = previous.get('archive_labels', [])
    if not isinstance(aliases, list) or not all(isinstance(value, str) for value in aliases):
        raise ValueError('시즌 폴더 이력을 확인할 수 없습니다.')
    payload['archive_labels'] = list(dict.fromkeys([*aliases, label]))
    if server_id:
        payload['last_server_id'] = str(server_id)
    atomic_write(profile / 'season_info.json',
                 (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return profile, archive


def delete_season_directories(data_root, number, labels, *, active_season):
    """Called only after typed UI confirmation; global/default data is excluded."""
    number = season_number(number)
    if str(active_season if active_season is not None else '').strip() and number == season_number(active_season):
        raise ValueError('현재 시즌은 삭제할 수 없습니다.')
    root = Path(data_root).resolve()
    selection = root / 'active_server_profile.json'

    def check_active():
        if selection.is_file():
            stored = json.loads(selection.read_text(encoding='utf-8-sig'))
            if not isinstance(stored, dict):
                raise ValueError('사용 중인 시즌 정보를 확인할 수 없어 삭제하지 않습니다.')
            if stored.get('season_key') == 'season_' + number:
                raise ValueError('사용 중인 서버 프로필의 시즌은 삭제할 수 없습니다.')

    check_active()
    targets = []
    info = read_season_entry(root, number)
    aliases = info.get('archive_labels', [])
    if not isinstance(aliases, list) or not all(isinstance(value, str) for value in aliases):
        raise ValueError('시즌 폴더 이력을 확인할 수 없습니다.')
    labels = list(dict.fromkeys([*labels, *aliases]))
    for dirname in ('archive_logs', 'season_prestart_logs'):
        managed_root = root / dirname
        if not managed_root.resolve().is_relative_to(root):
            raise ValueError('시즌 폴더가 사용자 데이터 밖을 가리킵니다.')
        discovered = []
        if managed_root.is_dir():
            for path in managed_root.iterdir():
                matches = re.findall(r'(\d+)\s*(?:차)?\s*시즌(?:\s|$)', path.name)
                if matches and str(int(matches[-1])) == number:
                    discovered.append(path.name)
        for label in dict.fromkeys([*labels, *discovered]):
            matches = re.findall(r'(\d+)\s*(?:차)?\s*시즌(?:\s|$)', label)
            if matches and str(int(matches[-1])) != number:
                raise ValueError('다른 시즌의 폴더는 함께 삭제할 수 없습니다.')
            targets.append(Path(season_directory(managed_root, label)))
    # Keep the settings/schedule directory until all record folders are removed.
    targets.append(profile_season_directory(root, number))
    # Validate all roots before attempting even a permission repair.
    for target in targets:
        if not target.resolve().is_relative_to(root):
            raise ValueError('사용자 데이터 밖의 시즌 폴더는 삭제할 수 없습니다.')
        if target.exists():
            if not target.is_dir():
                raise ValueError('시즌 경로가 폴더가 아니므로 삭제하지 않습니다.')
            _check_delete_path(target, target, root)
    # Resolve permission failures before deleting any files or central history.
    for target in targets:
        if target.is_dir():
            _prepare_delete_tree(target, root, check_active)
    for target in targets:
        if target.is_dir():
            remove_season_directory(target, root, check_active=check_active, prepared=True)

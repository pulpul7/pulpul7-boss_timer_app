"""Cross-process maintenance fence. An unreadable fence fails closed."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import uuid

from runtime_storage import atomic_write
from web_service_common import file_transaction

ENV_ROOT = 'BOSS_TIMER_UPDATE_ROOT'
ENV_TICKET = 'BOSS_TIMER_UPDATE_TICKET'
ENV_ATTEMPT = 'BOSS_TIMER_UPDATE_ATTEMPT'
_lock = threading.RLock()


def data_root():
    return Path(os.environ.get(ENV_ROOT) or
                Path(os.environ.get('APPDATA') or os.environ.get('LOCALAPPDATA') or Path.home()) / 'BossTimer').resolve()


def fence_path(root=None):
    return Path(root or data_root()) / 'update_program' / 'maintenance.json'


def read(root=None):
    try:
        value = json.loads(fence_path(root).read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('schema') != 1
                or any(not isinstance(value.get(key), str) or len(value[key]) != 32
                       or any(c not in '0123456789abcdef' for c in value[key]) for key in ('ticket', 'attempt'))
                or not isinstance(value.get('transaction'), str)):
            raise ValueError('invalid maintenance fence')
        return value
    except FileNotFoundError:
        return None


def blocked(root=None):
    try:
        state = read(root)
        return bool(state and not (state.get('phase') == 'verifying'
                    and state['ticket'] == os.environ.get(ENV_TICKET)
                    and state.get('attempt') == os.environ.get(ENV_ATTEMPT)))
    except (OSError, ValueError, TypeError, AttributeError):
        return True


def active(root=None):
    try:
        return read(root) is not None
    except (OSError, ValueError, TypeError):
        return True


def begin(root, ticket, transaction, *, check=None, owner=None):
    path = fence_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, file_transaction(path):
        if read(root) is not None:
            raise ValueError('이미 전체 업데이트 또는 복구가 진행 중입니다.')
        if check is not None:
            check()  # Recheck accepted web work while no new work can register.
        attempt = uuid.uuid4().hex
        atomic_write(path, json.dumps(dict(schema=1, ticket=ticket, attempt=attempt, owner=owner, phase='stopping',
                                          transaction=str(transaction)), ensure_ascii=False).encode('utf-8'))
        return attempt


def phase(root, ticket, value, *, attempt=None):
    path = fence_path(root)
    with _lock, file_transaction(path):
        state = read(root)
        if state is None or state['ticket'] != ticket or (attempt is not None and state.get('attempt') != attempt):
            raise ValueError('업데이트 차단 상태가 변경되었습니다.')
        state['phase'] = value
        atomic_write(path, json.dumps(state, ensure_ascii=False).encode('utf-8'))


def finish(root, ticket, *, attempt=None):
    path = fence_path(root)
    with _lock, file_transaction(path):
        state = read(root)
        if state is not None and (state['ticket'] != ticket or (attempt is not None and state.get('attempt') != attempt)):
            raise ValueError('다른 업데이트의 차단 상태는 해제하지 않습니다.')
        path.unlink(missing_ok=True)


def matches(root, ticket, attempt):
    try:
        state = read(root)
        return state is not None and state['ticket'] == ticket and state.get('attempt') == attempt
    except (OSError, ValueError, TypeError):
        return False


def require_available(root=None):
    if blocked(root):
        raise ValueError('전체 업데이트 중에는 봇·웹의 시작과 자동 복구가 차단됩니다.')


def require_idle(root=None):
    if active(root):
        raise ValueError('전체 업데이트가 완료될 때까지 웹 변경 작업을 기다려주세요.')


@contextmanager
def launch_gate(root=None, *, allow_trial=True):
    """Serialize a spawn with fence creation, including already queued starts."""
    path = fence_path(root)
    with _lock, file_transaction(path):
        (require_available if allow_trial else require_idle)(root)
        yield


@contextmanager
def web_change(manager):
    """Register long web work briefly; bot launches remain independent."""
    root = manager.root.parent
    with launch_gate(root, allow_trial=False):
        manager._program_update_work = getattr(manager, '_program_update_work', 0) + 1
    try:
        yield
    finally:
        with _lock, file_transaction(fence_path(root)):
            manager._program_update_work -= 1

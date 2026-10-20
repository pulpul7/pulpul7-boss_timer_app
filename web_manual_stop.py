"""Durable, exact-worker fence for an explicit system-tray shutdown."""
from pathlib import Path
import re
import time

from web_service_common import read_json, write_json, file_transaction


def manual_stop(root, run_id=None):
    """Ignore old stop requests once a different persistent worker owns this root."""
    root = Path(root)
    try:
        item = read_json(root / 'tray_stop.json')
        current = read_json(root / 'persistent_worker.json')
        if (item.get('schema') == 1 and item.get('source') == 'system_tray'
                and re.fullmatch('[0-9a-f]{32}', str(item.get('run_id', '')))
                and type(item.get('pid')) is int and item['pid'] > 0
                and type(item.get('requested_at')) in (int, float)
                and all(item.get(k) == current.get(k) for k in ('run_id', 'pid'))
                and (not run_id or item['run_id'] == run_id)):
            return item
    except (OSError, ValueError):
        pass
    return None


def request_manual_stop(root, run_id, pid):
    root = Path(root)
    with file_transaction(root / 'tray_stop.guard'):
        current = read_json(root / 'persistent_worker.json')
        if current.get('run_id') != run_id or current.get('pid') != pid:
            raise ValueError('web worker identity changed')
        item = dict(schema=1, source='system_tray', run_id=run_id, pid=pid,
                    requested_at=time.time(), completed=False)
        write_json(root / 'tray_stop.json', item)
    return item


def complete_manual_stop(root, run_id, *, shared_confirmed):
    root = Path(root)
    with file_transaction(root / 'tray_stop.guard'):
        item = manual_stop(root, run_id)
        if item:
            item.update(completed=True, completed_at=time.time(), shared_confirmed=shared_confirmed)
            write_json(root / 'tray_stop.json', item)


def clear_manual_stop(root):
    """Only called by an explicit Web Start, never by recovery/reattachment."""
    root = Path(root)
    with file_transaction(root / 'tray_stop.guard'):
        item = manual_stop(root)
        if item and not item.get('completed'):
            # A live worker still owns the listener/router cleanup. Do not race it.
            from web_persistent_runtime import AttachedProcess
            try:
                process = AttachedProcess(item['pid'])
            except OSError as exc:
                if getattr(exc, 'winerror', None) not in (87, 1168):
                    raise
                process = None  # Exact PID no longer exists; no process was killed.
            try:
                if process is not None and process.poll() is None:
                    raise ValueError('트레이에서 웹서버를 종료 중입니다. 종료 후 다시 실행해주세요.')
            finally:
                if process is not None:
                    process.close()
        (root / 'tray_stop.json').unlink(missing_ok=True)

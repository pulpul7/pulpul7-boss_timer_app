"""Exact Windows process identities for updater-owned bot cleanup."""
from dataclasses import asdict
import ctypes
import os
from pathlib import Path
import time

from discord_bot_process_recovery import BotProcess, _kernel, _inspect, _snapshot, _path, _identity, terminate_stale_bots


def identity(pid):
    if os.name != 'nt':
        raise OSError('전체 업데이트 프로세스 제어는 Windows에서만 지원합니다.')
    value = _inspect(_kernel(), int(pid))
    if value is None:
        raise OSError('프로세스 실행 경로와 생성 시각을 확인하지 못했습니다.')
    return asdict(value)


def alive(value):
    if not value:
        return False
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, int(value['pid']))
    if not handle:
        if ctypes.get_last_error() == 87:
            return False
        raise OSError(ctypes.get_last_error(), '프로세스 종료 여부를 확인할 권한이 없습니다.')
    try:
        result = kernel.WaitForSingleObject(handle, 0)
        if result == 0:
            return False
        if result != 0x102:
            raise OSError('프로세스 종료 확인에 실패했습니다.')
        current = _identity(kernel, handle)
        if current is None:
            raise OSError('프로세스 실행 경로를 다시 확인하지 못했습니다.')
        return _path(current[0]) == _path(value['path']) and current[1] == value['created']
    finally:
        kernel.CloseHandle(handle)


def wait_exited(value, timeout, *, cancelled=None):
    deadline = time.monotonic() + timeout
    while alive(value):
        if cancelled is not None and cancelled():
            raise ValueError('이전 업데이트 시도가 취소되었습니다.')
        if time.monotonic() >= deadline:
            raise ValueError('이전 프로그램의 종료를 확인하지 못했습니다. 업데이트를 중단했습니다.')
        time.sleep(.1)
    if cancelled is not None and cancelled():
        raise ValueError('이전 업데이트 시도가 취소되었습니다.')


def capture_bots(expected_path, roots):
    kernel = _kernel()
    records = _snapshot(kernel)
    roots = {int(pid) for pid in roots if pid}
    found = {}
    for _ in range(len(records) + 1):
        changed = False
        for pid, (parent, name) in records.items():
            if pid in found or name.lower() != Path(expected_path).name.lower():
                continue
            process = _inspect(kernel, pid, parent)
            if not process or _path(process.path) != _path(expected_path):
                continue
            orphan = parent not in records
            if parent in records and pid not in roots and parent not in found:
                parent_process = _inspect(kernel, parent)
                orphan = bool(parent_process and parent_process.created > process.created)
            if pid in roots or parent in found or orphan:
                found[pid] = process
                changed = True
        if not changed:
            break
    return [asdict(process) for process in found.values()]


def reap_bots(values, expected_path):
    """Recheck path + creation time. A reused PID is already a different process."""
    selected = []
    for value in values:
        if _path(value['path']) != _path(expected_path) or Path(value['path']).name.lower() not in {'boss_timer_discord_bot.exe', 'discord_bot.exe'}:
            raise ValueError('업데이트 대상 봇의 실행 경로가 다릅니다.')
        if alive(value):
            selected.append(BotProcess(**value))
    if selected and not terminate_stale_bots(selected):
        raise ValueError('이전 봇 프로세스가 남아 있어 새 봇을 실행하지 않았습니다.')


def stop_trial(value):
    """Only the replacement GUI's recorded process may be stopped on rollback."""
    if value and alive(value) and not terminate_stale_bots([BotProcess(**value)]):
        raise ValueError('새 프로그램의 종료를 확인하지 못해 복원을 보류했습니다.')


def trial_processes(destination):
    """A unique, uncommitted version folder cannot contain the persistent old web."""
    kernel = _kernel()
    expected = {_path(Path(destination) / name) for name in ('boss_timer_gui.exe', 'boss_timer_discord_bot.exe')}
    result = []
    for pid, (parent, name) in _snapshot(kernel).items():
        if name.lower() not in {'boss_timer_gui.exe', 'boss_timer_discord_bot.exe'}:
            continue
        process = _inspect(kernel, pid, parent)
        if process and _path(process.path) in expected:
            result.append(asdict(process))
    return sorted(result, key=lambda value: value['created'], reverse=True)

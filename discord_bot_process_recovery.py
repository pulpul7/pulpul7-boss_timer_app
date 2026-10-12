"""Identify and stop leftover packaged bots without touching other applications.

Windows process handles are checked again at termination time, so a recycled PID
cannot make recovery terminate a different process. No tokens or command lines
are read. The GUI calls this only while starting/recovering its local bot.
"""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import ntpath
import os


@dataclass(frozen=True)
class BotProcess:
    pid: int
    parent_pid: int
    path: str
    created: int


def _path(value):
    return ntpath.normcase(ntpath.normpath(str(value or "")))


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "QueryFullProcessImageNameW": ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                       ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "GetProcessTimes": ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4,
                            wintypes.BOOL),
        "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    return kernel


def _identity(kernel, handle):
    buffer = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buffer))
    if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return None
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
        return None
    created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    return buffer.value, created


def _inspect(kernel, pid, parent_pid=0):
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        identity = _identity(kernel, handle)
        return BotProcess(pid, parent_pid, *identity) if identity else None
    finally:
        kernel.CloseHandle(handle)


def _snapshot(kernel):
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(kernel, name)
        function.argtypes, function.restype = [wintypes.HANDLE, ctypes.POINTER(Entry)], wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Process snapshot failed")
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        records = {}
        available = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while available:
            records[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            available = kernel.Process32NextW(handle, ctypes.byref(entry))
        return records
    finally:
        kernel.CloseHandle(handle)


def select_stale_bots(bots, live_pids, inspect_parent, *, payload=None,
                      config_path="", client_id="", current_pid=0, owned_pid=0,
                      allow_current_parent=False):
    """Pure selection logic. Keep bots attached to another live GUI untouched."""
    payload = payload or {}
    try:
        listener_pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        listener_pid = 0
    if payload.get("ok") and listener_pid <= 0:
        return []
    same_client = bool(client_id and payload.get("client_id") == client_id)
    same_profile = bool(config_path and payload.get("config_path")
                        and _path(payload["config_path"]) == _path(config_path))
    selected = {}
    for process in bots.values():
        root = process
        seen = {root.pid}
        while root.parent_pid in bots:
            parent = bots[root.parent_pid]
            if parent.pid in seen or _path(parent.path) != _path(root.path) or parent.created > root.created:
                break
            seen.add(parent.pid)
            root = parent
        if current_pid in seen or (owned_pid in seen and not listener_pid):
            continue
        if root.parent_pid in live_pids:
            parent = inspect_parent(root.parent_pid)
            # Access denied is not evidence that a parent died.
            if parent is None:
                continue
            parent_gone = parent.created > root.created
            owned_listener = (allow_current_parent and root.parent_pid == current_pid and listener_pid in seen
                              and (same_client or same_profile))
            if not parent_gone and not owned_listener:
                continue
        # A mismatched listener is recoverable only if it really belongs to this
        # orphan bot family. With no listener, reclaim orphan packaged bots only.
        if listener_pid and listener_pid not in seen:
            continue
        selected[root.pid] = root
        selected[process.pid] = process
    # Stop onefile children before their bootloader, avoiding detached survivors.
    return sorted(selected.values(), key=lambda item: item.created, reverse=True)


def find_stale_bots(*, payload=None, config_path="", client_id="", owned_pid=0,
                    expected_executable="", allow_current_parent=False):
    if os.name != "nt":
        return []
    kernel = _kernel()
    records = _snapshot(kernel)
    bots = {}
    for pid, (parent_pid, name) in records.items():
        if name.lower() not in ("boss_timer_discord_bot.exe", "discord_bot.exe"):
            continue
        process = _inspect(kernel, pid, parent_pid)
        if process is None:
            continue
        basename = ntpath.basename(process.path).lower()
        if basename != "boss_timer_discord_bot.exe" and _path(process.path) != _path(expected_executable):
            continue  # A generic discord_bot.exe may belong to another program.
        bots[pid] = process
    return select_stale_bots(bots, set(records), lambda pid: _inspect(kernel, pid),
                             payload=payload, config_path=config_path, client_id=client_id,
                             current_pid=os.getpid(), owned_pid=owned_pid,
                             allow_current_parent=allow_current_parent)


def terminate_stale_bots(processes):
    """Return only after each verified process exited; never kill by filename."""
    kernel = _kernel()
    for process in processes:
        handle = kernel.OpenProcess(0x1000 | 0x100000 | 1, False, process.pid)
        if not handle:
            # It may have exited during graceful shutdown. Access denied fails.
            if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER
                continue
            return False
        try:
            identity = _identity(kernel, handle)
            if not identity or _path(identity[0]) != _path(process.path) or identity[1] != process.created:
                return False
            if kernel.WaitForSingleObject(handle, 0) == 0:
                continue
            if not kernel.TerminateProcess(handle, 0):
                return False
            if kernel.WaitForSingleObject(handle, 3000) != 0:
                return False
        finally:
            kernel.CloseHandle(handle)
    return True

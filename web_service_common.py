"""Private web-service storage and diagnostics. No GUI/Discord imports."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import time
import uuid

PROTOCOL = 3
PHASE = 7
DEFAULT_PORT = 8765
WORKER_FLAG = "--boss-timer-web-worker"
ACCESS_DISPLAY_DAYS = 7
ACCESS_RETENTION_DAYS = 30


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_json(path: Path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
        # Readers outside this process (including antivirus) can briefly deny
        # replacement on Windows. Keep the old complete file and retry briefly.
        with file_transaction(path):
            for attempt in range(5):
                try:
                    os.replace(temporary, path)
                    break
                except OSError as exc:
                    if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 4:
                        raise
                    time.sleep(0.02 * (attempt + 1))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # Cleanup must not turn a successful publication into a failure.


@contextmanager
def file_transaction(path: Path):
    """Coordinate this module's Windows readers/writers across GUI and worker."""
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    identity = hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()
    handle = kernel.CreateMutexW(None, False, "Local\\BossTimerWebJSON-" + identity)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, 1000)
        if result == 0x102:
            raise TimeoutError("web file transaction busy")
        if result not in {0, 0x80}:  # WAIT_OBJECT_0 / WAIT_ABANDONED
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


@contextmanager
def open_shared_read(path: Path):
    """Finish reading a snapshot before another process replaces its file."""
    with file_transaction(path):
        with path.open("rb") as stream:
            yield stream


def read_json(path: Path):
    with open_shared_read(path) as stream:
        content = stream.read()
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("invalid web-service document")
    return result


def diagnostic_logger(root: Path, component: str):
    logger = logging.Logger(f"boss_timer.web.{component}.{uuid.uuid4().hex}")
    logger.setLevel(logging.INFO)
    try:
        root.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(root / f"{component}.log", maxBytes=262144,
                                      backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
    except OSError:
        handler = logging.NullHandler()
    logger.addHandler(handler)
    return logger


def diagnostic(logger, event, **fields):
    # Explicit fields only: never log HTTP queries, headers, cookies or tokens.
    try:
        logger.info(json.dumps(dict(at=timestamp(), phase=PHASE, event=event, **fields),
                               ensure_ascii=False, separators=(",", ":")))
    except Exception:
        pass


def close_logger(logger):
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def worker_paths(root: Path, run_id: str):
    if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise ValueError("invalid web-service session")
    return root / f"runtime_{run_id}.json", root / f"stop_{run_id}"


def schedule_path(root: Path, run_id: str):
    worker_paths(root, run_id)  # Validate before constructing any session path.
    return root / f"schedule_{run_id}.json"


def access_state_path(root: Path, run_id: str):
    worker_paths(root, run_id)
    return root / f"access_{run_id}.json"

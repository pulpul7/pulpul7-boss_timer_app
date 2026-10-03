"""Local connection ownership state; no tokens or schedule data are stored here."""
from contextlib import contextmanager
from functools import lru_cache
import json
import os
from pathlib import Path
import tempfile
import time

MAX_RETRIES = 10
CONTROL_PROTOCOL = 2
AUTHORITY_SECONDS = 10


@lru_cache(maxsize=1)
def _windows_file_api():
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _read_state_text(path):
    if os.name != "nt":
        return path.read_text(encoding="utf-8")
    import ctypes
    import msvcrt
    api = _windows_file_api()
    # AudioPlayer and the GUI read this file frequently. Allow atomic replace
    # while a reader holds the old file; ordinary CRT opens deny deletion.
    handle = api.CreateFileW(str(path.resolve()), 0x80000000, 0x7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.WinError(ctypes.get_last_error())
        error.filename = str(path)
        raise error
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        api.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, "r", encoding="utf-8") as reader:
        return reader.read()


class ConnectionPolicy:
    def __init__(self, config_path):
        self.path = Path(str(config_path) + ".connection.json")

    def snapshot(self, *, strict=False):
        try:
            data = json.loads(_read_state_text(self.path))
            if not isinstance(data, dict):
                raise ValueError("invalid connection state")
            return dict(standby=bool(data.get("standby", True)),
                        handover_hold=bool(data.get("handover_hold", False)),
                        handover_role=str(data.get("handover_role", "")),
                        client_id=str(data.get("client_id", "")),
                        retries=max(0, min(MAX_RETRIES, int(data.get("retries", 0)))),
                        error=str(data.get("error", "")), next_retry=float(data.get("next_retry", 0)),
                        protocol=int(data.get("protocol", 0)),
                        runtime_id=str(data.get("runtime_id", "")),
                        authority_generation=int(data.get("authority_generation", 0)),
                        authority_request=str(data.get("authority_request", "")),
                        blocked_request=str(data.get("blocked_request", "")),
                        authority_active=bool(data.get("authority_active", False)),
                        authority_until=float(data.get("authority_until", 0)),
                        voice_requested=bool(data.get("voice_requested", False)),
                        voice_channel_id=str(data.get("voice_channel_id", "")),
                        voice_error=str(data.get("voice_error", "")),
                        voice_retries=int(data.get("voice_retries", 0)),
                        voice_next_retry=float(data.get("voice_next_retry", 0)),
                        command_sync_allowed=bool(data.get("command_sync_allowed", False)),
                        query_members=[dict(member) for member in data.get("query_members", []) if isinstance(member, dict)],
                        query_until=float(data.get("query_until", 0)),
                        query_responder=dict(data.get("query_responder") or {}),
                        query_routing_generation=int(data.get("query_routing_generation", -1)),
                        query_protocol=int(data.get("query_protocol", 0)))
        except FileNotFoundError:
            return self._empty()
        except (OSError, ValueError, TypeError):
            if strict:
                raise  # A failed read must never overwrite valid stored state.
            return dict(self._empty(), retries=MAX_RETRIES, handover_hold=True,
                        error="연결 상태 파일을 읽지 못했습니다. 확인 후 수동으로 봇을 실행하세요.")

    @staticmethod
    def _empty():
        return dict(standby=True, retries=0, error="", next_retry=0,
                    handover_hold=False, handover_role="", client_id="", protocol=0,
                    runtime_id="", authority_generation=0, authority_until=0, authority_active=False,
                    authority_request="", blocked_request="",
                    voice_requested=False, voice_channel_id="", voice_error="",
                    voice_retries=0, voice_next_retry=0, command_sync_allowed=False,
                    query_members=[], query_until=0, query_responder={}, query_routing_generation=-1, query_protocol=0)

    @staticmethod
    def has_authority(state, runtime_id=None, *, allow_joining=False):
        # monotonic() uses the same system clock across local processes. A new
        # runtime ID prevents a restarted bot from inheriting an old lease.
        return bool(state.get("protocol") == CONTROL_PROTOCOL
                    and state.get("runtime_id")
                    and (runtime_id is None or state["runtime_id"] == runtime_id)
                    and not state.get("standby") and not state.get("handover_hold")
                    and (allow_joining or state.get("authority_active"))
                    and state.get("authority_until", 0) > time.monotonic())

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path) + ".lock", "a+b") as handle:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                # Non-blocking lock with a bounded wait, never indefinite.
                deadline = time.monotonic() + 1.0
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.01)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _save(self, data):
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            replace_deadline = time.monotonic() + .3
            while True:
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    if time.monotonic() >= replace_deadline:
                        raise
                    time.sleep(.01)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def update(self, **values):
        with self._locked():
            data = self.snapshot(strict=True)
            if ("query_routing_generation" in values
                    and data.get("query_routing_generation", -1) > values["query_routing_generation"]):
                # A delayed GUI poll cannot replace a newer failover hint from
                # the bot. Still refresh membership from the normal poll.
                from discord_query_routing import QueryLedger
                preferred = data.get("query_responder") or {}
                values.update(query_responder=preferred,
                              query_routing_generation=data["query_routing_generation"])
                if "query_members" in values:
                    values["query_members"] = QueryLedger.order_candidates(values["query_members"], preferred)
            data.update(values)
            self._save(data)
            return data

    def resume(self):
        # Manual Gateway retry never grants administrator authority.
        return self.update(retries=0, error="", next_retry=0,
                           voice_error="", voice_retries=0, voice_next_retry=0)

    def pause(self):
        return self.update(standby=True, authority_until=0, voice_requested=False,
                           handover_hold=False, handover_role="")

    def block_request(self, request_id="", runtime=None):
        """Persist an explicit release/failure across all GUI windows on this PC."""
        with self._locked():
            data = self.snapshot(strict=True)
            request_id = request_id or data["authority_request"]
            if request_id:
                data["blocked_request"] = request_id
            if (runtime is None or data["runtime_id"] == runtime) and request_id == data["authority_request"]:
                data.update(standby=True, authority_until=0, authority_active=False, voice_requested=False)
            self._save(data)

    def success(self):
        with self._locked():
            data = self.snapshot(strict=True)
            if not data["error"] and data["retries"]:
                data.update(retries=0, next_retry=0)
                self._save(data)
            return data

    def claim_retry(self, now=None):
        now = time.time() if now is None else now
        with self._locked():
            data = self.snapshot(strict=True)
            if (data["error"] or data["retries"] >= MAX_RETRIES
                    or now < data["next_retry"]):
                return False
            data.update(retries=data["retries"] + 1, next_retry=now + 5)
            self._save(data)
            return True

    def claim_voice_retry(self):
        with self._locked():
            data = self.snapshot(strict=True)
            now = time.monotonic()
            if self.has_authority(data) and not data["voice_error"] and data["voice_retries"] >= MAX_RETRIES:
                data["voice_error"] = "음성 재접속 10회 제한에 도달했습니다. 디코 실행 버튼으로 다시 요청하세요."
                self._save(data)
                return False
            if (not self.has_authority(data) or data["voice_error"]
                    or data["voice_retries"] >= MAX_RETRIES or now < data["voice_next_retry"]):
                return False
            data.update(voice_retries=data["voice_retries"] + 1, voice_next_retry=now + 5)
            self._save(data)
            return True


def is_access_error(exc):
    return getattr(exc, "status", None) == 403 or getattr(exc, "code", None) in (50001, 50013)


ACCESS_ERROR_TEXT = ("디스코드 접근 권한이 없어 자동 재접속을 중단했습니다. "
                     "봇 초대, 서버·채널 ID, 채널 보기·연결·말하기 권한을 확인한 뒤 수동으로 접속하세요.")

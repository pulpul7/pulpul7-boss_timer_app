"""Local connection ownership state; no tokens or schedule data are stored here."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time

MAX_RETRIES = 10


class ConnectionPolicy:
    def __init__(self, config_path):
        self.path = Path(str(config_path) + ".connection.json")

    def snapshot(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("invalid connection state")
            return dict(standby=bool(data.get("standby", False)),
                        handover_hold=bool(data.get("handover_hold", False)),
                        handover_role=str(data.get("handover_role", "")),
                        client_id=str(data.get("client_id", "")),
                        retries=max(0, min(MAX_RETRIES, int(data.get("retries", 0)))),
                        error=str(data.get("error", "")), next_retry=float(data.get("next_retry", 0)))
        except FileNotFoundError:
            return dict(standby=False, retries=0, error="", next_retry=0,
                        handover_hold=False, handover_role="", client_id="")
        except (OSError, ValueError, TypeError):
            return dict(standby=True, retries=MAX_RETRIES,
                        handover_hold=True, handover_role="error", client_id="",
                        error="연결 상태 파일을 읽지 못했습니다. 확인 후 수동으로 봇을 실행하세요.", next_retry=0)

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
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def update(self, **values):
        with self._locked():
            data = self.snapshot()
            data.update(values)
            self._save(data)
            return data

    def resume(self):
        return self.update(standby=False, retries=0, error="", next_retry=0)

    def pause(self):
        return self.update(standby=True)

    def success(self):
        with self._locked():
            data = self.snapshot()
            if not data["standby"] and not data["error"] and data["retries"]:
                data.update(retries=0, next_retry=0)
                self._save(data)
            return data

    def claim_retry(self, now=None):
        now = time.time() if now is None else now
        with self._locked():
            data = self.snapshot()
            if (data["standby"] or data["error"] or data["handover_role"] == "outgoing" or data["retries"] >= MAX_RETRIES
                    or now < data["next_retry"]):
                return False
            data.update(retries=data["retries"] + 1, next_retry=now + 5)
            self._save(data)
            return True


def is_access_error(exc):
    return getattr(exc, "status", None) == 403 or getattr(exc, "code", None) in (50001, 50013)


ACCESS_ERROR_TEXT = ("디스코드 접근 권한이 없어 자동 재접속을 중단했습니다. "
                     "봇 초대, 서버·채널 ID, 채널 보기·연결·말하기 권한을 확인한 뒤 수동으로 접속하세요.")

"""Local notice synthesis/player adapter, not yet enabled by the application.

The future host must acquire a boss lease BEFORE output and release it only
after all that output ends. Passing a periodically sampled busy flag is not
enough. A failed begin_boss() means silence is unconfirmed; fail closed.
"""
from dataclasses import dataclass, field
import threading

from .notice_local_player import LocalPlayer


@dataclass(eq=False)
class LocalHandle:
    owner: object
    cancel: threading.Event = field(default_factory=threading.Event)
    state: str = "preparing"
    player: object = None
    synth: object = None
    worker: object = None
    error: str = ""


class LocalNoticeTransport:
    notice_audio_protocol = 1

    def __init__(self, synthesizer, *, player_factory=LocalPlayer):
        self.synthesizer = synthesizer
        self.player_factory = player_factory
        self.lock = threading.RLock()
        self.handles = set()
        self.boss_leases = set()
        self.closed = False
        self.fault = False

    def _check(self, handle):
        if not isinstance(handle, LocalHandle) or handle.owner is not self:
            raise ValueError("다른 출력 경로의 알리미 요청입니다.")

    def prepare(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError("읽을 문장을 1~10,000자로 입력해 주세요.")
        with self.lock:
            if self.closed or self.fault or self.handles:
                raise RuntimeError("알리미 출력이 종료되었거나 이전 요청이 남아 있습니다.")
            handle = LocalHandle(self)
            self.handles.add(handle)
            handle.worker = threading.Thread(target=self._prepare, args=(handle, text.strip()),
                                             name="notice-local-prepare", daemon=True)
            handle.worker.start()
            return handle

    def prepare_prepared(self, lease):
        """Open a pre-generated file silently; no synthesizer/network call."""
        with self.lock:
            if self.closed or self.fault or self.handles:
                raise RuntimeError('알리미 출력이 종료되었거나 이전 요청이 남아 있습니다.')
            handle = LocalHandle(self)
            self.handles.add(handle)
            handle.worker = threading.Thread(target=self._prepare, args=(handle, None, lease),
                                             name='notice-local-open', daemon=True)
            try:
                handle.worker.start()
            except Exception:
                self.handles.discard(handle)
                raise
            return handle

    @staticmethod
    def _cleanup(synth):
        if synth is not None:
            try:
                synth.stop()  # The factory must provide an isolated temporary cache.
            except Exception:
                pass

    def _prepare(self, handle, text, lease=None):
        synth = lease
        try:
            if handle.cancel.is_set():
                return
            if lease is None:
                synth = self.synthesizer()
                path = synth.synthesize(text)
            else:
                path = lease.path
            if handle.cancel.is_set():
                return
            if not path:
                raise RuntimeError(getattr(synth, "last_error", "") or "TTS 음성 생성에 실패했습니다.")
            with self.lock:
                if handle.cancel.is_set() or self.closed:
                    return
                # Opening the isolated player remains silent until explicit play.
                handle.player = self.player_factory(path)
                handle.synth, synth = synth, None
                handle.state = "ready"
        except Exception as exc:
            with self.lock:
                if not handle.cancel.is_set():
                    handle.error = str(exc)
                    handle.state = "failed"
        finally:
            self._cleanup(synth)

    def status(self, handle):
        with self.lock:
            self._check(handle)
            if handle.state == "stopped" or handle.player is None:
                return handle.state
            return handle.player.status()

    def play(self, handle):
        with self.lock:
            self._check(handle)
            if self.closed or self.fault or self.boss_leases or handle.cancel.is_set():
                return False
            if self.status(handle) not in {"ready", "paused"}:
                return False
            try:
                if handle.player.play() is True:
                    return True
            except Exception as exc:
                handle.error = str(exc)
            # A timeout does not mean a queued Play was rejected. Terminate it
            # before releasing the lock so it cannot start after a boss gate.
            self.stop(handle)
            return False

    resume = play

    def pause(self, handle):
        with self.lock:
            self._check(handle)
            if self.status(handle) in {"preparing", "ready", "paused", "completed", "stopped"}:
                return True
            try:
                if handle.player is not None and handle.player.pause() is True:
                    return True
            except Exception as exc:
                handle.error = str(exc)
            return self.stop(handle)

    def stop(self, handle):
        with self.lock:
            self._check(handle)
            handle.cancel.set()
            try:
                silent = handle.player is None or handle.player.stop() is True
            except Exception as exc:
                handle.error = str(exc)
                silent = False
            if not silent:
                self.fault = True
                return False
            handle.state = "stopped"
            self.handles.discard(handle)
            synth, handle.synth = handle.synth, None
            if synth is not None:
                threading.Thread(target=self._cleanup, args=(synth,), name="notice-local-cleanup", daemon=True).start()
            return True

    def begin_boss(self, lease):
        """Host output barrier; unique lease per overlapping boss output."""
        if not isinstance(lease, str) or not lease:
            raise ValueError("보스 출력 식별자가 필요합니다.")
        with self.lock:
            self.boss_leases.add(lease)
            silent = all(self.pause(handle) for handle in list(self.handles))
            return silent and not self.fault

    def end_boss(self, lease):
        with self.lock:
            self.boss_leases.discard(lease)

    def close(self):
        with self.lock:
            self.closed = True
            results = [self.stop(handle) for handle in list(self.handles)]
            return all(results)

"""Serial notice audio controller; deliberately separate from legacy playback.

Transport contract (protocol 1):
* prepare(text) returns a silent handle; generation never starts playback.
* status(handle): preparing/ready/playing/paused/completed/failed/stopped.
* play/resume rechecks the boss audio gate atomically, returns True only if safe.
* pause returns True only after notice audio is silent; stop also confirms silence.
* completed means actual successful EOF, never queued/started/estimated duration.
* a handle owns only notice audio, never the boss player or local preview.

No production transport is installed until both local/Discord paths meet this
contract. The host must call interrupt_for_boss BEFORE playing any boss chime.
"""
import json
import threading
import time

RETRY_SECONDS = 30
MAX_CANDIDATE_ATTEMPTS = 64
STOP_RETRY_SECONDS = 2


class NoticeAudioController:
    def __init__(self, store, transport, *, clock=time.monotonic, prepared_audio=None):
        if getattr(transport, "notice_audio_protocol", None) != 1:
            raise ValueError("재생 완료 확인·보스 우선 제어를 지원하는 알리미 출력 경로가 필요합니다.")
        self.store = store
        self.transport = transport
        self.clock = clock
        self.prepared_audio = prepared_audio
        self.lock = threading.RLock()
        self.active = None
        self.cooldowns = {}
        self.closed = False
        self.fault = ""
        self.stop_retry_at = 0.0
        self.state = "대기"
        self.pending_completion = None

    def _valid(self):
        request = self.active["request"]
        if self.prepared_audio is not None and not self.prepared_audio.current(self.store.server_id, request):
            return False  # Schedule changed, even before its 5-second projection update.
        token = request["token"]
        context = json.loads(token[4]) if len(token) == 5 else None
        return self.store.delivery_token(request["id"], opportunity=context) == token

    def _drop(self, *, retry=False):
        if not self.active:
            return True
        try:
            if self.transport.stop(self.active["handle"]) is not True:
                raise RuntimeError("알리미 음소거/정지가 확인되지 않았습니다.")
        except Exception as exc:
            # No further playback or completion is allowed while an old player
            # might still be audible. Retry only stop, never play or resume.
            self.fault = str(exc) or type(exc).__name__
            self.stop_retry_at = self.clock() + STOP_RETRY_SECONDS
            self.state = "출력 정지 확인 실패 · 자동 안내 차단"
            return False
        if retry:
            self.cooldowns[self.active["request"]["id"]] = self.clock() + RETRY_SECONDS
        self.active = None
        self.fault = ""
        self.stop_retry_at = 0.0
        return True

    def interrupt_for_boss(self):
        """Synchronous silence barrier. False requires transport-level fallback."""
        with self.lock:
            if not self.active:
                return not self.fault
            if self.fault:
                return False
            try:
                status = self.transport.status(self.active["handle"])
                if status in {"preparing", "ready", "paused", "completed", "failed", "stopped"}:
                    return True
                if status == "playing" and self.transport.pause(self.active["handle"]) is True:
                    self.state = "보스 안내 우선 · 일시정지"
                    return True
                return self._drop(retry=True)
            except Exception:
                return self._drop(retry=True)

    def switch_store(self, store):
        with self.lock:
            if store.path != self.store.path:
                if not self._drop():
                    return False
                self.cooldowns.clear()
                self.pending_completion = None
            self.store = store
            return True

    def step(self, *, boss_busy, opportunity=None):
        """One bounded scheduler step; synthesis/playback remain asynchronous."""
        with self.lock:
            if self.closed:
                return
            if self.fault:
                if self.clock() >= self.stop_retry_at and self._drop(retry=True):
                    self.state = "출력 정지 확인 완료 · 자동 안내 복구"
                return  # A fresh step revalidates delivery before any playback.
            try:
                if self.pending_completion is not None:
                    done = self.store.complete_delivery(self.pending_completion)
                    self.pending_completion = None
                    self.state = "안내 완료" if done else "변경된 완료 신호 무시"
                    return
                if self.active:
                    if not self._valid():
                        self._drop()
                        self.state = self.state if self.fault else "변경/만료된 안내 폐기"
                        return
                    handle = self.active["handle"]
                    status = self.transport.status(handle)
                    if status == "completed":
                        # Take the success token only after confirmed EOF, then
                        # release this player's resources before advancing queue.
                        token = self.active["request"]["token"]
                        self.pending_completion = token
                        if self._drop():
                            done = self.store.complete_delivery(token)
                            self.pending_completion = None
                            self.state = "안내 완료" if done else "변경된 완료 신호 무시"
                        return
                    if status in {"failed", "stopped"}:
                        self._drop(retry=True)
                        return
                    if status not in {"preparing", "ready", "playing", "paused"}:
                        raise RuntimeError("알 수 없는 알리미 재생 상태입니다.")
                    if boss_busy:
                        self.interrupt_for_boss()
                        return
                    if status in {"ready", "paused"}:
                        # Revision, expiry, manual checks and quotas are checked
                        # again before every initial play AND resume.
                        if self._valid():
                            method = self.transport.resume if status == "paused" else self.transport.play
                            if method(handle) is True:
                                self.state = "알리미 재생 중"
                        return
                    self.state = "음성 준비 중" if status == "preparing" else "알리미 재생 중"
                    return
                if boss_busy:
                    self.state = "보스 안내 대기 · 알리미 후순위"
                    return
                self.cooldowns = {key: until for key, until in self.cooldowns.items() if self.clock() < until}
                waiting_for_audio = False
                for _ in range(MAX_CANDIDATE_ATTEMPTS):
                    request = self.store.automatic_candidate(opportunity, exclude_ids=self.cooldowns)
                    if not request:
                        self.state = '사전 음성 준비 대기' if waiting_for_audio else '대기'
                        return
                    lease = (self.prepared_audio.acquire(self.store.server_id, request)
                             if self.prepared_audio is not None else None)
                    if self.prepared_audio is None or lease is not None:
                        break
                    # Skip unprepared speech briefly, so a ready notice can use
                    # this opportunity. Never synthesize on the playback path.
                    self.cooldowns[request['id']] = self.clock() + RETRY_SECONDS
                    waiting_for_audio = True
                else:
                    self.state = '사전 음성 준비 대기'
                    return
                # A failed prepare must not starve all other valid notices.
                self.cooldowns[request["id"]] = self.clock() + RETRY_SECONDS
                try:
                    handle = (self.transport.prepare_prepared(lease) if lease is not None
                              else self.transport.prepare(request["tts_text"]))
                except Exception:
                    if lease is not None:
                        lease.stop()
                    raise
                if handle is None:
                    if lease is not None:
                        lease.stop()
                    raise RuntimeError("음성 준비 요청을 처리하지 못했습니다.")
                self.active = {"request": request, "handle": handle}
                self.state = "음성 준비 중"
            except Exception as exc:
                self._drop(retry=True)
                if not self.fault:
                    self.state = f"알리미 처리 실패: {exc}"

    def close(self):
        with self.lock:
            self.closed = True
            return self._drop()

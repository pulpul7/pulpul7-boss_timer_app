"""Output primitive for interruptible notices; no notice/business policies.

Unlike VoiceClient.pause(), suspension releases Discord's single player slot.
The decoder stays at its current position until an explicitly authorized resume.
This module does not enqueue, synthesize, reconnect, or announce anything.
"""
import asyncio
import threading
import time


class ResumableSource:
    def __init__(self, source):
        self.source = source
        self.lock = threading.Lock()
        self.segment = None
        self.pending = None
        self.eof = False
        self.frames = 0
        self.closed = False
        self.closing = False

    def attach(self, base_class):
        owner = self
        with self.lock:
            if self.closed or self.closing or (self.segment is not None and not self.segment.finished.is_set()):
                raise RuntimeError("이전 알리미 재생 스레드가 아직 종료되지 않았습니다.")

            class Segment(base_class):
                def __init__(self):
                    self.suspended = False
                    self.finished = threading.Event()

                def suspend(self):
                    # Never wait here for a decoder's blocking read().
                    with owner.lock:
                        self.suspended = True

                def read(self):
                    with owner.lock:
                        if self.suspended or owner.closed or owner.segment is not self:
                            return b""
                        if owner.pending is not None:
                            data, owner.pending = owner.pending, None
                            owner.frames += 1
                            return data
                    data = owner.source.read()
                    with owner.lock:
                        if not data:
                            owner.eof = True  # Real decoder EOF, not synthetic suspension.
                        if self.suspended:
                            if data:
                                owner.pending = data  # Preserve a frame decoded during interruption.
                            return b""
                        if data:
                            owner.frames += 1
                        return data

                def is_opus(self):
                    return owner.source.is_opus()

                def cleanup(self):
                    # Discord cleans each player segment. Only the logical owner
                    # may release the retained decoder once all readers exit.
                    self.finished.set()

                def __getattr__(self, name):
                    return getattr(owner.source, name)

            self.segment = Segment()
            return self.segment

    def close(self):
        with self.lock:
            if self.closed:
                return True
            if self.closing:
                return False
            if self.segment is not None and not self.segment.finished.is_set():
                return False
            self.closing = True
        try:
            self.source.cleanup()
        except Exception:
            with self.lock:
                self.closing = False
            raise
        with self.lock:
            self.closed = True
            self.closing = False
        return True


class DiscordNoticeOutput:
    """One already prepared notice, owned by a running Discord bot.

    Caller must revalidate revision/server/expiry before EVERY play/resume.
    No automatic resume: boss completion alone is not sufficient authorization.
    deadline is monotonic; it also bounds a retained, paused decoder's lifetime.
    """
    def __init__(self, bot, source, *, scope_id, deadline, clock=time.monotonic):
        if not scope_id or not 0 < deadline - clock() <= 1800:
            raise ValueError("알리미 출력 식별자와 30분 이내의 유효한 출력 기한이 필요합니다.")
        self.bot = bot
        self.audio = ResumableSource(source)
        self.scope_id = scope_id
        self.deadline = deadline
        self.clock = clock
        self.state = "ready"
        self.error = ""
        self.token = None
        self.segment = None
        self.released = False
        self.watcher = bot.client.loop.create_task(self._watch())

    def owns(self, token):
        return token is not None and self.token is token

    def before_stop(self, token, reason):
        if not self.owns(token) or self.segment is None:
            return
        self.segment.suspend()
        self.state = ("pausing" if reason in {"notice_preempt", "timed_clip_replace", "countdown_composite_replace"}
                      else "stopping")

    def _ended(self, token, segment, error):
        self.bot._clear_current_voice_playback(token)
        if not self.owns(token) or self.segment is not segment:
            return  # A delayed callback must not complete another generation.
        self.token = None
        connected = self.bot.voice_client is not None and self.bot.voice_client.is_connected()
        if error or not connected:
            self.state = "failed"
            self.error = str(error or "음성 연결이 종료되었습니다.")
        elif self.state == "stopping":
            self.state = "stopped"
        elif self.audio.eof and self.audio.frames > 0:
            self.state = "completed"
        elif self.state == "pausing":
            self.state = "paused"
        else:
            self.state = "failed"
            self.error = "알리미가 끝까지 재생되기 전에 중단되었습니다."

    async def play(self):
        bot = self.bot
        async with bot.voice_transition_lock:
            if (self.state not in {"ready", "paused"} or self.clock() >= self.deadline or self.released
                    or bot._is_voice_bridge_scope_cancelled(self.scope_id)):
                return False
            if self.segment is not None and not self.segment.finished.is_set():
                return False
            voice = bot.voice_client
            if voice is None or not voice.is_connected():
                return False
            # No takeover of a boss, countdown, queued job or short clip gap.
            if (bot.voice_play_lock.locked() or getattr(bot, "regular_voice_job_active", False)
                    or not bot.play_queue.empty() or bot.timed_bridge_tasks
                    or bot.current_voice_playback_token is not None
                    or voice.is_playing() or voice.is_paused()):
                return False
            segment = self.audio.attach(bot.discord.AudioSource)
            token = object()
            self.segment, self.token = segment, token
            self.state = "playing"
            bot.current_voice_scope_id = self.scope_id
            bot.current_voice_playback_token = token
            def after(error):
                bot.client.loop.call_soon_threadsafe(self._ended, token, segment, error)
            try:
                voice.play(segment, after=after)
                bot.current_voice_player_thread = getattr(voice, "_player", None)
            except Exception as exc:
                segment.suspend()
                segment.cleanup()
                self._ended(token, segment, exc)
                return False
            return True

    async def stop(self):
        bot = self.bot
        async with bot.voice_transition_lock:
            if self.released:
                return True
            if self.owns(bot.current_voice_playback_token):
                _, _, joined = await bot._stop_current_voice_playback_and_wait(
                    expected_scope_id=self.scope_id, reason="notice_stop")
                if not joined:
                    self.error = "알리미 재생 스레드 종료를 확인하지 못했습니다."
                    return False
            if self.segment is not None:
                self.segment.suspend()
                if not self.segment.finished.is_set():
                    return False
            if self.state != "completed":
                self.state = "stopped"
        # Decoder cleanup can wait on FFmpeg; never hold the boss output lock.
        self.released = await asyncio.to_thread(self.audio.close)
        return self.released

    async def _watch(self):
        try:
            while not self.released:
                voice = self.bot.voice_client
                if (self.clock() >= self.deadline or voice is None or not voice.is_connected()
                        or self.bot._is_voice_bridge_scope_cancelled(self.scope_id)
                        or self.state in {"completed", "failed", "stopped"}):
                    await self.stop()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"알리미 출력 정리 실패: {exc}"
            self.state = "failed"

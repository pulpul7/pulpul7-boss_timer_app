"""Local-only preview: no notice store, Discord bridge or boss audio queues."""
import queue
import subprocess
import threading
import time

from .notice_local_player import LocalPlayer


def spawn_local_player(path):
    return PreviewPlayback(LocalPlayer(path))


class PreviewPlayback:
    """Process-like preview wrapper using the real EOF/stop-ack local player."""
    def __init__(self, player):
        self.player = player
        self.lock = threading.Lock()
        self.changed = threading.Event()
        self.returncode = None
        self.started = False

    def poll(self):
        with self.lock:
            return self.returncode

    def _finish(self, code):
        if self.player.stop() is not True:
            raise OSError("미리듣기 플레이어의 종료를 확인하지 못했습니다.")
        self.returncode = code
        self.changed.set()

    def wait(self, timeout):
        until = time.monotonic() + timeout
        while True:
            with self.lock:
                if self.returncode is not None:
                    return self.returncode
                state = self.player.status()
                if state == "ready" and not self.started:
                    self.started = True
                    if self.player.play() is not True:
                        self._finish(2)
                elif state == "completed":
                    self._finish(0)
                elif state in {"failed", "stopped"}:
                    self._finish(2)
                if self.returncode is not None:
                    return self.returncode
            if time.monotonic() >= until:
                raise subprocess.TimeoutExpired("notice-preview", timeout)
            self.changed.wait(min(0.02, max(0, until - time.monotonic())))

    def terminate(self):
        with self.lock:
            if self.returncode is None:
                self._finish(1)

    kill = terminate


class LocalPreview:
    def __init__(self, factory, *, spawn=spawn_local_player):
        self.factory = factory
        self.spawn = spawn
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.worker = None
        self.process = None
        self.retained_synth = None

    def start(self, text):
        if self.worker is not None and self.worker.is_alive():
            raise ValueError("이전 음성 처리가 끝날 때까지 잠시 기다려 주세요.")
        with self.lock:
            if self.process is not None:
                raise ValueError("이전 미리듣기의 종료가 확인되지 않았습니다. 미리듣기 정지를 다시 눌러 주세요.")
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError("읽을 문장을 1~10,000자로 입력해 주세요.")
        if not callable(self.factory):
            raise ValueError("이 본체는 로컬 TTS 미리듣기를 지원하지 않습니다. 본체 업데이트가 필요합니다.")
        while not self.messages.empty():
            self.messages.get_nowait()
        self.cancel.clear()
        self.worker = threading.Thread(target=self._run, args=(text.strip(),), name="notice-local-preview", daemon=True)
        self.worker.start()

    def stop(self):
        self.cancel.set()
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.terminate()
                except OSError as exc:
                    self.messages.put(f"미리듣기 정지 확인 실패: {exc}")
            if self.retained_synth is not None and self.process is not None and self.process.poll() is not None:
                synth, self.retained_synth = self.retained_synth, None
                self.process = None
                threading.Thread(target=self._cleanup, args=(synth,), name="notice-preview-cleanup", daemon=True).start()

    def _cleanup(self, synth):
        if synth is not None:
            try:
                synth.stop()  # Only this preview's temporary cache; no persistent directory.
            except Exception as exc:
                self.messages.put(f"미리듣기 임시 파일 정리 실패: {exc}")

    def _run(self, text):
        synth = None
        process = None
        try:
            self.messages.put("음성 생성 중 · Discord에는 보내지 않습니다.")
            synth = self.factory()
            path = synth.synthesize(text)
            if self.cancel.is_set():
                return
            if not path:
                raise RuntimeError(getattr(synth, "last_error", "") or "TTS 설정과 모듈 설치 상태를 확인해 주세요.")
            with self.lock:
                if self.cancel.is_set():
                    return
                self.process = process = self.spawn(path)
            self.messages.put("이 PC에서 음성 준비/재생 중 · 정지 버튼으로 중단할 수 있습니다.")
            code = process.wait(timeout=620)
            if code and not self.cancel.is_set():
                raise RuntimeError("로컬 음성 재생에 실패했습니다. Windows 오디오 장치를 확인해 주세요.")
            self.messages.put("정지했습니다." if self.cancel.is_set() else "미리듣기 완료 · 안내 기록에는 반영하지 않았습니다.")
        except Exception as exc:
            self.messages.put("정지했습니다." if self.cancel.is_set() else f"미리듣기 오류: {exc}")
        finally:
            silent = False
            try:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                silent = process is None or process.poll() is not None
            except Exception as exc:
                self.messages.put(f"미리듣기 종료 확인 실패 · 새 재생 차단: {exc}")
            with self.lock:
                if silent:
                    self.process = None
                else:
                    self.retained_synth = synth
            if silent:
                self._cleanup(synth)
                if self.cancel.is_set():
                    self.messages.put("정지했습니다.")

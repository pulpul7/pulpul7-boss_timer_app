"""Prepare one owned notice file: announcement chime, then unchanged TTS."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


NOTICE_CHIME_FILENAME = "안내방송_비행기4.wav"
NOTICE_AUDIO_FORMAT = "notice-chime4-pcm-v1"


def notice_audio_resources(app_root, resource_root):
    app_root, resource_root = Path(app_root), Path(resource_root)
    chimes = (app_root / "wave" / NOTICE_CHIME_FILENAME, resource_root / "wave" / NOTICE_CHIME_FILENAME)
    chime = next((path for path in chimes if path.is_file()), chimes[-1])
    converters = (os.environ.get("BOSS_TIMER_FFMPEG", ""), resource_root / "ffmpeg.exe",
                  app_root / "ffmpeg.exe", shutil.which("ffmpeg"))
    ffmpeg = next((Path(path) for path in converters if path and Path(path).is_file()), None)
    try:
        stat = chime.stat()
        stamp = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        stamp = "missing"
    signature = f"{NOTICE_AUDIO_FORMAT}|{chime}|{stamp}|{ffmpeg}"
    return chime, ffmpeg, signature


class NoticeChimeSynthesizer:
    """Shared by local preview and prepared PC/Discord announcements only."""
    def __init__(self, speech, chime, ffmpeg):
        self.speech = speech
        self.chime = Path(chime)
        self.ffmpeg = ffmpeg
        self.last_error = ""
        self._directory = None
        self._stopped = threading.Event()

    def synthesize(self, text):
        if self._stopped.is_set():
            return None
        if not self.chime.is_file() or self.ffmpeg is None:
            self.last_error = "알리미 차임벨 또는 음성 변환 파일을 찾지 못했습니다. 본체 배포 파일을 확인하세요."
            return None
        speech_path = self.speech.synthesize(text)
        if not speech_path:
            self.last_error = self.speech.last_error
            return None
        if self._stopped.is_set():
            return None
        try:
            if self._directory is None:
                self._directory = tempfile.TemporaryDirectory(prefix="boss_timer_notice_audio_")
            identity = hashlib.sha256(str(text).encode("utf-8")).hexdigest()
            target = Path(self._directory.name) / (identity + ".wav")
            if not target.is_file():
                filters = (
                    "[0:a]aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo,"
                    "apad=pad_dur=0.12[chime];"
                    "[1:a]aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo[speech];"
                    "[chime][speech]concat=n=2:v=0:a=1[notice]"
                )
                result = subprocess.run(
                    [str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                     "-i", str(self.chime), "-i", str(speech_path), "-filter_complex", filters,
                     "-map", "[notice]", "-c:a", "pcm_s16le", str(target)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode or not target.is_file() or target.stat().st_size <= 44:
                    target.unlink(missing_ok=True)
                    raise RuntimeError(result.stderr.strip() or "알리미 차임벨 결합에 실패했습니다.")
            if self._stopped.is_set():
                return None
            self.last_error = ""
            return str(target)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def stop(self):
        self._stopped.set()
        try:
            self.speech.stop()
        finally:
            if self._directory is not None:
                self._directory.cleanup()
                self._directory = None

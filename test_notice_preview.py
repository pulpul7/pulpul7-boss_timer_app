"""No actual synthesis, sound, external process or Discord connection."""
import base64
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from notice_module.payload.notice_preview import LocalPreview, spawn_local_player, PreviewPlayback
from notice_module.payload.notice_local_player import spawn_player


class PreviewTests(unittest.TestCase):
    def test_preview_uses_only_supplied_synth_and_separate_local_process(self):
        synth = Mock()
        synth.synthesize.return_value = "preview.mp3"
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        spawn = Mock(return_value=process)
        preview = LocalPreview(lambda: synth, spawn=spawn)
        preview.start(" 저장하지 않은 편집 문장 ")
        preview.worker.join(timeout=2)
        self.assertFalse(preview.worker.is_alive())
        synth.synthesize.assert_called_once_with("저장하지 않은 편집 문장")
        spawn.assert_called_once_with("preview.mp3")
        synth.stop.assert_called_once()
        self.assertIn("미리듣기 완료", " ".join(list(preview.messages.queue)))

    def test_cancel_during_synthesis_never_plays_late_result(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        synth = Mock()
        def render(_):
            entered.set()
            release.wait(timeout=2)
            return "late.mp3"
        synth.synthesize.side_effect = render
        spawn = Mock()
        preview = LocalPreview(lambda: synth, spawn=spawn)
        preview.start("테스트")
        self.assertTrue(entered.wait(timeout=1))
        with self.assertRaises(ValueError):
            preview.start("동시 테스트")
        preview.stop()
        release.set()
        preview.worker.join(timeout=2)
        spawn.assert_not_called()
        synth.stop.assert_called_once()

    def test_stop_terminates_only_preview_process(self):
        started, finished = threading.Event(), threading.Event()
        self.addCleanup(finished.set)
        process = Mock()
        process.poll.side_effect = lambda: 0 if finished.is_set() else None
        process.terminate.side_effect = finished.set
        def wait(**_):
            started.set()
            finished.wait(timeout=2)
            return 0
        process.wait.side_effect = wait
        synth = Mock()
        synth.synthesize.return_value = "preview.mp3"
        preview = LocalPreview(lambda: synth, spawn=lambda _: process)
        preview.start("테스트")
        self.assertTrue(started.wait(timeout=1))
        preview.stop()
        preview.worker.join(timeout=2)
        process.terminate.assert_called_once()
        synth.stop.assert_called_once()

    def test_failed_synthesis_reports_error_and_cleans_temp_cache(self):
        synth = Mock(last_error="TTS 모듈 설치 필요")
        synth.synthesize.return_value = None
        spawn = Mock()
        preview = LocalPreview(lambda: synth, spawn=spawn)
        preview.start("테스트")
        preview.worker.join(timeout=2)
        spawn.assert_not_called()
        synth.stop.assert_called_once()
        self.assertIn("TTS 모듈 설치 필요", " ".join(list(preview.messages.queue)))

    def test_playback_timeout_kills_player_before_cleanup(self):
        synth, process = Mock(), Mock()
        synth.synthesize.return_value = "preview.mp3"
        process.poll.return_value = None
        process.kill.side_effect = lambda: setattr(process.poll, "return_value", 0)
        process.wait.side_effect = [subprocess.TimeoutExpired("preview", 620), 0]
        preview = LocalPreview(lambda: synth, spawn=lambda _: process)
        preview.start("테스트")
        preview.worker.join(timeout=2)
        process.kill.assert_called_once()
        synth.stop.assert_called_once()

    def test_unconfirmed_stop_preserves_handle_and_blocks_new_playback(self):
        synth, process = Mock(), Mock()
        synth.synthesize.return_value = "preview.mp3"
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("preview", 620)
        process.kill.side_effect = OSError("cannot stop")
        preview = LocalPreview(lambda: synth, spawn=lambda _: process)
        preview.start("정지 실패")
        preview.worker.join(timeout=2)
        self.assertIs(preview.process, process)
        synth.stop.assert_not_called()
        with self.assertRaises(ValueError):
            preview.start("중복 재생 차단")
        cleaned = threading.Event()
        synth.stop.side_effect = cleaned.set
        process.terminate.side_effect = lambda: setattr(process.poll, "return_value", 1)
        preview.stop()
        self.assertTrue(cleaned.wait(timeout=2))
        self.assertIsNone(preview.process)

    def test_invalid_text_and_older_host_do_not_start_worker(self):
        preview = LocalPreview(None)
        for text in ("", "x" * 10001, "정상 문장"):
            with self.assertRaises(ValueError):
                preview.start(text)
        self.assertIsNone(preview.worker)

    def test_player_path_is_data_not_executable_powershell(self):
        path = 'C:\\temp\\한글 $(not-a-command) `test.mp3'
        with patch("notice_module.payload.notice_local_player.os.name", "nt"), \
                patch("notice_module.payload.notice_local_player.os.path.isfile", return_value=True), \
                patch("notice_module.payload.notice_local_player.subprocess.Popen") as popen:
            spawn_player(path)
        args, kwargs = popen.call_args
        command = args[0]
        self.assertIn("Hidden", command)
        script = base64.b64decode(command[-1]).decode("utf-16le")
        self.assertNotIn("not-a-command", script)
        self.assertIn("$env:BOSS_NOTICE_AUDIO_PATH", script)
        self.assertIn("not-a-command", kwargs["env"]["BOSS_NOTICE_AUDIO_PATH"])
        self.assertNotIn("shell", kwargs)

    def test_preview_adapter_waits_for_real_completion_then_releases_player(self):
        player = Mock()
        player.status.side_effect = ["preparing", "ready", "playing", "completed"]
        player.play.return_value = player.stop.return_value = True
        playback = PreviewPlayback(player)
        self.assertIsNone(playback.poll())
        self.assertEqual(playback.wait(timeout=1), 0)
        player.play.assert_called_once()
        player.stop.assert_called_once()

    def test_cancel_before_ready_cannot_later_play(self):
        player = Mock()
        player.stop.return_value = True
        playback = PreviewPlayback(player)
        playback.terminate()
        self.assertEqual(playback.wait(timeout=1), 1)
        player.play.assert_not_called()

    def test_failed_play_is_not_preview_completion(self):
        player = Mock()
        player.status.return_value = "ready"
        player.play.return_value = False
        player.stop.return_value = True
        self.assertEqual(PreviewPlayback(player).wait(timeout=1), 2)
        player.stop.assert_called_once()

    def test_preview_stop_requires_confirmed_player_exit(self):
        player = Mock()
        player.stop.return_value = False
        playback = PreviewPlayback(player)
        with self.assertRaises(OSError):
            playback.terminate()
        self.assertIsNone(playback.poll())

    def test_default_preview_spawns_only_the_local_player(self):
        with patch("notice_module.payload.notice_preview.LocalPlayer") as player:
            playback = spawn_local_player("preview.mp3")
        player.assert_called_once_with("preview.mp3")
        self.assertIsInstance(playback, PreviewPlayback)


if __name__ == "__main__":
    unittest.main()

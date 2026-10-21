"""Read-only notice diagnostics; no real Tk, audio, network or user files."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest

from notice_module.payload.main import NoticePlugin
from notice_module.payload.notice_preparation import PreparedSpeechPool


class OutputDiagnosticsTests(unittest.TestCase):
    def plugin(self):
        plugin = object.__new__(NoticePlugin)
        plugin.audio_status = '대기'
        plugin.prepared_audio = PreparedSpeechPool()
        plugin.stopped = False
        plugin.audio_worker = plugin.audio_controller = plugin.audio_identity = None
        plugin.host = NS(get_output_context=Mock(return_value=None),
                         get_schedule_snapshot=Mock(), call_later=Mock(), log=Mock())
        return plugin

    def test_tts_failure_is_visible_without_endpoint_credentials(self):
        plugin = self.plugin()
        plugin.prepared_audio.last_error = "WSServerHandshakeError: 429, url='wss://example.invalid/tts?key=secret'"
        result = plugin.output_status()
        self.assertIn('429', result)
        self.assertIn('음성 생성/변환 오류', result)
        self.assertNotIn('secret', result)
        self.assertNotIn('example.invalid', result)

    def test_successful_preparation_clears_old_failure_display(self):
        plugin = self.plugin();plugin.prepared_audio.last_error = 'failed'
        self.assertIn('failed', plugin.output_status())
        plugin.prepared_audio.last_error = ''
        self.assertEqual(plugin.output_status(), '대기')

    def test_reading_status_has_no_preparation_or_output_side_effect(self):
        plugin = self.plugin();plugin.prepared_audio.prepare = Mock()
        for _ in range(30):plugin.output_status()
        plugin.prepared_audio.prepare.assert_not_called()
        plugin.host.get_output_context.assert_not_called()
        plugin.host.get_schedule_snapshot.assert_not_called()

    def test_connection_reason_is_captured_before_worker_and_legacy_host_works(self):
        for specific in (False, True):
            with self.subTest(specific=specific):
                plugin = self.plugin();workers = []
                if specific:plugin.host.get_output_wait_reason = Mock(return_value='알리미 인증 정보 확인 필요')
                def thread(**kw):
                    return NS(start=lambda: workers.append(kw['target']))
                with patch('notice_module.payload.main.threading.Thread', side_effect=thread):
                    plugin._audio_tick()
                if specific:plugin.host.get_output_wait_reason.assert_called_once()
                plugin.host.get_schedule_snapshot.assert_not_called()
                self.assertEqual(len(workers), 1)
                workers[0]()
                expected = '알리미 인증 정보 확인 필요' if specific else '디코 연결/인계 완료 대기'
                self.assertEqual(plugin.audio_status, expected)
                if specific:plugin.host.get_output_wait_reason.assert_called_once()


if __name__ == '__main__':
    unittest.main()

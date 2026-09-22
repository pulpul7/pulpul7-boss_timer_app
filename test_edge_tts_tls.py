"""Offline installer TLS tests; never change the OS certificate store."""
import ast
from pathlib import Path
import ssl
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

from edge_tts_module import _create_download_ssl_context, install_edge_tts_module


class DownloadTlsTests(unittest.TestCase):
    def test_windows_uses_native_context_without_global_ssl_injection(self):
        original_ssl_context = ssl.SSLContext
        native = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        backend = SimpleNamespace(SSLContext=Mock(return_value=native), inject_into_ssl=Mock())
        with patch('edge_tts_module.sys.platform', 'win32'), patch.dict('sys.modules', truststore=backend):
            actual = _create_download_ssl_context()
        backend.SSLContext.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        backend.inject_into_ssl.assert_not_called()
        self.assertIs(actual, native)
        self.assertEqual(actual.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(actual.check_hostname)
        self.assertIs(ssl.SSLContext, original_ssl_context)

    def test_missing_truststore_is_actionable_and_never_downloads(self):
        download = Mock()
        with tempfile.TemporaryDirectory(prefix='boss-tts-tls-unit-') as folder:
            destination = Path(folder) / 'tts_module'
            with patch('edge_tts_module.sys.platform', 'win32'), patch.dict('sys.modules', truststore=None):
                with self.assertRaisesRegex(RuntimeError, 'requirements-gui.txt'):
                    install_edge_tts_module(str(destination), urlopen=download)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(folder).iterdir()), [])
        download.assert_not_called()

    def test_other_platform_keeps_verified_default_context(self):
        with patch('edge_tts_module.sys.platform', 'linux'):
            context = _create_download_ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def assert_failed_download_preserves_module(self, error, message_pattern, expected_type=RuntimeError):
        download = Mock(side_effect=error)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with tempfile.TemporaryDirectory(prefix='boss-tts-tls-unit-') as folder:
            destination = Path(folder) / 'tts_module'
            destination.mkdir()
            original = destination / 'existing.txt'
            original.write_text('existing installation', encoding='utf-8')
            with patch('edge_tts_module._create_download_ssl_context', return_value=context):
                with self.assertRaisesRegex(expected_type, message_pattern):
                    install_edge_tts_module(str(destination), download_url='https://example.invalid/module.zip', urlopen=download)
            self.assertEqual(original.read_text(encoding='utf-8'), 'existing installation')
            self.assertEqual(list(Path(folder).iterdir()), [destination])
        download.assert_called_once()  # no unverified retry/fallback
        self.assertIs(download.call_args.kwargs['context'], context)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_wrapped_certificate_failure_explains_trust_store_and_preserves_install(self):
        error = urllib.error.URLError(ssl.SSLCertVerificationError(1, 'CERTIFICATE_VERIFY_FAILED'))
        self.assert_failed_download_preserves_module(error, '인증서 저장소')

    def test_direct_certificate_failure_also_preserves_install(self):
        self.assert_failed_download_preserves_module(ssl.SSLCertVerificationError(1, 'invalid chain'), '인증서 검증을 끄지')

    def test_legacy_string_certificate_reason_is_identified(self):
        self.assert_failed_download_preserves_module(urllib.error.URLError('[SSL: CERTIFICATE_VERIFY_FAILED]'), '인증서 저장소')

    def test_404_is_not_misreported_as_certificate_failure(self):
        self.assert_failed_download_preserves_module(
            urllib.error.HTTPError('https://example.invalid', 404, 'Not Found', {}, None),
            'GitHub Release 파일을 찾을 수 없습니다')

    def test_network_error_keeps_original_reason(self):
        self.assert_failed_download_preserves_module(urllib.error.URLError('connection timed out'),
                                                    'connection timed out', urllib.error.URLError)

    def test_gui_build_requires_and_bundles_truststore_before_side_effects(self):
        root = Path(__file__).resolve().parent
        tree = ast.parse((root / 'boss_timer_gui.spec').read_text(encoding='utf-8'))
        guard = next(node for node in tree.body if isinstance(node, ast.Try) and
                     any(isinstance(child, ast.Import) and any(alias.name == 'truststore' for alias in child.names)
                         for child in node.body))
        # Run only the import guard, never the spec (which creates a build).
        with patch.dict('sys.modules', truststore=None):
            with self.assertRaisesRegex(RuntimeError, 'requirements-gui.txt'):
                exec(compile(ast.Module(body=[guard], type_ignores=[]), '<build dependency guard>', 'exec'), {})
        analysis = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == 'Analysis')
        imports = ast.literal_eval(next(k.value for k in analysis.keywords if k.arg == 'hiddenimports'))
        self.assertIn('truststore', imports)
        self.assertIn('truststore._windows', imports)
        self.assertLess(guard.lineno, analysis.lineno)
        self.assertIn('truststore>=0.10.4,<1', (root / 'requirements-gui.txt').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

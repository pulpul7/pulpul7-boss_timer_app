"""Offline recording integrity, HTTP authentication and failure isolation checks."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from web_https_app import HTTPSApplication
from web_notice_audio import SOUNDS, sound_asset
from web_service_worker import LocalHandler


class NoticeAudioTests(unittest.TestCase):
    def app(self, authenticated=True):
        context = dict(authenticated=authenticated, blocked=False, cookies=[])
        access = SimpleNamespace(context=Mock(return_value=context), read_wait=Mock(return_value=0))
        assets = {name: b'asset' for name in ('index.html', 'login.html', 'style.css', 'schedule.js', 'login.js')}
        return HTTPSApplication('test.duckdns.org', 8766, Mock(), access, None,
                                assets=assets, authority_check=lambda: True)

    def call(self, app, path='/sounds/air-raid.mp3', extra=None):
        result = {}
        environ = dict(REQUEST_METHOD='GET', PATH_INFO=path, REMOTE_ADDR='127.0.0.1',
                       HTTP_HOST='test.duckdns.org:8766', **{'wsgi.url_scheme': 'https'})
        environ.update(extra or {})
        def start(status, headers):
            result.update(code=int(status.split()[0]), headers=dict(headers))
        result['body'] = b''.join(app(environ, start))
        return result

    def test_recordings_match_allowlisted_hashes_and_remain_small(self):
        self.assertEqual(len(SOUNDS), 2)
        for path, (_, expected) in SOUNDS.items():
            raw, mime = sound_asset(path)
            self.assertEqual(mime, 'audio/mpeg')
            self.assertLess(len(raw), 64 * 1024)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)

    def test_only_known_recording_paths_are_served(self):
        for path in ('/sounds/../index.html', '/sounds/%2e%2e/index.html',
                     '/sounds/air-raid.mp3/extra', '/sounds/SOURCES.txt', '/sounds/user-upload.mp3'):
            self.assertIsNone(sound_asset(path))
            self.assertEqual(self.call(self.app(), path)['code'], 404)

    def test_https_requires_login_and_reuses_cached_recording(self):
        self.assertEqual(self.call(self.app(False))['code'], 401)
        app = self.app()
        result = self.call(app)
        self.assertEqual(result['code'], 200)
        self.assertIn("media-src 'self' blob:", result['headers']['Content-Security-Policy'])
        self.assertNotIn('https://cdn.freesound.org', result['headers']['Content-Security-Policy'])
        cached = self.call(app, extra={'HTTP_IF_NONE_MATCH': result['headers']['ETag']})
        self.assertEqual(cached['code'], 304)
        self.assertEqual(cached['body'], b'')

    def test_missing_sound_does_not_break_schedule_endpoint(self):
        app = self.app()
        with patch('web_notice_audio.sound_asset', side_effect=OSError('missing')):
            self.assertEqual(self.call(app)['code'], 503)
        app.schedule.payload.return_value = (200, dict(events=[]))
        self.assertEqual(self.call(app, '/api/schedule')['code'], 200)

    def test_modified_recording_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='boss-notice-audio-unit-') as temporary:
            folder = Path(temporary)
            (folder / 'air-raid.mp3').write_bytes(b'not the bundled sound')
            with patch('web_notice_audio.bundle_folder', return_value=folder):
                with self.assertRaises(ValueError):
                    sound_asset('/sounds/air-raid.mp3')

    def test_local_listener_uses_same_recordings_and_authentication(self):
        handler = object.__new__(LocalHandler)
        handler.server = SimpleNamespace(assets={}, logger=None)
        handler._context = Mock(return_value=dict(authenticated=True, cookies=[]))
        handler._authorized = Mock(return_value=True)
        handler._send = Mock();handler._json = Mock()
        handler.path = '/sounds/nuclear.mp3';handler._record_visit = False
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[:3], (200, *sound_asset(handler.path)))
        handler._send.reset_mock();handler._authorized.return_value = False
        handler.do_GET();handler._send.assert_not_called()


if __name__ == '__main__':
    unittest.main()

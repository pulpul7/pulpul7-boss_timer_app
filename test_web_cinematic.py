"""MP4 metadata, seek ranges and both authenticated server routes; no app data."""
import io
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from web_cinematic import atoms, cinematic_response, clips, video_metadata
from web_https_app import HTTPSApplication
from web_service_worker import LocalHandler
from inspect_cinematic_media import measure


class CinematicTests(unittest.TestCase):
    def app(self, authenticated=True):
        access = SimpleNamespace(context=Mock(return_value=dict(authenticated=authenticated, blocked=False, cookies=[])), read_wait=Mock(return_value=0))
        files = {n: b'asset' for n in ('index.html', 'login.html', 'style.css', 'schedule.js', 'login.js')}
        return HTTPSApplication('test.invalid', 443, Mock(), access, None, assets=files, authority_check=lambda: True)

    def call(self, path, authenticated=True, **headers):
        result = {}
        env = dict(REQUEST_METHOD='GET', PATH_INFO=path, REMOTE_ADDR='127.0.0.1', HTTP_HOST='test.invalid', **{'wsgi.url_scheme': 'https'}, **headers)
        def start(status, values):
            result.update(code=int(status.split()[0]), headers=dict(values))
        result['body'] = b''.join(self.app(authenticated)(env, start))
        return result

    def test_parser_uses_video_track_timebase_and_frames(self):
        def box(name, body):
            return struct.pack('>I4s', len(body)+8, name) + body
        desc = bytearray(86)
        struct.pack_into('>I4s', desc, 0, 86, b'avc1')
        struct.pack_into('>HH', desc, 32, 960, 226)
        table = box(b'stsd', b'\0'*4 + struct.pack('>I', 1) + desc) + box(b'stsz', b'\0'*8 + struct.pack('>I', 141))
        track = box(b'mdhd', b'\0'*12 + struct.pack('>II', 1200, 14100)) + box(b'hdlr', b'\0'*8 + b'vide') + box(b'minf', box(b'stbl', table))
        raw = box(b'ftyp', b'isom') + box(b'moov', box(b'trak', box(b'mdia', track)))
        with patch.object(Path, 'open', return_value=io.BytesIO(raw)):
            value = video_metadata(Path('synthetic.mp4'))
        self.assertEqual(value, dict(duration=11.75, fps=12, width=960, height=226))
        with self.assertRaises(ValueError):
            list(atoms(struct.pack('>I4s', 999, b'moov')))

    def test_real_catalog_has_valid_video_only_durations(self):
        records = clips()
        self.assertTrue(records, 'The source cinematic folder must be included for this check.')
        for _, _, item in records.values():
            self.assertGreater(item['duration'], 0)
            self.assertAlmostEqual(item['fps'], 12)
            self.assertEqual(item['width'], 960)
            self.assertGreaterEqual(item['height'], 100)
            self.assertLessEqual(item['height'], 1080)
        catalog = json.loads(cinematic_response('/cinematic/catalog.json')[1])['clips']
        self.assertEqual(len(catalog), len(records))
        self.assertTrue(all(c['name'] and c['url'].startswith('/cinematic/') for c in catalog))

    def test_catalog_conditional_response_tracks_current_media(self):
        first = self.call('/cinematic/catalog.json')
        tag = first['headers']['ETag']
        cached = self.call('/cinematic/catalog.json', HTTP_IF_NONE_MATCH='"other", ' + tag)
        self.assertEqual((cached['code'], cached['body']), (304, b''))
        with patch('web_cinematic.clips', return_value={}):
            changed = self.call('/cinematic/catalog.json', HTTP_IF_NONE_MATCH=tag)
        self.assertEqual(changed['code'], 200)
        self.assertNotEqual(changed['headers']['ETag'], tag)
        self.assertEqual(json.loads(changed['body']), {'clips': []})

    def test_audio_measurements_use_edit_list_without_encoder_padding(self):
        def box(name, body):
            return struct.pack('>I4s', len(body)+8, name) + body
        movie = box(b'mvhd', b'\0'*12 + struct.pack('>II', 1000, 29000))
        audio = box(b'mdhd', b'\0'*12 + struct.pack('>II', 44100, 29*44100+1024)) + box(b'hdlr', b'\0'*8+b'soun')
        edit = box(b'edts', box(b'elst', b'\0'*4 + struct.pack('>IIiHH', 1, 29000, 1024, 1, 0)))
        raw = box(b'moov', movie + box(b'trak', edit + box(b'mdia', audio)))
        stat = SimpleNamespace(st_size=len(raw), st_mtime_ns=1)
        with patch.object(Path, 'read_bytes', return_value=raw), patch.object(Path, 'stat', return_value=stat), \
             patch('inspect_cinematic_media.video_metadata', return_value=dict(duration=29, fps=12, width=960, height=226)):
            result = measure('synthetic.mp4')
        self.assertEqual(result['audio'][0]['playback_seconds'], 29)
        self.assertAlmostEqual(result['audio'][0]['encoded_seconds'], 29+1024/44100)
        self.assertEqual(result['movie_seconds'], 29)

    def test_ranges_cache_and_rejected_ranges(self):
        key, (file, size, _) = next(iter(clips().items()))
        path, raw = '/cinematic/'+key+'.mp4', file.read_bytes()
        for requested, expected in [('bytes=0-63', raw[:64]), ('bytes=64-', raw[64:]), ('bytes=-32', raw[-32:]), ('bytes=0-999999999', raw)]:
            code, body, mime, headers = cinematic_response(path, requested)
            self.assertEqual((code, body, mime), (206, expected, 'video/mp4'))
            self.assertIn('Content-Range', dict(headers))
        for invalid in ('bytes=', 'bytes=-0', 'bytes=90-1', f'bytes={size}-', 'bytes=0-1,3-4', 'garbage'):
            code, body, _, headers = cinematic_response(path, invalid)
            self.assertEqual((code, body, dict(headers)['Content-Range']), (416, b'', f'bytes */{size}'))
        self.assertEqual(cinematic_response(path, if_none_match='"'+key+'"')[:2], (304, b''))
        self.assertEqual(cinematic_response(path, 'bytes=0-1', if_range='"old"')[:2], (200, raw))

    def test_no_arbitrary_paths_and_no_files_for_anonymous_requests(self):
        for path in ('/cinematic/../AGENTS.md', '/cinematic/%2e%2e/AGENTS.md', '/cinematic/private.mp4'):
            self.assertEqual(self.call(path)['code'], 404)
        for path in ('/cinematic/catalog.json', '/cinematic/player.js', '/cinematic/player.css'):
            self.assertEqual(self.call(path, authenticated=False)['code'], 401)

    def test_https_and_local_support_seekable_video(self):
        key = next(iter(clips()))
        path = '/cinematic/'+key+'.mp4'
        expected = cinematic_response(path, 'bytes=12-43')
        result = self.call(path, HTTP_RANGE='bytes=12-43')
        self.assertEqual((result['code'], result['body']), expected[:2])
        self.assertEqual(result['headers']['Content-Range'], dict(expected[3])['Content-Range'])
        self.assertIn("media-src 'self' blob:", result['headers']['Content-Security-Policy'])
        handler = object.__new__(LocalHandler)
        handler.server = SimpleNamespace(assets={}, logger=None)
        handler._context = Mock(return_value=dict(authenticated=True, cookies=[]))
        handler._authorized = Mock(return_value=True)
        handler._send, handler._json = Mock(), Mock()
        handler.path, handler.headers = path, {'Range': 'bytes=12-43'}
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[:3], expected[:3])
        self.assertEqual(handler._send.call_args.args[4], expected[3])
        handler._send.reset_mock(); handler._authorized.return_value=False
        handler.do_GET(); handler._send.assert_not_called()

    def test_player_and_error_do_not_change_schedule_availability(self):
        for path, mime in (('/cinematic/player.js', 'text/javascript'), ('/cinematic/player.css', 'text/css')):
            result = self.call(path)
            self.assertEqual(result['code'], 200)
            self.assertIn(mime, result['headers']['Content-Type'])
            self.assertEqual(self.call(path, HTTP_IF_NONE_MATCH=result['headers']['ETag'])['code'], 304)
        with patch('web_cinematic.cinematic_response', side_effect=ValueError('bad clip')):
            self.assertEqual(self.call('/cinematic/catalog.json')['code'], 503)


if __name__ == '__main__':
    unittest.main()

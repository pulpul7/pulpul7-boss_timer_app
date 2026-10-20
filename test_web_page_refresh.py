"""Offline refresh pacing/receipts; no real web process, Discord or user data."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from web_page_refresh import PageRefresh, browser_refresh_state, stamp_refresh_page
from web_request_limits import DocumentQueue
from web_service_common import write_json
from web_service_manager import WebServiceManager
from web_https_app import HTTPSApplication
from web_live_assets import AssetSnapshot


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.queue = DocumentQueue(clock=lambda: self.now)

    def test_fifo_duplicate_retries_and_global_pacing(self):
        self.assertEqual(self.queue.claim('first'), 0)
        self.assertEqual(self.queue.claim('second'), .25)
        self.assertEqual(self.queue.claim('third'), .5)
        self.assertEqual(self.queue.claim('second'), .25)
        self.assertEqual(list(self.queue.pending), ['second', 'third'])
        self.now += .25
        self.assertEqual(self.queue.claim('third'), .5)  # Cannot pass the earlier browser.
        self.assertEqual(self.queue.claim('second'), 0)
        self.assertEqual(self.queue.claim('third'), .25)
        self.now += .25
        self.assertEqual(self.queue.claim('third'), 0)

    def test_simultaneous_f5_requests_admit_only_one_document(self):
        with ThreadPoolExecutor(max_workers=20) as executor:
            waits = list(executor.map(self.queue.claim, range(100)))
        self.assertEqual(waits.count(0), 1)
        self.assertEqual(len(self.queue.pending), 99)
        self.assertTrue(all(0 < wait < 30 for wait in waits if wait))

    def test_abandoned_browser_expires_and_queue_storage_is_bounded(self):
        self.queue.capacity = 3
        self.queue.claim('first')
        for name in ('abandoned', 'second', 'third'):
            self.queue.claim(name)
        self.assertEqual(self.queue.claim('overflow'), 5)
        self.assertEqual(len(self.queue.pending), 3)
        self.now += 20
        self.assertEqual(self.queue.claim('fresh'), 0)
        self.assertEqual(len(self.queue.pending), 0)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='boss-refresh-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.refresh = PageRefresh(self.root, 'a'*32, None)

    def signal(self, **updates):
        import time
        request = dict(schema=1, run_id='a'*32, id='b'*32, until=time.time()+10)
        request.update(updates)
        write_json(self.refresh.path, request)
        return request

    def test_worker_ack_is_for_exact_valid_signal_only_and_duplicates_do_not_reload_again(self):
        before = self.refresh.token
        self.signal(run_id='c'*32)
        self.assertFalse(self.refresh.poll())
        self.assertEqual(self.refresh.token, before)
        self.signal(until=0)
        self.assertFalse(self.refresh.poll())
        self.signal()
        self.assertTrue(self.refresh.poll())
        self.assertEqual(self.refresh.status()['web_refresh_job'], 'b'*32)
        self.assertEqual(self.refresh.token, 'b'*32)
        self.assertFalse(self.refresh.poll())
        self.signal(until=__import__('time').time()+11)
        self.assertFalse(self.refresh.poll())

    def manager(self):
        manager = object.__new__(WebServiceManager)
        manager.root, manager.run_id = self.root, 'a'*32
        manager.generation, manager.desired = 3, True
        manager.closed = threading.Event()
        manager.page_refresh_lock = threading.Lock()
        manager._log = Mock()
        manager._read_worker_state = lambda: self.refresh.status()
        def publish(path, value):
            write_json(path, value)
            self.refresh.poll()
        return manager, publish

    def test_manager_waits_for_worker_receipt_and_cleans_private_request(self):
        manager, publish = self.manager()
        with patch('web_service_manager.write_json', side_effect=publish):
            message = manager._request_browser_refresh()
        self.assertIn('\uc804\ub2ec \uc644\ub8cc', message)
        self.assertFalse(self.refresh.path.exists())
        self.assertFalse(manager.page_refresh_lock.locked())

    def test_old_worker_and_generation_change_cannot_report_success_or_restart(self):
        manager, publish = self.manager()
        manager._read_worker_state = lambda: {}
        with self.assertRaises(ValueError):
            manager._request_browser_refresh()
        self.assertFalse(self.refresh.path.exists())
        manager._read_worker_state = lambda: self.refresh.status()
        def change(path, value):
            publish(path, value)
            manager.generation += 1
        with patch('web_service_manager.write_json', side_effect=change):
            with self.assertRaises(ValueError):
                manager._request_browser_refresh()
        self.assertFalse(self.refresh.path.exists())
        self.assertFalse(manager.page_refresh_lock.locked())

    def test_maintenance_gate_prevents_new_refresh_work(self):
        manager, _ = self.manager()
        with patch('program_update_guard.web_change', side_effect=ValueError('maintenance')):
            with self.assertRaisesRegex(ValueError, 'maintenance'):
                manager.request_browser_refresh()
        self.assertFalse(self.refresh.path.exists())

    def test_document_stamps_exact_state_before_first_poll_and_rejects_markup(self):
        access = NS(page_refresh=self.refresh)
        snapshot = NS(version='c'*64)
        state = browser_refresh_state(access, snapshot)
        page = stamp_refresh_page(b'<html lang="ko"><head></head></html>', state)
        self.assertIn(('data-web-refresh-token="'+self.refresh.token+'"').encode(), page)
        self.assertIn(b'data-web-page-version="'+b'c'*64+b'"', page)
        self.assertNotIn(b'bad', stamp_refresh_page(b'<html>', dict(web_refresh_token='bad" onclick="bad')))

    def test_https_initial_document_and_existing_status_share_refresh_state_without_auth_changes(self):
        import json
        context = dict(cookies=[], blocked=False, authenticated=True, visitor='v')
        access = NS(context=Mock(return_value=context), read_wait=Mock(return_value=0), page_refresh=self.refresh)
        schedule = NS(status=Mock(return_value=dict(schedule_updated_at='now', schedule_version='s')))
        files = {name: b'asset' for name in ('index.html', 'login.html', 'schedule.js', 'login.js', 'style.css')}
        files['index.html'] = b'<html><head></head></html>'
        source = NS(snapshot=AssetSnapshot.create(files, 'c'*64))
        app = HTTPSApplication('test.duckdns.org', 8766, schedule, access, None,
                               authority_check=lambda: True, asset_source=source)
        def get(path):
            response = {}
            def start(status, headers): response.update(code=int(status.split()[0]), headers=headers)
            body = app(dict(REQUEST_METHOD='GET', PATH_INFO=path, HTTP_HOST='test.duckdns.org:8766',
                            REMOTE_ADDR='192.0.2.1', **{'wsgi.url_scheme':'https'}), start)
            response['body'] = b''.join(body)
            return response
        with patch('web_font_library.font_developer_allowed', return_value=False):
            page = get('/')
        old = self.refresh.token
        self.assertEqual(page['code'], 200)
        self.assertIn(old.encode(), page['body'])
        first = json.loads(get('/api/status')['body'])
        self.assertEqual(first['web_refresh_token'], old)
        self.assertEqual(first['web_page_version'], 'c'*64)
        self.signal();self.refresh.poll()
        updated = get('/api/status')
        self.assertEqual(json.loads(updated['body'])['web_refresh_token'], 'b'*32)
        self.assertFalse(any(name == 'Set-Cookie' for name, _ in updated['headers']))


if __name__ == '__main__':
    unittest.main()

"""Offline manual-update completion receipts; never downloads or starts a web worker."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from web_page_updater import WebPageUpdater


class UpdateCompletionTests(unittest.TestCase):
    def updater(self):
        folder = tempfile.TemporaryDirectory(prefix='boss-web-update-completion-unit-')
        self.addCleanup(folder.cleanup)
        manager = SimpleNamespace(root=Path(folder.name), closed=threading.Event(), _log=Mock())
        updater = WebPageUpdater(manager)
        updater.settings['enabled'] = False
        updater.due = lambda _: False
        def stop():
            updater.close()
            if updater.thread is not None:
                updater.thread.join(2)
        self.addCleanup(stop)
        return updater

    def test_waiter_does_not_finish_before_apply_finishes(self):
        updater = self.updater()
        applying, release = threading.Event(), threading.Event()
        result, errors = [], []
        def update():
            updater.view.update(state='restarting', message='웹서비스 재시작 중')
            applying.set()
            if not release.wait(2):
                raise ValueError('fixture apply timeout')
            updater.view.update(state='applied', message='웹 업데이트 적용 완료', version='test-version')
        updater._update = update
        def wait():
            try:
                result.append(updater.request_and_wait(timeout=2))
            except Exception as exc:
                errors.append(exc)
        waiter = threading.Thread(target=wait)
        waiter.start()
        try:
            self.assertTrue(applying.wait(1))
            self.assertTrue(waiter.is_alive())
            self.assertEqual(result, [])
            release.set()
            waiter.join(2)
            self.assertFalse(waiter.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(result[0]['state'], 'applied')
            self.assertEqual(result[0]['version'], 'test-version')
        finally:
            release.set()
            waiter.join(2)

    def test_failure_cannot_reuse_previous_success(self):
        updater = self.updater()
        updater.view.update(state='applied', message='previous success')
        updater._update = Mock(side_effect=ValueError('파일 검증 실패'))
        with self.assertRaisesRegex(ValueError, '파일 검증 실패'):
            updater.request_and_wait(timeout=2)
        updater._update.assert_called_once()

    def test_already_current_is_success_without_apply(self):
        updater = self.updater()
        updater._update = lambda: updater.view.update(state='current', message='최신 웹 파일입니다.')
        result = updater.request_and_wait(timeout=2)
        self.assertEqual(result['state'], 'current')
        self.assertFalse(result['busy'])

    def test_overlapping_manual_update_is_rejected(self):
        updater = self.updater()
        updater.view['busy'] = True
        with self.assertRaisesRegex(ValueError, '진행 중'):
            updater.request_and_wait(timeout=2)
        self.assertIsNone(updater.manual_completion)

    def test_closed_program_never_queues_update(self):
        updater = self.updater()
        updater.manager.closed.set()
        with self.assertRaisesRegex(ValueError, '종료 중'):
            updater.request_and_wait(timeout=2)
        self.assertFalse(updater.manual)


if __name__ == '__main__':
    unittest.main()

from datetime import datetime
from types import SimpleNamespace as NS
import threading
import unittest

from notice_web_publication import schedule_published
from web_delegated_hosting import edit_hash


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.payload = dict(captured_at='2026-10-17T03:00:01+00:00', season='18', server_name='test', events=[])
        self.manager = NS(closed=threading.Event(), lock=threading.RLock(), pending_snapshot=self.payload,
                          snapshot_version=10, written_snapshot_version=10, snapshot_updated_at='published',
                          view=dict(state='running', schedule_state='ready', schedule_updated_at='published'))
        self.app = NS(web_service_manager=self.manager, current_season_no=18, schedule_server_profile_id='test',
                      schedule_last_import_meta=dict(imported_at=datetime(2026, 10, 17, 12)),
                      _get_schedule_share_server_name=lambda: 'test')

    def test_local_requires_worker_consumption_not_just_capture_or_disk_write(self):
        self.manager.written_snapshot_version = 9
        self.assertFalse(schedule_published(self.app))
        self.manager.written_snapshot_version = 10
        self.manager.view['schedule_updated_at'] = 'older'
        self.assertFalse(schedule_published(self.app))
        self.manager.view['schedule_updated_at'] = 'published'
        self.assertTrue(schedule_published(self.app))

    def test_old_capture_wrong_server_and_wrong_season_do_not_qualify(self):
        for changes in [dict(captured_at='2026-10-17T02:59:59+00:00'), dict(server_name='other'), dict(season='17')]:
            self.manager.pending_snapshot = dict(self.payload, **changes)
            self.assertFalse(schedule_published(self.app))

    def test_receipt_is_stable_between_captures_but_resets_on_next_import(self):
        self.assertTrue(schedule_published(self.app))
        self.manager.snapshot_version += 1
        self.assertTrue(schedule_published(self.app))
        self.app.schedule_last_import_meta['imported_at'] = datetime(2026, 10, 17, 12, 1)
        self.assertFalse(schedule_published(self.app))

    def test_remote_upload_is_not_publication_until_matching_ack(self):
        relay = self.manager.delegation = NS(lock=threading.RLock(), role='source', local_payload=self.payload,
                                           pending=dict(uploaded=True), sent_hash='old')
        self.assertFalse(schedule_published(self.app))
        relay.pending = None
        self.assertFalse(schedule_published(self.app))
        relay.sent_hash = edit_hash(self.payload)
        self.assertTrue(schedule_published(self.app))

    def test_host_of_other_admin_and_stopped_service_do_not_confirm_local_input(self):
        self.manager.delegation = NS(lock=threading.RLock(), role='host')
        self.assertFalse(schedule_published(self.app))
        self.manager.delegation.role = ''
        self.manager.view['state'] = 'stopped'
        self.assertFalse(schedule_published(self.app))
        self.manager.view['state'] = 'running'
        self.manager.closed.set()
        self.assertFalse(schedule_published(self.app))

    def test_old_host_without_import_marker_has_no_false_receipt(self):
        self.app.schedule_last_import_meta = None
        self.assertFalse(schedule_published(self.app))


if __name__ == '__main__':
    unittest.main()

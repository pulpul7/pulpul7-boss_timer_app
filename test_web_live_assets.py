"""Offline static swaps: same worker/session, exact receipts and rollback."""
from io import BytesIO
import itertools
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

from web_asset_store import git_hash, stage_version, version_id
from web_https_app import HTTPSApplication
from web_live_assets import AssetSnapshot, LiveWebAssets, asset_request_path
from web_service_common import read_json, write_json
from web_service_manager import WebServiceManager
from web_service_worker import LocalHandler


def files(marker):
    return dict(zip(('index.html','style.css','schedule.js','login.html','login.js','metadata.json'),
                    (b'<html>/schedule.js /style.css '+marker+b'</html>', marker+b' css', marker+b' js',
                     b'<html>/login.js /style.css</html>', b'login js',
                     b'{"schema":1,"asset_api":1,"login_api":2,"page_revision":132}')))


def identity(data):
    return version_id({name: git_hash(raw) for name, raw in data.items()})


class LiveAssetsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='boss-live-assets-unit-')
        self.addCleanup(temporary.cleanup)
        self.root, self.run_id = Path(temporary.name), 'a'*32
        self.old, self.new = files(b'old'), files(b'new')
        with patch('web_live_assets.load_assets', return_value=self.old):
            self.live = LiveWebAssets(self.root, self.run_id, None)
        stage_version(self.root, identity(self.new), self.new)

    def request(self, version=None, **changes):
        data = dict(schema=1, id=uuid.uuid4().hex, run_id=self.run_id, version=version or identity(self.new),
                    action='apply', until=time.time()+15)
        data.update(changes)
        write_json(self.live.path, data)
        return data

    def test_swap_and_rollback_use_same_shared_object_and_keep_old_snapshot_immutable(self):
        old = self.live.snapshot
        command = self.request()
        self.assertTrue(self.live.poll())
        self.assertEqual(self.live.snapshot.files, self.new)
        self.assertEqual(self.live.status()['asset_job_id'], command['id'])
        self.assertFalse(self.live.poll())
        self.assertEqual(old.files, self.old)
        with self.assertRaises(TypeError):old.files['schedule.js'] = b'changed'
        self.request(identity(self.old), action='rollback')
        self.live.poll()
        self.assertIs(self.live.snapshot, old)

    def test_wrong_worker_expired_and_incompatible_assets_keep_current_pages(self):
        original = self.live.snapshot
        for changes in ({'run_id':'b'*32}, {'until':time.time()-1}, {'version':'not-a-version'}):
            self.request(**changes);self.live.poll()
            self.assertIs(self.live.snapshot, original)
            self.assertEqual(self.live.status()['asset_job_state'], 'error')
        incompatible = dict(self.new, **{'metadata.json':b'{"schema":1,"asset_api":99,"login_api":2}'})
        target = identity(incompatible)
        folder = self.root/'pages'/'versions'/target;folder.mkdir()
        for name, raw in incompatible.items():(folder/name).write_bytes(raw)
        self.request(target);self.live.poll()
        self.assertIs(self.live.snapshot, original)

    def test_superseded_command_cannot_publish_after_rollback_or_cancellation(self):
        original = self.live.snapshot
        self.request()
        from web_asset_store import read_version
        def supersede(root, version):
            data = read_version(root, version)
            self.request(identity(self.old), action='rollback')
            return data
        with patch('web_live_assets.read_version', side_effect=supersede):
            self.assertFalse(self.live.poll())
        self.assertIs(self.live.snapshot, original)
        self.live.poll()
        self.assertIs(self.live.snapshot, original)

    def app(self, source=None):
        context = dict(authenticated=True, available=True, blocked=False, cookies=[], visitor='c'*32)
        self.access = NS(context=Mock(return_value=context), read_wait=Mock(return_value=0))
        return HTTPSApplication('test.duckdns.org',8766,NS(),self.access,None,asset_source=source or self.live)

    def response(self, app, etag=None):
        env = dict(REQUEST_METHOD='GET',PATH_INFO='/schedule.js',REMOTE_ADDR='127.0.0.1',
                   HTTP_HOST='test.duckdns.org:8766',HTTP_COOKIE='same-session',
                   **{'wsgi.url_scheme':'https','wsgi.input':BytesIO()})
        if etag:env['HTTP_IF_NONE_MATCH']=etag
        result={}
        def start(status, headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        result['body']=b''.join(app(env,start));return result

    def test_https_serves_new_bytes_and_etag_with_same_app_and_access_session(self):
        app=self.app();before=self.response(app);access=app.access
        self.assertEqual(self.response(app,before['headers']['ETag'])['status'],304)
        self.request();self.live.poll()
        after=self.response(app,before['headers']['ETag'])
        self.assertEqual(after['status'],200);self.assertEqual(after['body'],self.new['schedule.js'])
        self.assertNotEqual(after['headers']['ETag'],before['headers']['ETag'])
        self.assertIs(app.access,access);self.assertNotIn('Set-Cookie',after['headers'])

    def test_each_request_uses_one_snapshot_for_body_and_etag(self):
        old=self.live.snapshot;new=AssetSnapshot.create(self.new,identity(self.new))
        class Switching:
            calls=0
            @property
            def snapshot(inner):
                inner.calls+=1;return old if inner.calls==1 else new
        source=Switching();app=self.app(source)
        reply=self.response(app)
        self.assertEqual(source.calls,1)
        self.assertEqual(reply['body'],old.files['schedule.js'])
        self.assertEqual(reply['headers']['ETag'],old.etags['/schedule.js'])

    def test_local_listener_also_uses_shared_assets_without_recreating_server(self):
        handler=LocalHandler.__new__(LocalHandler)
        handler.path='/style.css';handler.client_address=('127.0.0.1',123)
        handler._context=Mock(return_value=dict(authenticated=True,cookies=[]))
        handler._send=Mock()
        handler.server=NS(assets=self.old,asset_source=self.live)
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[1],self.old['style.css'])
        self.request();self.live.poll();handler.do_GET()
        self.assertEqual(handler._send.call_args.args[1],self.new['style.css'])

    def manager(self, capability=True):
        m=WebServiceManager.__new__(WebServiceManager)
        m.root=self.root;m.lock=threading.RLock();m.closed=threading.Event()
        m.generation=7;m.desired=True;m.settings={'port':8765};m.run_id=self.run_id
        m.process=NS(pid=123,poll=lambda:None);m.public=NS(send=Mock())
        m._update=Mock();m._log=Mock();m._monitor=Mock();m._flush_authority=Mock()
        m._stop_owned=Mock(return_value=True);m._start=Mock();m._wait_page_start=Mock()
        def state():
            self.live.poll()
            value=dict(self.live.status(),state='running')
            if not capability:value.pop('web_asset_reload')
            return value
        m._read_worker_state=state
        job=dict(version=identity(self.new),done=threading.Event(),cancelled=threading.Event(),error=None)
        m.page_update_job=job
        return m,job

    def test_manager_commits_only_after_matching_live_receipt_without_restart(self):
        m,job=self.manager();m._page_update_jobs_locked()
        self.assertIsNone(job['error']);self.assertTrue(job['done'].is_set())
        self.assertEqual(job['mode'],'hot')
        self.assertEqual(read_json(self.root/'pages/current.json')['version'],identity(self.new))
        self.assertEqual(self.live.snapshot.files,self.new)
        m._stop_owned.assert_not_called();m._start.assert_not_called();m._wait_page_start.assert_not_called()
        m._flush_authority.assert_not_called()
        self.assertFalse(asset_request_path(self.root,self.run_id).exists())

    def test_pointer_write_failure_restores_memory_without_stopping_web(self):
        m,job=self.manager()
        original=self.live.snapshot
        def fail_pointer(path,data):
            if path==self.root/'pages/current.json':raise OSError('fixture disk error')
            write_json(path,data)
        with patch('web_service_manager.write_json',side_effect=fail_pointer):m._page_update_jobs_locked()
        self.assertIsNotNone(job['error']);self.assertIs(self.live.snapshot,original)
        self.assertFalse((self.root/'pages/current.json').exists())
        m._stop_owned.assert_not_called();m._start.assert_not_called()

    def test_timeout_revokes_pending_apply_and_rolls_back_without_restart(self):
        m,job=self.manager();original=self.live.snapshot;normal=m._read_worker_state
        def lost_receipt():
            state=normal()
            request=read_json(self.live.path) if self.live.path.exists() else {}
            if request.get('action')=='apply':state.pop('asset_job_id',None)
            return state
        m._read_worker_state=lost_receipt
        with patch('web_service_manager.time.monotonic',side_effect=itertools.count(0,2.1)):
            m._page_update_jobs_locked()
        self.assertIsNotNone(job['error']);self.assertIs(self.live.snapshot,original)
        self.assertFalse(self.live.path.exists())
        m._stop_owned.assert_not_called();m._start.assert_not_called()

    def test_legacy_worker_restarts_once_and_checks_actual_readiness(self):
        m,job=self.manager(capability=False);m._page_update_jobs_locked()
        self.assertIsNone(job['error']);self.assertEqual(job['mode'],'restart')
        m._stop_owned.assert_called_once();m._start.assert_called_once_with(8765,7)
        m._wait_page_start.assert_called_once_with(7,identity(self.new))

    def test_failure_after_commit_restores_previous_pointer_and_memory(self):
        stage_version(self.root,identity(self.old),self.old)
        pointer=self.root/'pages/current.json'
        before=dict(schema=1,version=identity(self.old),previous='')
        write_json(pointer,before)
        m,job=self.manager();original=self.live.snapshot
        m._monitor.side_effect=ValueError('fixture post-commit failure')
        m._page_update_jobs_locked()
        self.assertIsNotNone(job['error'])
        self.assertEqual(read_json(pointer),before)
        self.assertIs(self.live.snapshot,original)
        m._stop_owned.assert_not_called();m._start.assert_not_called()

    def test_incompatible_live_protocol_is_rejected_without_stopping_web(self):
        m,job=self.manager();normal=m._read_worker_state
        m._read_worker_state=lambda:dict(normal(),web_asset_api=99)
        m._page_update_jobs_locked()
        self.assertIsNotNone(job['error']);m._stop_owned.assert_not_called();m._start.assert_not_called()


if __name__=='__main__':unittest.main()

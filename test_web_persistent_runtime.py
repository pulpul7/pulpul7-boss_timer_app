"""Persistent web lifecycle checks. No servers, child processes or provider calls."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from web_service_common import PROTOCOL, timestamp, write_json, read_json, worker_paths, schedule_path
from web_persistent_runtime import (WorkerRuntime, RemotePublicService, RUNTIME_PROTOCOL,
    control_path, discover, save_background_context, independent_worker_spawn)
from web_service_manager import WebServiceManager
from web_schedule_cache import ScheduleCache
from web_standalone_hosting import web_host_stamp, StandaloneHosting

RUN='a'*32; CLIENT='b'*32; SESSION='c'*32; WEB='d'*32


class PersistentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='boss-persistent-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'web_service'; self.root.mkdir()

    def manager(self):
        m=WebServiceManager(self.root.parent)
        m._log=Mock(); m._ensure_coordinator=Mock()
        self.addCleanup(lambda: m.close())
        return m

    def state(self, **extra):
        return dict(run_id=RUN,pid=23456,port=8765,protocol=PROTOCOL,runtime_protocol=1,
            state='running',checked_at=timestamp(),heartbeat=1,runtime_version='old',
            public_status=dict(state='prepared',enabled=True,address='https://unit.duckdns.org:8766',busy=False),
            **extra)

    def runtime(self, control=None):
        r=WorkerRuntime(self.root,RUN,None,lambda: {})
        for service in (r.public,r.ddns,r.certificate):
            service.set_active=Mock()
        r.public.status=Mock(return_value=dict(state='prepared',busy=False))
        r.next_remote=float('inf')
        r._still_background=Mock(return_value=True)
        self.addCleanup(r.close)
        if control is not None:write_json(control_path(self.root,RUN),control)
        return r

    def test_discovery_requires_matching_fresh_heartbeat_and_private_run_id(self):
        write_json(self.root/'persistent_worker.json',dict(run_id=RUN,pid=23456,port=8765,runtime_protocol=1))
        path=worker_paths(self.root,RUN)[0]
        write_json(path,self.state())
        self.assertEqual(discover(self.root)['pid'],23456)
        data=self.state();data['checked_at']=(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()
        write_json(path,data);self.assertIsNone(discover(self.root))
        data=self.state();data['pid']=999;write_json(path,data);self.assertIsNone(discover(self.root))

    def test_version_difference_reattaches_without_terminating_or_spawning(self):
        m=self.manager();m._monitor=Mock()
        process=Mock(pid=23456);process.poll.return_value=None
        state=self.state();answer=dict(state,controller_id=m.public.controller_id)
        m._read_worker_state=Mock(return_value=answer)
        write_json(self.root/('background.'+RUN+'.json'),dict(web_id=WEB))
        write_json(schedule_path(self.root,RUN),dict(revision=7,updated_at=timestamp()))
        with patch('web_service_manager.discover',return_value=state), \
             patch('web_service_manager.AttachedProcess',return_value=process), \
             patch('web_service_manager.runtime_version',return_value='new'), \
             patch('web_service_manager.subprocess.Popen') as spawn:
            m._discover_existing()
        self.assertIs(m.process,process);self.assertEqual(m.run_id,RUN)
        self.assertTrue(m.background_protected);self.assertTrue(m.status()['runtime_update_pending'])
        process.terminate.assert_not_called();spawn.assert_not_called()
        self.assertEqual(m.background_web_id,WEB)
        m.preserve_worker=True;m.process=None

    def test_gui_detach_preserves_worker_and_active_public_control(self):
        m=self.manager();m.discovery_pending=False;m.run_id=RUN
        m.process=Mock(pid=23456);m.process.poll.return_value=None
        m.desired=True;m.background_protected=True;m.public.view.update(state='prepared')
        m._flush_snapshot=Mock();m._flush_authority=Mock();m._stop_owned=Mock()
        m.close(preserve_web=True)
        control=read_json(control_path(self.root,RUN))
        self.assertEqual(control['controller_until'],0)
        self.assertTrue(control['public_active']);self.assertTrue(m.preserve_worker)
        m._flush_authority.assert_called_once_with(force=True);m._stop_owned.assert_not_called()

    def test_temporary_public_off_is_not_sent_back_as_an_off_command(self):
        m=self.manager();m.discovery_pending=False;m.run_id=RUN;m.desired=True
        m.process=Mock(pid=23456);m.process.poll.return_value=None
        m.background_protected=True;m.process_generation=m.generation
        state=self.state();state['public_status']['state']='off'
        m._read_worker_state=Mock(return_value=state)
        m.public.view.update(state='prepared');m._monitor()
        control=read_json(control_path(self.root,RUN))
        self.assertTrue(control['public_active']);self.assertTrue(control['keep_background'])
        self.assertEqual(m.public.status()['state'],'off')
        r=self.runtime(control);r.remote_initialized=True;r.remote_valid_until=0
        r.refresh();r.public.set_active.assert_called_with(False)
        r.remote_valid_until=time.monotonic()+15
        r.refresh();r.public.set_active.assert_called_with(True)
        m.process=None

    def test_reconnect_recovers_retained_web_intent_even_if_latest_status_is_off(self):
        m=self.manager();m._monitor=Mock()
        process=Mock(pid=23456);process.poll.return_value=None
        state=self.state(background=True,background_authorized=True)
        state['public_status']['state']='off'
        # Older controllers confused a transient OFF status with an OFF command.
        write_json(control_path(self.root,RUN),dict(schema=1,run_id=RUN,
            keep_background=True,public_active=False,public_enabled=True))
        m._read_worker_state=Mock(return_value=dict(state,controller_id=m.public.controller_id))
        write_json(schedule_path(self.root,RUN),dict(revision=1,updated_at=timestamp()))
        write_json(self.root/('background.'+RUN+'.json'),dict(web_id=WEB))
        with patch('web_service_manager.discover',return_value=state), \
             patch('web_service_manager.AttachedProcess',return_value=process), \
             patch('web_service_manager.subprocess.Popen') as spawn:
            m._discover_existing()
        self.assertTrue(m.background_protected);self.assertTrue(m.authority_ready)
        self.assertTrue(read_json(control_path(self.root,RUN))['public_active'])
        process.terminate.assert_not_called();spawn.assert_not_called()
        m.process=None

    def test_background_intent_respects_explicit_public_off(self):
        m=self.manager();m.discovery_pending=False;m.run_id=RUN;m.desired=True
        m.process=Mock();m.background_protected=True;m.public.view.update(state='prepared')
        m.public.configure(False)
        self.assertFalse(read_json(control_path(self.root,RUN))['public_enabled'])
        m.public_paused=True;m.public.send(force=True)
        self.assertFalse(read_json(control_path(self.root,RUN))['public_active'])
        m.process=None

    def test_explicit_close_does_not_detach_or_keep_public_authority(self):
        m=self.manager();m.discovery_pending=False;m.run_id=RUN
        m._flush_authority=Mock();m.close()
        self.assertFalse(m.preserve_worker)
        m._flush_authority.assert_called_once_with(force=True)

    def test_stop_after_reattach_clears_stale_public_cache_without_waiting(self):
        m = self.manager();m.discovery_pending = False;m.run_id = RUN
        process = Mock(pid=23456);process.poll.return_value = 0
        m.process = process;m.desired = True;m.background_protected = True
        m.view.update(state='running', enabled=True, persistent=True, runtime_update_pending=True)
        m.public.view.update(state='prepared', busy=True, address='https://example.invalid')
        saved = dict(m.public.settings)
        write_json(self.root/'public_listener.json', dict(schema=1, enabled=False))
        manual = StandaloneHosting(NS(manager=m, app=NS(discord_handover=None)))
        original = m.emergency_stop
        def stop(**kwargs):
            original(**kwargs)
            self.assertTrue(m._stop_owned())
            m._update(state='stopped')
        m.emergency_stop = stop
        with patch('web_standalone_hosting.time.sleep', side_effect=AssertionError('unexpected stop wait')):
            manual.stop_sync()
        self.assertEqual(manual.stage, 'stopped')
        self.assertEqual(manual.status()['operation'], 'stop')
        self.assertEqual(m.public.status()['state'], 'off')
        self.assertFalse(m.public.status()['busy'])
        self.assertEqual(m.public.status()['address'], '')
        self.assertEqual(m.public.settings, saved)
        self.assertFalse(m.status()['runtime_update_pending'])
        self.assertIsNone(m.process)

    def test_dead_worker_does_not_hide_unremoved_public_resources(self):
        m = self.manager();m.run_id = RUN
        process = Mock(pid=23456);process.poll.return_value = 0;m.process = process
        m.public.view.update(state='prepared', busy=True)
        write_json(self.root/'public_resources.json', dict(schema=1, mapping='still-owned'))
        self.assertTrue(m._stop_owned())
        self.assertEqual(m.public.status()['state'], 'error')
        self.assertFalse(m.public.status()['busy'])
        self.assertTrue((self.root/'public_resources.json').exists())

    def test_shutdown_cache_requires_process_and_disabled_listener(self):
        m = self.manager();m.run_id = RUN
        process = Mock(pid=23456);process.poll.return_value = None;m.process = process
        m.public.view.update(state='prepared')
        self.assertFalse(m.public.worker_stopped(process))
        self.assertEqual(m.public.status()['state'], 'prepared')
        process.poll.return_value = 0
        write_json(self.root/'public_listener.json', dict(schema=1, enabled=True))
        self.assertFalse(m.public.worker_stopped(process))
        self.assertEqual(m.public.status()['state'], 'error')
        write_json(self.root/'public_listener.json', dict(schema=1, enabled=False))
        self.assertTrue(m.public.worker_stopped(process))
        self.assertEqual(m.public.status()['state'], 'off')
        m.process = None

    def test_detached_worker_keeps_bounded_authority_and_network_maintenance(self):
        r=self.runtime(dict(schema=1,run_id=RUN,controller_id='one',controller_until=0,
                            public_active=True,public_enabled=True))
        r.remote_valid_until=time.monotonic()+15
        r.refresh()
        lease=read_json(self.root/'authority_lease.json')
        self.assertTrue(r.detached);self.assertTrue(lease['enabled'])
        self.assertLessEqual(lease['valid_until'],time.time()+10)
        r.public.set_active.assert_called_with(True);r.ddns.set_active.assert_called_with(True)
        r.certificate.set_active.assert_called_with(True)

    def test_expired_remote_proof_cannot_renew_public_authority(self):
        r=self.runtime(dict(schema=1,run_id=RUN,controller_until=0,public_active=True,public_enabled=True))
        r.remote_valid_until=0;r.refresh()
        r.public.set_active.assert_called_with(False)
        self.assertFalse((self.root/'authority_lease.json').exists())

    def test_first_background_check_preserves_existing_lease_without_extending_it(self):
        r=self.runtime(dict(schema=1,run_id=RUN,controller_until=0,public_active=True,public_enabled=True))
        lease=dict(schema=1,run_id=RUN,enabled=True,valid_until=time.time()+8)
        write_json(self.root/'authority_lease.json',lease)
        r.refresh();r.public.set_active.assert_called_with(True)
        self.assertEqual(read_json(self.root/'authority_lease.json'),lease)
        r.remote_initialized=True;r.remote_valid_until=0;r.refresh()
        self.assertFalse(read_json(self.root/'authority_lease.json')['enabled'])

    def test_another_live_gui_cannot_replace_controller(self):
        r=self.runtime(dict(schema=1,run_id=RUN,controller_id='second',controller_until=time.time()+5))
        r.control=dict(schema=1,run_id=RUN,controller_id='first',controller_until=time.time()+5)
        with patch('web_authority_gate.external_allowed',return_value=False):r.refresh()
        self.assertEqual(r.control['controller_id'],'first')

    def test_reconnecting_gui_keeps_background_guard_until_owner_verified(self):
        r=self.runtime(dict(schema=1,run_id=RUN,controller_id='new',controller_until=time.time()+5,
                            keep_background=True,public_active=True,public_enabled=True))
        r.remote_valid_until=time.monotonic()+15;r.refresh()
        self.assertTrue(r.detached);r.public.set_active.assert_called_with(True)

    def test_remote_handover_acknowledges_only_after_cleanup(self):
        r=self.runtime();c=dict(client=CLIENT,runtime=SESSION,web_id=WEB)
        data=dict(scope=dict(guild='guild'),members={CLIENT:dict(runtime=SESSION)},
            web_standalone=dict(protocol=1,id=WEB,owner=CLIENT,runtime=SESSION,state='active',guild='guild',server='s',season='18'),
            web_background=dict(stop_requested='request'))
        r._load_config=Mock(return_value=c);r.record=Mock()
        r.record.read.return_value=(data,'sha')
        r.public.status.return_value=dict(state='off',busy=False)
        r._remote_check()
        self.assertTrue(r.stop_requested)
        r.public.set_active.assert_called_once_with(False)
        written=r.record.write.call_args.args[0]
        self.assertEqual(written['web_standalone']['state'],'stopped')
        self.assertEqual(written['members'][CLIENT]['web_closed_runtime'],SESSION)
        self.assertNotIn('web_background',written)

    def test_marker_write_conflict_keeps_successfully_verified_owner(self):
        r=self.runtime(dict(schema=1,run_id=RUN,controller_until=0,
                            public_active=True,public_enabled=True))
        r._load_config=Mock(return_value=dict(client=CLIENT,runtime=SESSION,web_id=WEB))
        data=dict(scope=dict(guild='guild'),web_standalone=dict(protocol=1,id=WEB,owner=CLIENT,
            runtime=SESSION,state='active',guild='guild',server='s',season='18'))
        r.record=Mock();r.record.read.return_value=(data,'sha')
        r.record.write.side_effect=RuntimeError('optimistic write conflict')
        r.next_marker=0;r._remote_check()
        self.assertTrue(r.remote_initialized);self.assertFalse(r.remote_stop)
        self.assertGreater(r.remote_valid_until,time.monotonic())
        self.assertLessEqual(r.remote_valid_until,time.monotonic()+15)
        self.assertEqual(r.remote_failures,0)
        self.assertLessEqual(r.next_marker,time.monotonic()+5)
        r.refresh();r.public.set_active.assert_called_with(True)

    def test_failed_owner_read_does_not_extend_public_authority(self):
        r=self.runtime()
        r._load_config=Mock(return_value=dict(client=CLIENT,runtime=SESSION,web_id=WEB))
        r.record=Mock();r.record.read.side_effect=OSError('offline')
        r._remote_check()
        self.assertEqual(r.remote_valid_until,0)
        self.assertFalse(r.remote_initialized);self.assertEqual(r.remote_failures,1)

    def test_rewriting_identical_settings_does_not_restart_certificate_maintenance(self):
        r=self.runtime(dict(schema=1,run_id=RUN,controller_until=0,
                            public_active=True,public_enabled=True))
        r.remote_valid_until=time.monotonic()+15
        settings=self.root/'https_settings.json'
        write_json(settings,dict(enabled=True,domain='unit.duckdns.org'))
        r.service_signatures['certificate']=hashlib.sha256(settings.read_bytes()).digest()
        certificate=r.certificate;certificate.close=Mock(wraps=certificate.close)
        r.refresh();certificate.close.assert_not_called()
        signature=r.service_signatures['certificate']
        write_json(settings,dict(enabled=True,domain='unit.duckdns.org'))
        r.refresh();certificate.close.assert_not_called()
        self.assertIs(r.certificate,certificate)
        self.assertEqual(r.service_signatures['certificate'],signature)

    def test_late_cleanup_does_not_erase_new_remote_owner(self):
        r=self.runtime();r._load_config=Mock(return_value=dict(client=CLIENT,runtime=SESSION,web_id=WEB))
        data=dict(scope=dict(guild='guild'),web_standalone=dict(protocol=1,id='e'*32,owner='f'*32,
            runtime=SESSION,state='active',guild='guild',server='s',season='18'))
        r.record=Mock();r.record.read.return_value=(data,'sha');r.public.status.return_value=dict(state='off',busy=False)
        r._remote_check();r.record.write.assert_not_called();self.assertTrue(r.stop_requested)

    def test_detached_schedule_uses_original_capture_and_expires_at_window_end(self):
        cache=ScheduleCache(self.root,RUN,None)
        now=datetime.now(timezone.utc)
        cache.data=dict(captured_at=(now-timedelta(hours=1)).isoformat(),window_end=(now+timedelta(hours=2)).isoformat())
        original=deepcopy(cache.data);cache.last_capture_at=time.monotonic()-3600
        self.assertTrue(cache._stale(now));cache.detached=True
        self.assertFalse(cache._stale(now));self.assertEqual(cache.data,original)
        self.assertTrue(cache._stale(now+timedelta(hours=3)))
        cache.read_failed=True;self.assertTrue(cache._stale(now))

    def test_worker_main_persistent_mode_does_not_watch_or_require_parent(self):
        from web_service_worker import main
        server=Mock(counter_lock=threading.Lock(),request_count=0)
        server.handle_request.side_effect=lambda: worker_paths(self.root,RUN)[1].touch()
        runtime=Mock(stop_requested=False,detached=True)
        runtime.status.return_value=dict(runtime_protocol=1,runtime_version='test',background_authorized=True)
        cache=Mock();cache.status.return_value={}
        access=Mock();access.summary.return_value={}
        https=Mock();https.status.return_value={}
        with patch('web_service_worker.ParentWatch') as parent, \
             patch('web_service_worker.ScheduleCache',return_value=cache), \
             patch('web_service_worker.AccessRuntime',return_value=access), \
             patch('web_service_worker.LocalWebServer',return_value=server), \
             patch('web_service_worker.HTTPSListener',return_value=https), \
             patch('web_live_assets.load_assets',return_value={name:b'unit' for name in
                 ('index.html','style.css','schedule.js','login.html','login.js','metadata.json')}), \
             patch('web_service_worker.diagnostic_logger',return_value=None), \
             patch('web_service_worker.diagnostic') as log, \
             patch('web_service_worker.close_logger'), \
             patch('web_push.PushRuntime'), \
             patch('web_persistent_runtime.WorkerRuntime',return_value=runtime):
            result=main(['--root',str(self.root),'--run-id',RUN,'--parent-pid','99999999',
                         '--port','8765','--persistent'])
        self.assertEqual(result,0,str(log.call_args_list));parent.assert_not_called()
        server.handle_request.assert_called_once();runtime.close.assert_called_once()

    def test_stale_remote_reply_is_discarded_after_gui_reconnect(self):
        r=self.runtime()
        config=dict(client=CLIENT,runtime=SESSION,web_id=WEB)
        write_json(self.root/('background.'+RUN+'.json'),config)
        write_json(control_path(self.root,RUN),dict(controller_until=time.time()+5,keep_background=False))
        self.assertFalse(WorkerRuntime._still_background(r,config))
        write_json(control_path(self.root,RUN),dict(controller_until=0))
        self.assertTrue(WorkerRuntime._still_background(r,config))
        write_json(self.root/('background.'+RUN+'.json'),dict(config,runtime='f'*32))
        self.assertFalse(WorkerRuntime._still_background(r,config))

    def test_freeze_spawn_owns_extraction_and_never_builds_an_executable(self):
        m=self.manager();m.discovery_pending=False;m._update=Mock()
        with patch('web_service_manager.subprocess.Popen') as spawn, \
             patch('web_service_manager.sys.frozen',True,create=True):
            m._start(8765,m.generation)
        kwargs=spawn.call_args.kwargs
        self.assertEqual(kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'],'1')
        self.assertIn('--persistent',spawn.call_args.args[0])
        self.assertIs(kwargs['stdin'],subprocess.DEVNULL)
        m.process=None

    def test_reconnection_keeps_independent_web_identity_and_encrypts_credentials(self):
        old=dict(protocol=1,id=WEB,owner=CLIENT,runtime=SESSION,state='active',guild='guild',server='s',season='18')
        data=dict(scope=dict(guild='guild'),web_standalone=old,members={CLIENT:dict(runtime='e'*32,online=True)})
        def mutate(fn):fn(data);return deepcopy(data)
        coordinator=NS(alive=True,client=CLIENT,runtime='e'*32,scope=dict(guild='guild',server='s',season='18'),
            lock=threading.RLock(),record=NS(path='data/handover/unit.json',read=lambda:(deepcopy(data),'sha'),mutate=mutate))
        manual=NS(record=deepcopy(data))
        manual.observe=lambda value,_:setattr(manual,'record',deepcopy(value))
        settings=dict(owner='owner',repo='repo',branch='main',token='private-unit-token')
        bridge=NS(app=NS(discord_handover=coordinator,_get_github_data_settings=lambda:settings),standalone=manual)
        m=NS(root=self.root,run_id=RUN,background_protected=True,background_scope_verified=False,
            background_web_id=WEB,background_binding=None,snapshot_version=1,
            publication_allowed=lambda:True,public=NS(status=lambda:dict(state='prepared')),_log=Mock())
        with patch('web_secret_store.protect_secret',return_value='encrypted-only'):
            save_background_context(m,bridge)
        self.assertTrue(m.background_protected);self.assertTrue(m.background_scope_verified)
        self.assertEqual(data['web_standalone']['runtime'],SESSION)
        config=read_json(self.root/('background.'+RUN+'.json'))
        self.assertEqual(config['runtime'],SESSION)
        self.assertEqual(config['protected_token'],'encrypted-only')
        self.assertNotIn(settings['token'],(self.root/('background.'+RUN+'.json')).read_text())

    def test_unverified_profile_cannot_overwrite_background_snapshot(self):
        m=self.manager();m.background_protected=True;m.background_scope_verified=False
        m.process=Mock();m.process.poll.return_value=None;m.run_id=RUN
        m.pending_snapshot=dict(events=[],server_name='another server');m.snapshot_version=2
        m._flush_snapshot()
        self.assertFalse(schedule_path(self.root,RUN).exists())
        m.process=None

    def test_debug_spawn_skips_injection_only_in_this_thread_and_breaks_launcher_job(self):
        states=[]
        @contextmanager
        def skip():
            states.append('disabled')
            try:yield
            finally:states.append('restored')
        monkey=NS(skip_subprocess_arg_patch=skip)
        with patch.dict(sys.modules,{'_pydev_bundle.pydev_monkey':monkey}), \
             patch('web_persistent_runtime.os.name','nt'):
            with independent_worker_spawn() as flags:
                self.assertEqual(states,['disabled'])
                self.assertTrue(flags & 0x01000000)
            self.assertEqual(states,['disabled','restored'])

    def test_debug_spawn_restores_patching_even_if_creation_fails(self):
        states=[]
        @contextmanager
        def skip():
            states.append(False)
            try:yield
            finally:states.append(True)
        with patch.dict(sys.modules,{'_pydev_bundle.pydev_monkey':NS(skip_subprocess_arg_patch=skip)}):
            with self.assertRaises(OSError):
                with independent_worker_spawn():raise OSError('creation rejected')
        self.assertEqual(states,[False,True])

    def test_normal_spawn_does_not_load_debugger_or_change_global_debug_configuration(self):
        with patch.dict(sys.modules,{'_pydev_bundle.pydev_monkey':None}):
            with independent_worker_spawn() as flags:
                self.assertFalse(flags & 0x01000000)

    def test_manager_applies_debugger_isolation_at_the_actual_spawn_boundary(self):
        m=self.manager();m.discovery_pending=False;m._update=Mock()
        state=dict(patching=True)
        @contextmanager
        def skip():
            state['patching']=False
            try:yield
            finally:state['patching']=True
        def spawn(*args,**kwargs):
            self.assertFalse(state['patching'])
            self.assertTrue(kwargs['creationflags'] & 0x01000000)
            return Mock(pid=23456)
        with patch.dict(sys.modules,{'_pydev_bundle.pydev_monkey':NS(skip_subprocess_arg_patch=skip)}), \
             patch('web_service_manager.subprocess.Popen',side_effect=spawn):
            m._start(8765,m.generation)
        self.assertTrue(state['patching'])
        self.assertTrue(m._log.call_args.kwargs['debugger_detached'])
        m.process=None


if __name__=='__main__':unittest.main()

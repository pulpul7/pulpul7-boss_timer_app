"""Offline command checks: no Discord login, GUI, server, or user data."""
import ast
import asyncio
from copy import deepcopy
from pathlib import Path
import threading
import time
from types import SimpleNamespace as NS, MethodType
import unittest
from unittest.mock import AsyncMock, Mock, patch

from discord_authority import AuthorityRecord
from discord_connection_policy import ConnectionPolicy, CONTROL_PROTOCOL
from test_web_delegated_hosting import MemoryRepository, initial_record, HOST, SOURCE, HOST_SESSION, SOURCE_SESSION, SCOPE
from web_delegated_hosting import build_delegation, MAX_ATTEMPTS
from web_discord_control import DiscordWebControl
import web_discord_control as control_module


class ImmediateThread:
    def __init__(self, target, args=(), **_kwargs): self.target, self.args = target, args
    def start(self): self.target(*self.args)


class WebCommandTests(unittest.TestCase):
    def node(self, *, role='', configured=True, result='prepared', authorized=True):
        data = initial_record()
        if role == 'host':
            info = build_delegation(data, SOURCE, SOURCE_SESSION, SCOPE, 2, 'e'*32)
            data.update(owner=SOURCE, owner_runtime=SOURCE_SESSION, generation=2, request='e'*32, web_delegate=info)
        elif not authorized:
            data.update(owner=SOURCE, owner_runtime=SOURCE_SESSION)
        repo = MemoryRepository(data)
        for member in repo.data['members'].values():
            member['web_standalone_protocol'] = 1
            member['web_update_protocol'] = 1
            member['program_update_protocol'] = 1
        public = dict(state='waiting', enabled=True, address='')
        policy = dict(ConnectionPolicy._empty(), protocol=CONTROL_PROTOCOL, runtime_id=HOST_SESSION,
                      standby=role == 'host' or not authorized, authority_active=authorized,
                      authority_needs_sync=False, authority_until=10**12, query_until=10**12)
        manager = NS(delegation=NS(status=lambda:dict(role=role)),
                     ddns_status=lambda:dict(configured=configured), _log=Mock(),
                     public=NS(status=lambda:deepcopy(public)), publication_allowed=lambda:True,
                     status=lambda:dict(state='running'))
        def retry(**_kwargs):
            public.update(state=result, address='https://example.invalid:8766' if result=='prepared' else '')
        manager.retry_public = Mock(side_effect=retry)
        manager.stop_web_verified = Mock()
        manager.force_start_web = Mock()
        coordinator = NS(client=HOST, alive=True, busy=False, releasing=False, lock=threading.RLock(),
            scope=SCOPE,
            app=NS(web_service_manager=manager, _append_debug_log=Mock()), _assert_profile=Mock(),
            record=AuthorityRecord(repo.get, repo.put, SCOPE), _live_data=lambda data:data,
            _status=lambda:dict(online=True, runtime_id=HOST_SESSION), _notify=Mock(),
            policy=NS(snapshot=lambda:deepcopy(policy)))
        control = DiscordWebControl(coordinator)
        return control, coordinator, manager, repo, public

    def request(self, control, **overrides):
        done=Mock()
        payload=dict(request_id='123456789', target_administrator='host', **overrides)
        with patch.object(control_module.threading, 'Thread', ImmediateThread):
            control.request(payload, done)
        return done

    def test_name_request_executes_once_and_preserves_voice_owner(self):
        control, _, manager, repo, _ = self.node()
        before=AuthorityRecord.owner_stamp(repo.data)
        done=self.request(control)
        manager.retry_public.assert_called_once_with(once=True)
        self.assertTrue(done.call_args.args[0])
        self.assertIn('https://example.invalid', done.call_args.args[1])
        self.assertEqual(AuthorityRecord.owner_stamp(repo.data), before)
        # Three CAS writes: requested, accepted, completed. No polling reads.
        self.assertEqual(repo.writes, 3)

    def test_internal_handover_stops_only_exact_session_and_preserves_settings(self):
        control, coordinator, manager, repo, _ = self.node()
        coordinator.runtime = HOST_SESSION
        coordinator._live_data = Mock(side_effect=AssertionError('unnecessary extra probe'))
        repo.data['web_operation'] = dict(id='e'*32, owner=HOST, runtime=HOST_SESSION, expires=time.time()+100)
        done = self.request(control, operation='administrator_web_stop', target_client_id=HOST,
            expected_target_runtime=HOST_SESSION, handover_operation='e'*32)
        self.assertTrue(done.call_args.args[0])
        manager.stop_web_verified.assert_called_once_with(for_handover=True, record_stop=False)
        self.assertEqual(repo.data['web_standalone']['state'], 'stopped')
        self.assertTrue(repo.data['members'][HOST]['web_call']['stopped'])
        self.assertEqual(repo.writes, 3)  # Request, acceptance, merged stopped/result.
        coordinator._live_data.assert_not_called()

    def test_internal_handover_rejects_replaced_target_session(self):
        control, coordinator, manager, repo, _ = self.node()
        coordinator.runtime = HOST_SESSION
        repo.data['web_operation'] = dict(id='e'*32, owner=HOST, runtime=HOST_SESSION, expires=time.time()+100)
        done = self.request(control, operation='administrator_web_stop', target_client_id=HOST,
            expected_target_runtime='9'*32, handover_operation='e'*32)
        self.assertFalse(done.call_args.args[0])
        manager.stop_web_verified.assert_not_called()
        self.assertEqual(repo.writes, 0)

    def test_superseded_handover_cannot_publish_a_stopped_acknowledgement(self):
        control, coordinator, _, repo, _ = self.node()
        coordinator.runtime = HOST_SESSION
        call = dict(id='123', runtime=HOST_SESSION, action='stop', state='accepted', handover_operation='e'*32)
        repo.data['members'][HOST]['web_call'] = deepcopy(call)
        repo.data['web_operation'] = dict(id='f'*32, expires=time.time()+100)
        before = deepcopy(repo.data)
        control._finish(call, True, 'stopped', stopped=True, for_handover=True)
        self.assertEqual(repo.data, before)
        self.assertEqual(repo.writes, 0)
        coordinator.app._append_debug_log.assert_called_once()

    def test_stop_acceptance_notice_is_queued_without_delaying_cleanup(self):
        control, coordinator, manager, repo, _ = self.node()
        queued = Mock()
        manager.standalone = NS(_notify=queued)
        order = []
        queued.side_effect = lambda *_: order.append('queued')
        manager.stop_web_verified.side_effect = lambda: order.append('stop')
        done = self.request(control, operation='administrator_web_stop')
        self.assertTrue(done.call_args.args[0])
        self.assertEqual(order, ['queued', 'stop'])

    def test_designated_host_without_voice_can_retry(self):
        control, _, manager, repo, _ = self.node(role='host')
        done=self.request(control)
        manager.retry_public.assert_called_once_with(once=True)
        self.assertTrue(done.call_args.args[0])
        self.assertEqual(repo.data['owner'], SOURCE)

    def test_unconfigured_target_reports_setup_without_enabling(self):
        control, _, manager, _, _ = self.node(configured=False)
        done=self.request(control)
        manager.retry_public.assert_not_called()
        self.assertFalse(done.call_args.args[0])
        self.assertIn('DuckDNS', done.call_args.args[1])

    def test_unassigned_standby_cannot_publish(self):
        control, _, manager, _, _ = self.node(authorized=False)
        done=self.request(control)
        manager.retry_public.assert_not_called()
        self.assertFalse(done.call_args.args[0])

    def test_failed_attempt_is_not_repeated(self):
        control, _, manager, _, _ = self.node(result='error')
        done=self.request(control)
        manager.retry_public.assert_called_once_with(once=True)
        self.assertFalse(done.call_args.args[0])
        self.assertIn('1회 시도 실패', done.call_args.args[1])

    def test_duplicate_completed_events_do_not_start_again(self):
        control, coordinator, manager, repo, _ = self.node()
        self.request(control)
        for _ in range(5): control.observe(repo.data, coordinator._status())
        manager.retry_public.assert_called_once()

    def test_pending_duplicate_request_is_rejected(self):
        control, _, manager, repo, _ = self.node()
        repo.data['members'][HOST]['web_call']=dict(id='111',runtime=HOST_SESSION,state='pending',expires=time.time()+10)
        done=self.request(control)
        manager.retry_public.assert_not_called()
        self.assertIn('처리 중',done.call_args.args[1])

    def test_already_published_returns_address_without_restarting(self):
        control, _, manager, _, public = self.node()
        public.update(state='prepared',address='https://example.invalid:8766')
        done=self.request(control)
        manager.retry_public.assert_not_called()
        self.assertIn('이미 웹 공개 중',done.call_args.args[1])

    def test_stop_command_works_without_voice_or_ddns_and_records_stop_proof(self):
        control, _, manager, repo, _ = self.node(authorized=False, configured=False)
        before = AuthorityRecord.owner_stamp(repo.data)
        done = self.request(control, operation='administrator_web_stop')
        self.assertTrue(done.call_args.args[0])
        manager.stop_web_verified.assert_called_once()
        manager.retry_public.assert_not_called()
        self.assertTrue(repo.data['members'][HOST]['web_call']['stopped'])
        self.assertEqual(AuthorityRecord.owner_stamp(repo.data), before)

    def test_stop_failure_never_claims_peer_has_stopped(self):
        control, _, manager, repo, _ = self.node()
        manager.stop_web_verified.side_effect = ValueError('public deny failed')
        done = self.request(control, operation='administrator_web_stop')
        self.assertFalse(done.call_args.args[0])
        self.assertFalse(repo.data['members'][HOST]['web_call']['stopped'])

    def test_old_target_cannot_mistake_logout_for_web_retry(self):
        control, _, manager, repo, _ = self.node()
        repo.data['members'][HOST]['web_standalone_protocol'] = 0
        done = self.request(control, operation='administrator_web_stop')
        self.assertFalse(done.call_args.args[0])
        manager.retry_public.assert_not_called()
        manager.stop_web_verified.assert_not_called()

    def test_success_without_stop_proof_is_rejected(self):
        control, _, _, _, _ = self.node()
        control.latest = dict(members={HOST: dict(runtime=HOST_SESSION, web_call=dict(
            id='123', runtime=HOST_SESSION, state='done', action='stop', ok=True))})
        with self.assertRaisesRegex(ValueError, '종료 확인'):
            control._wait(HOST, '123', Mock(), time.monotonic()+10)

    def test_force_command_is_web_only_and_can_target_standby_pc(self):
        control, _, manager, repo, _ = self.node(authorized=False)
        before = AuthorityRecord.owner_stamp(repo.data)
        done = self.request(control, operation='administrator_web_force')
        self.assertTrue(done.call_args.args[0])
        manager.force_start_web.assert_called_once()
        manager.retry_public.assert_not_called()
        self.assertEqual(AuthorityRecord.owner_stamp(repo.data), before)

    def test_standalone_owner_without_voice_can_use_existing_retry_command(self):
        control, _, manager, repo, _ = self.node(authorized=False)
        repo.data['web_standalone'] = dict(protocol=1, id='e'*32, state='active',
            owner=HOST, runtime=HOST_SESSION, **SCOPE)
        done = self.request(control)
        self.assertTrue(done.call_args.args[0])
        manager.retry_public.assert_called_once_with(once=True)

    def test_host_once_disables_background_public_and_worker_retries(self):
        from web_delegated_hosting import DelegatedHosting
        relay=object.__new__(DelegatedHosting)
        relay.lock=threading.RLock();relay.wake=threading.Event()
        relay.retry(once=True)
        self.assertEqual(relay.worker_retries, MAX_ATTEMPTS)
        self.assertEqual(relay.public_retries, MAX_ATTEMPTS)
        relay.retry()
        self.assertEqual(relay.public_retries, 0)

    def test_update_waits_for_apply_and_preserves_voice(self):
        control, _, manager, repo, _ = self.node()
        manager.page_updater = NS(request_and_wait=Mock(return_value=dict(state='applied', message='웹 업데이트 적용 완료')))
        before = AuthorityRecord.owner_stamp(repo.data)
        done = self.request(control, operation='administrator_web_update')
        self.assertTrue(done.call_args.args[0])
        self.assertIn('적용 완료', done.call_args.args[1])
        manager.page_updater.request_and_wait.assert_called_once_with(timeout=control_module.UPDATE_SECONDS)
        manager.retry_public.assert_not_called()
        manager.force_start_web.assert_not_called()
        self.assertEqual(AuthorityRecord.owner_stamp(repo.data), before)
        for _ in range(3): control.observe(repo.data, control.coordinator._status())
        manager.page_updater.request_and_wait.assert_called_once()

    def test_update_targets_remote_web_host_instead_of_voice_owner(self):
        host_control, host, manager, repo, _ = self.node(role='host')
        manager.page_updater = NS(request_and_wait=Mock(return_value=dict(state='current', message='최신 웹 파일입니다.')))
        caller = NS(**vars(host))
        caller.client = SOURCE
        caller._status = lambda: dict(online=True, runtime_id=SOURCE_SESSION)
        caller.app = NS(web_service_manager=NS(), _append_debug_log=Mock())
        caller_control = DiscordWebControl(caller)
        caller._notify = lambda data: host_control.observe(data, host._status())
        host._notify = lambda data: caller_control.observe(data, caller._status())
        done = self.request(caller_control, operation='administrator_web_update')
        self.assertTrue(done.call_args.args[0])
        self.assertIn('최신', done.call_args.args[1])
        self.assertEqual(repo.data['owner'], SOURCE)
        self.assertEqual(repo.data['members'][HOST]['web_call']['action'], 'update')
        self.assertNotIn('web_call', repo.data['members'][SOURCE])
        manager.page_updater.request_and_wait.assert_called_once()

    def test_update_rejects_old_receiver_before_sending(self):
        control, _, manager, repo, _ = self.node()
        repo.data['members'][HOST].pop('web_update_protocol')
        manager.page_updater = NS(request_and_wait=Mock())
        done = self.request(control, operation='administrator_web_update')
        self.assertFalse(done.call_args.args[0])
        self.assertIn('새 버전', done.call_args.args[1])
        manager.page_updater.request_and_wait.assert_not_called()
        self.assertEqual(repo.writes, 0)

    def test_update_reports_apply_failure(self):
        control, _, manager, _, _ = self.node()
        manager.page_updater = NS(request_and_wait=Mock(side_effect=ValueError('파일 검증 실패')))
        done = self.request(control, operation='administrator_web_update')
        self.assertFalse(done.call_args.args[0])
        self.assertIn('파일 검증 실패', done.call_args.args[1])
        manager.retry_public.assert_not_called()

    def test_update_does_not_fall_back_from_disconnected_host(self):
        control, _, manager, repo, _ = self.node(role='host')
        repo.data['members'][HOST]['online'] = False
        manager.page_updater = NS(request_and_wait=Mock())
        done = self.request(control, operation='administrator_web_update')
        self.assertFalse(done.call_args.args[0])
        manager.page_updater.request_and_wait.assert_not_called()
        self.assertEqual(repo.writes, 0)

    def test_update_host_change_before_claim_reports_failure_without_running(self):
        control, _, manager, repo, _ = self.node()
        manager.page_updater = NS(request_and_wait=Mock())
        call = dict(id='1234', runtime=HOST_SESSION, action='update', state='pending', expires=time.time()+30,
                    update_host=[SOURCE, SOURCE_SESSION, 'old'])
        repo.data['members'][HOST]['web_call'] = deepcopy(call)
        control._perform(call)
        manager.page_updater.request_and_wait.assert_not_called()
        self.assertEqual(repo.data['members'][HOST]['web_call']['state'], 'done')
        self.assertFalse(repo.data['members'][HOST]['web_call']['ok'])

    def test_actual_bot_command_checks_permissions_and_duplicate_ack(self):
        tree=ast.parse(Path('boss_timer_discord_bot.py').read_text(encoding='utf-8-sig'))
        method=next(n for cls in tree.body if isinstance(cls,ast.ClassDef) for n in cls.body
                    if isinstance(n,ast.AsyncFunctionDef) and n.name=='_administrator_command')
        scope=dict(time=time,STATUS=NS(runtime_id=HOST_SESSION),log=lambda _:None)
        exec(compile(ast.Module(body=[method],type_ignores=[]),'administrator_command','exec'),scope)
        bot=NS(_defer_interaction_once=AsyncMock(return_value=True),
               _get_configured_server_id=lambda:'100',
               _queue_local_schedule_request=AsyncMock(side_effect=OSError('offline fixture')))
        callback=MethodType(scope['_administrator_command'],bot)
        interaction=NS(id=123456789,user=NS(guild_permissions=NS(administrator=False,manage_guild=False)),
                       followup=NS(send=AsyncMock()))
        asyncio.run(callback(interaction,'administrator_web_retry',target='host'))
        bot._queue_local_schedule_request.assert_not_called()
        asyncio.run(callback(interaction,'administrator_web_update'))
        bot._queue_local_schedule_request.assert_not_called()
        asyncio.run(callback(interaction,'administrator_program_update'))
        bot._queue_local_schedule_request.assert_not_called()
        self.assertIn('권한', interaction.followup.send.call_args.args[0])
        interaction.user.guild_permissions.manage_guild=True
        asyncio.run(callback(interaction,'administrator_program_update'))
        self.assertEqual(bot._queue_local_schedule_request.call_args.args[0]['operation'], 'administrator_program_update')
        self.assertNotIn('0714', str(bot._queue_local_schedule_request.call_args.args[0]))
        bot._queue_local_schedule_request.reset_mock()
        asyncio.run(callback(interaction,'administrator_web_retry',target='host'))
        self.assertEqual(bot._queue_local_schedule_request.call_count,1)
        self.assertEqual(bot._queue_local_schedule_request.call_args.args[0]['operation'],'administrator_web_retry')
        asyncio.run(callback(interaction,'administrator_web_update'))
        update_payload = bot._queue_local_schedule_request.call_args.args[0]
        self.assertEqual(update_payload['operation'], 'administrator_web_update')
        self.assertEqual(update_payload['target_administrator'], '')
        self.assertNotIn('0714', str(update_payload))
        bot._defer_interaction_once.return_value=False
        asyncio.run(callback(interaction,'administrator_web_retry',target='host'))
        previous_replies = interaction.followup.send.await_count
        asyncio.run(callback(interaction,'administrator_program_update'))
        self.assertEqual(interaction.followup.send.await_count, previous_replies)
        self.assertEqual(bot._queue_local_schedule_request.call_count,2)

    def test_full_update_restarts_only_after_reply_confirmation_and_never_calls_web_start(self):
        control, coordinator, manager, repo, _ = self.node()
        service = NS(prepare=Mock(return_value=dict(state='prepared', path='unit-job', message='검증 완료')),
                     schedule=Mock())
        coordinator._ui = lambda callback, **_kw: callback()
        coordinator.app._get_program_update_service = lambda: service
        call = dict(id='1234', runtime=HOST_SESSION, action='program_update', state='pending', expires=time.time()+30)
        repo.data['members'][HOST]['web_call'] = deepcopy(call)
        with patch.object(control, '_wait_program_reply', return_value=True) as acknowledged:
            control._perform(call)
        acknowledged.assert_called_once_with(call)
        service.prepare.assert_called_once_with()
        service.schedule.assert_called_once_with(service.prepare.return_value)
        manager.retry_public.assert_not_called()
        manager.stop_web_verified.assert_not_called()
        self.assertTrue(repo.data['members'][HOST]['web_call']['ok'])

    def test_full_update_does_not_restart_when_reply_cannot_be_delivered(self):
        control, coordinator, _, repo, _ = self.node()
        service = NS(prepare=Mock(return_value=dict(state='prepared', path='unit-job', message='검증 완료')), schedule=Mock())
        coordinator._ui = lambda callback, **_kw: callback()
        coordinator.app._get_program_update_service = lambda: service
        call = dict(id='1234', runtime=HOST_SESSION, action='program_update', state='pending', expires=time.time()+30)
        repo.data['members'][HOST]['web_call'] = deepcopy(call)
        with patch.object(control, '_wait_program_reply', return_value=False):
            control._perform(call)
        service.schedule.assert_not_called()

    def test_full_update_targets_voice_owner_before_separate_web_host(self):
        control, coordinator, _, repo, _ = self.node(role='host')
        control._wait = Mock()
        # Do not execute the simulated remote PC's worker.
        control.observe = Mock()
        done = self.request(control, operation='administrator_program_update')
        done.assert_not_called()
        self.assertIn('web_call', repo.data['members'][SOURCE])
        self.assertNotIn('web_call', repo.data['members'][HOST])
        self.assertEqual(repo.data['members'][SOURCE]['web_call']['action'], 'program_update')

    def test_full_update_rejects_old_receiver_without_writing_a_request(self):
        control, _, _, repo, _ = self.node()
        repo.data['members'][HOST].pop('program_update_protocol')
        done = self.request(control, operation='administrator_program_update')
        self.assertFalse(done.call_args.args[0])
        self.assertEqual(repo.writes, 0)

    def test_result_delivery_marker_is_fenced_to_requester_and_target_sessions(self):
        import json
        import tempfile
        control, coordinator, _, repo, _ = self.node()
        call = dict(id='1234', runtime=HOST_SESSION, requester_runtime=HOST_SESSION,
                    action='program_update', state='done', ok=True, expires=time.time()+30)
        repo.data['members'][HOST]['web_call'] = deepcopy(call)
        with tempfile.TemporaryDirectory(prefix='update-delivery-unit-') as directory:
            coordinator.app._get_discord_schedule_request_dir = lambda: directory
            folder = Path(directory) / 'results'
            folder.mkdir()
            marker = folder / '1234.delivered.json'
            marker.write_text(json.dumps(dict(request_id='1234', runtime=SOURCE_SESSION)), encoding='utf-8')
            control._ack_program_reply(HOST, call)
            self.assertEqual(repo.writes, 0)
            marker.write_text(json.dumps(dict(request_id='1234', runtime=HOST_SESSION)), encoding='utf-8')
            control._ack_program_reply(HOST, call)
            self.assertTrue(repo.data['members'][HOST]['web_call']['notified'])
            self.assertFalse(marker.exists())
            self.assertTrue(control._wait_program_reply(call))
            repo.data['members'][HOST]['runtime'] = SOURCE_SESSION
            control.observe(repo.data, coordinator._status())
            self.assertFalse(control._wait_program_reply(call))

    def test_bot_writes_delivery_marker_only_after_successful_discord_reply(self):
        import json
        import tempfile
        tree = ast.parse(Path('boss_timer_discord_bot.py').read_text(encoding='utf-8-sig'))
        method = next(n for cls in tree.body if isinstance(cls, ast.ClassDef) for n in cls.body
                      if isinstance(n, ast.AsyncFunctionDef) and n.name == '_administrator_command')
        with tempfile.TemporaryDirectory(prefix='update-bot-reply-unit-') as directory:
            folder = Path(directory)
            scope = dict(time=time, json=json, STATUS=NS(runtime_id=HOST_SESSION),
                         DISCORD_SCHEDULE_REQUEST_DIR=folder, log=lambda _: None)
            exec(compile(ast.Module(body=[method], type_ignores=[]), 'update_bot_reply', 'exec'), scope)
            bot = NS(_defer_interaction_once=AsyncMock(return_value=True),
                     _get_configured_server_id=lambda: '100', _queue_local_schedule_request=AsyncMock(),
                     discord=NS(AllowedMentions=NS(none=lambda: None)))
            callback = MethodType(scope['_administrator_command'], bot)
            interaction = NS(id=1234, user=NS(guild_permissions=NS(administrator=True)),
                             followup=NS(send=AsyncMock()))
            result = folder / 'results/1234.json'
            result.parent.mkdir()
            marker = result.with_suffix('.delivered.json')
            async def send(*_a, **_kw):
                self.assertFalse(marker.exists())
            interaction.followup.send.side_effect = send
            result.write_text(json.dumps(dict(ok=True, message='verified')), encoding='utf-8')
            asyncio.run(callback(interaction, 'administrator_program_update'))
            self.assertEqual(json.loads(marker.read_text(encoding='utf-8'))['runtime'], HOST_SESSION)
            marker.unlink()
            result.write_text(json.dumps(dict(ok=True, message='verified')), encoding='utf-8')
            interaction.followup.send.side_effect = OSError('fake Discord delivery error')
            with self.assertRaises(OSError):
                asyncio.run(callback(interaction, 'administrator_program_update'))
            self.assertFalse(marker.exists())

    def test_root_update_aliases_register_and_validate_without_issuing_updates(self):
        import discord
        from typing import Any
        tree = ast.parse(Path('boss_timer_discord_bot.py').read_text(encoding='utf-8-sig'))
        function = deepcopy(next(n for n in ast.walk(tree)
                                 if isinstance(n, ast.AsyncFunctionDef) and n.name == 'bosstimer_ansi'))
        function.decorator_list = function.decorator_list[1:]
        wrapper = ast.parse('def make(self):\n pass').body[0]
        wrapper.body = [function, ast.Return(value=ast.Name(id='bosstimer_ansi', ctx=ast.Load()))]
        module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
        scope = {'Any': Any}
        exec(compile(module, 'root_update_alias', 'exec'), scope)
        bot = NS(discord=discord, _administrator_command=AsyncMock(),
                 _defer_interaction_once=AsyncMock(return_value=True))
        callback = scope['make'](bot)
        command = discord.app_commands.Command(name='보탐', description='보탐', callback=callback)
        self.assertEqual(command.parameters[0].name, '동작')
        interaction = NS(followup=NS(send=AsyncMock()))
        for text in ('업데이트', '업데이트 전체 wrong', '업데이트 웹서버 0714 extra', '업데이트 기타 0714'):
            asyncio.run(callback(interaction, text))
        bot._administrator_command.assert_not_awaited()
        self.assertEqual(interaction.followup.send.await_count, 4)
        asyncio.run(callback(interaction, ' 업데이트   웹서버   0714 '))
        bot._administrator_command.assert_awaited_once_with(interaction, 'administrator_web_update')
        bot._administrator_command.reset_mock()
        asyncio.run(callback(interaction, '업데이트 전체 0714'))
        bot._administrator_command.assert_awaited_once_with(interaction, 'administrator_program_update')

    def test_new_slash_command_registers_with_actual_discord_sdk(self):
        import discord
        from typing import Any
        tree=ast.parse(Path('boss_timer_discord_bot.py').read_text(encoding='utf-8-sig'))
        function=deepcopy(next(n for n in ast.walk(tree)
                              if isinstance(n,ast.AsyncFunctionDef) and n.name=='webserver_once'))
        function.decorator_list=function.decorator_list[1:]
        wrapper=ast.parse('def make(self):\n pass').body[0]
        wrapper.body=[function,ast.Return(value=ast.Name(id='webserver_once',ctx=ast.Load()))]
        module=ast.fix_missing_locations(ast.Module(body=[wrapper],type_ignores=[]))
        scope={'Any':Any}
        exec(compile(module,'webserver_slash_command','exec'),scope)
        bot=NS(discord=discord,_administrator_command=AsyncMock(),_defer_interaction_once=AsyncMock(return_value=True))
        callback=scope['make'](bot)
        command=discord.app_commands.Command(name='웹서버',description='웹 공개 1회 시도',callback=callback)
        self.assertEqual(command.parameters[0].name,'관리자이름')
        self.assertTrue(command.parameters[0].required)
        interaction=NS(followup=NS(send=AsyncMock()))
        asyncio.run(callback(interaction,' 나츠 '))
        bot._administrator_command.assert_awaited_once_with(interaction,'administrator_web_retry',target='나츠')
        bot._administrator_command.reset_mock()
        asyncio.run(callback(interaction,'로그아웃 나츠'))
        bot._administrator_command.assert_awaited_once_with(interaction,'administrator_web_stop',target='나츠')
        bot._administrator_command.reset_mock()
        asyncio.run(callback(interaction,'단독 나츠'))
        bot._administrator_command.assert_awaited_once_with(interaction,'administrator_web_force',target='나츠')
        bot._administrator_command.reset_mock()
        for text in ('업데이트', '업데이트 wrong', '업데이트 0714 extra'):
            asyncio.run(callback(interaction,text))
        bot._administrator_command.assert_not_awaited()
        self.assertEqual(interaction.followup.send.await_count, 3)
        asyncio.run(callback(interaction,' 업데이트   0714 '))
        bot._administrator_command.assert_awaited_once_with(interaction,'administrator_web_update')


if __name__ == '__main__': unittest.main()

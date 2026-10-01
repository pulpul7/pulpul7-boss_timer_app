import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from notice_output_bridge import handle_notice_command
from notice_module.payload.notice_discord_transport import DiscordNoticeTransport
from notice_module.payload.notice_host_opportunities import completed_opportunities
from notice_module.payload.notice_management import KST
from boss_timer_discord_bot import BotStatus, VoiceBridgeJob
import boss_timer_discord_bot as bot_module
from notice_module.payload.main import NoticePlugin
from notice_module.payload.notice_management import NoticeStore


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.status = NS(pid=12, guild_id='guild', online=True, voice_connected=True,
                         standby=False, handover_hold=False, shutdown_requested=threading.Event())
        self.output = NS(scope_id='notice:req', released=False, state='ready',
                         play=AsyncMock(return_value=True), stop=AsyncMock(return_value=True))
        self.bot = NS(notice_output=self.output)

    def command(self, action, **extra):
        return dict(id='req', action=action, pid=12, sent_at=time.time(), guild_id='guild', **extra)

    async def test_handover_and_wrong_guild_never_play(self):
        for field, value in [('handover_hold', True), ('standby', True), ('voice_connected', False)]:
            original = getattr(self.status, field)
            setattr(self.status, field, value)
            result = await handle_notice_command(self.bot, self.command('play'), self.status)
            self.assertFalse(result['accepted'])
            setattr(self.status, field, original)
        command = self.command('play')
        command['guild_id'] = 'other'
        self.assertFalse((await handle_notice_command(self.bot, command, self.status))['accepted'])
        self.output.play.assert_not_awaited()

    async def test_stale_process_and_command_rejected(self):
        for field, value in [('pid', 13), ('sent_at', time.time()-20)]:
            command = self.command('play')
            command[field] = value
            with self.assertRaises(ValueError):
                await handle_notice_command(self.bot, command, self.status)
        self.output.play.assert_not_awaited()

    async def test_stop_targets_only_owned_notice(self):
        command = self.command('stop')
        command['id'] = 'another'
        self.assertTrue((await handle_notice_command(self.bot, command, self.status))['accepted'])
        self.output.stop.assert_not_awaited()
        await handle_notice_command(self.bot, self.command('stop'), self.status)
        self.output.stop.assert_awaited_once()

    async def test_heartbeat_renews_short_deadline_and_busy_play_stays_ready(self):
        self.output.play.return_value = False
        result = await handle_notice_command(self.bot, self.command('play'), self.status, clock=lambda: 10)
        self.assertFalse(result['accepted'])
        self.assertEqual(self.output.deadline, 13)
        self.assertEqual(result['state'], 'ready')


class TransportTests(unittest.TestCase):
    def test_prepare_is_silent_and_release_requires_confirmed_stop(self):
        request = Mock(return_value=dict(accepted=True, state='ready'))
        lease = NS(path='fake.mp3', stop=Mock())
        transport = DiscordNoticeTransport(request)
        handle = transport.prepare_prepared(lease)
        self.assertEqual(request.call_args.args[0]['action'], 'prepare')
        request.return_value = dict(accepted=False, state='failed')
        self.assertFalse(transport.stop(handle))
        lease.stop.assert_not_called()
        request.return_value = dict(accepted=True, state='stopped')
        self.assertTrue(transport.stop(handle))
        lease.stop.assert_called_once()

    def test_timeout_retains_handle_for_later_stop(self):
        transport = DiscordNoticeTransport(Mock(side_effect=TimeoutError))
        lease = NS(path='fake.mp3', stop=Mock())
        handle = transport.prepare_prepared(lease)
        self.assertEqual(transport.status(handle), 'failed')
        lease.stop.assert_not_called()


class PluginOutputTests(unittest.TestCase):
    def test_same_bot_recovers_after_uncertain_stop_without_replaying(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = NoticeStore(root, '9')
            now = datetime.now(KST)
            store.register(dict(id='event', title='안내', tts_text='테스트', valid_from=now,
                                valid_until=now + timedelta(hours=1)))
            settings = store.snapshot()['settings']
            settings['output_enabled'] = True
            store.configure(settings)
            state, stop_fails, elapsed = ['ready'], [True], [0.0]
            calls = []
            def request(command):
                action = command['action']
                calls.append(action)
                if action == 'stop' and stop_fails[0]:
                    raise TimeoutError()
                if action == 'play':
                    state[0] = 'playing'
                return dict(accepted=True, state=state[0])
            context = dict(identity=('9', '19', 12, 'secret'), request=request)
            host = NS(data_root=root, get_output_context=lambda: context,
                get_schedule_snapshot=lambda: dict(server_id='9', season='19', events=[]),
                call_later=Mock(return_value='timer'), cancel_later=Mock(), log=Mock())
            plugin = NoticePlugin(host)
            self.addCleanup(plugin.stop)
            lease = NS(path='prepared.mp3', stop=Mock())
            plugin.prepared_audio = Mock(current=Mock(return_value=True), acquire=Mock(return_value=lease))
            plugin.stopped = False
            def tick():
                plugin._audio_tick()
                plugin.audio_worker.join(timeout=3)
                self.assertFalse(plugin.audio_worker.is_alive())
            tick()
            controller = plugin.audio_controller
            controller.clock = lambda: elapsed[0]
            tick()
            state[0] = 'completed'
            tick()
            self.assertTrue(controller.fault)
            lease.stop.assert_not_called()
            self.assertIsNone(store.snapshot()['events']['event']['last_delivery'])
            stop_fails[0] = False
            elapsed[0] = 3
            tick()
            self.assertIs(plugin.audio_controller, controller)
            self.assertFalse(controller.fault)
            lease.stop.assert_called_once()
            tick()
            self.assertIsNotNone(store.snapshot()['events']['event']['last_delivery'])
            self.assertEqual(calls.count('prepare'), 1)
            self.assertEqual(calls.count('play'), 1)

    def test_prepared_audio_to_confirmed_delivery_without_real_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = NoticeStore(root, '9')
            now = datetime.now(KST)
            store.register(dict(id='event', title='안내', tts_text='테스트', valid_from=now,
                                valid_until=now+timedelta(hours=1)))
            settings = store.snapshot()['settings']
            settings['output_enabled'] = True
            store.configure(settings)
            state = ['ready']
            calls = []
            def request(command):
                calls.append(command['action'])
                if command['action'] == 'play':
                    state[0] = 'playing'
                elif command['action'] == 'stop':
                    state[0] = 'stopped'
                return dict(accepted=True, state=state[0])
            context = dict(identity=('9', '19', 12, 'secret'), request=request)
            host = NS(data_root=root, get_output_context=lambda: context,
                get_schedule_snapshot=lambda: dict(server_id='9', season='19', events=[]),
                call_later=Mock(return_value='timer'), cancel_later=Mock(), log=Mock())
            plugin = NoticePlugin(host)
            lease = NS(path='prepared.mp3', stop=Mock())
            plugin.prepared_audio = Mock(current=Mock(return_value=True), acquire=Mock(return_value=lease))
            plugin.stopped = False
            def tick():
                plugin._audio_tick()
                plugin.audio_worker.join(timeout=3)
                self.assertFalse(plugin.audio_worker.is_alive())
            tick()
            self.assertEqual(calls, ['prepare'])
            self.assertIsNone(store.snapshot()['events']['event']['last_delivery'])
            tick()
            self.assertIn('play', calls)
            self.assertIsNone(store.snapshot()['events']['event']['last_delivery'])
            state[0] = 'completed'
            tick()
            self.assertIsNotNone(store.snapshot()['events']['event']['last_delivery'])
            self.assertIn('stop', calls)
            lease.stop.assert_called_once()
            plugin.stop()


class TimedReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_timed_one_minute_all_clips_must_reach_eof(self):
        for outcomes, expected in [([True, True], 1), ([True, False], 0)]:
            status = BotStatus()
            bot = bot_module.DiscordScheduleBot.__new__(bot_module.DiscordScheduleBot)
            bot._is_voice_bridge_scope_cancelled = Mock(return_value=False)
            bot._play_clip = AsyncMock(side_effect=outcomes)
            bot._cleanup_audio_source = Mock()
            at = datetime.now()-timedelta(seconds=1)
            job = VoiceBridgeJob(id='timed', created_at=at, phase='PRE_ALERT_SEQUENCE',
                category='general', lane='center', volume=1, clip_paths=(),
                timed_clips=((at, 'a.mp3'), (at, 'b.mp3')), target_time=at.isoformat(), offset_sec=60)
            with patch.object(bot_module, 'STATUS', status), patch.object(bot_module, 'log'):
                await bot._play_timed_bridge_clips(job)
            self.assertEqual(len(status.notice_opportunities), expected)
            self.assertTrue(all(call.kwargs['confirm_eof'] for call in bot._play_clip.call_args_list))


class OpportunityTests(unittest.TestCase):
    def test_matches_actual_completion_not_just_schedule_time(self):
        now = datetime(2026, 9, 29, 19, 59, 10, tzinfo=KST)
        target = now.replace(hour=20, minute=0, second=0)
        snapshot = dict(server_id='9', events=[dict(boss_name='월드보스', fixed=True,
                                                   scheduled_at=target.isoformat())])
        receipt = dict(id='one', phase='FIXED_PRE_ALERT_SEQUENCE', scheduled_at=target.isoformat(),
                       created_at=(now-timedelta(seconds=10)).isoformat(), at=now.isoformat(), text='월드보스 1분 전')
        result = completed_opportunities([receipt], snapshot, now)
        self.assertEqual(result[0]['fixed_kind'], 'world_boss')
        self.assertEqual(result[0]['phase'], 'one_minute_complete')
        self.assertFalse(completed_opportunities([], snapshot, now))
        self.assertFalse(completed_opportunities([receipt], snapshot, now+timedelta(minutes=3)))
        snapshot['events'][0]['scheduled_at'] = (target+timedelta(minutes=1)).isoformat()
        self.assertFalse(completed_opportunities([receipt], snapshot, now))

    def test_only_one_minute_non_test_jobs_create_opportunity(self):
        status = BotStatus()
        base = dict(id='test', created_at=datetime.now(), phase='PRE_ALERT', category='general',
                    lane='center', volume=1, clip_paths=(), target_time=datetime.now().isoformat())
        for offset, scope in [(0, ''), (60, 'voice-test')]:
            status.record_notice_opportunity(VoiceBridgeJob(**base, offset_sec=offset, scope_id=scope))
        self.assertFalse(status.notice_opportunities)
        status.record_notice_opportunity(VoiceBridgeJob(**base, offset_sec=60))
        self.assertEqual(len(status.notice_opportunities), 1)


if __name__ == '__main__':
    unittest.main()

"""Notice chat checks using fake output and memory only; no GUI/audio/network."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import discord
import boss_timer_discord_bot as module
from notice_output_bridge import handle_notice_command, request_notice
from notice_module.payload.notice_discord_transport import DiscordNoticeTransport
from notice_module.payload.notice_preparation import PreparedSpeechPool


class NoticeChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = bot = object.__new__(module.DiscordScheduleBot)
        self.generation = 7
        self.tasks = []
        def schedule(coroutine):
            task = asyncio.create_task(coroutine)
            self.tasks.append(task)
            return task
        bot.client = NS(loop=NS(create_task=schedule))
        bot.discord = discord
        bot.config = {}
        bot._send_allowed = Mock(return_value=True)
        bot._connection_state = lambda: dict(authority_generation=self.generation)
        bot._is_voice_bridge_scope_cancelled = Mock(return_value=False)
        self.channel = NS(id=101, send=AsyncMock())
        bot._resolve_text_channel = AsyncMock(return_value=self.channel)
        bot._cleanup_bot_text_channel_messages = AsyncMock()
        self.output = NS(scope_id='notice:req', released=False, state='ready',
            notice_text='서버가 오픈되었습니다.', notice_text_generation=7, notice_text_announced=False,
            stop=AsyncMock(return_value=True))
        async def play():
            self.output.state = 'playing'
            return True
        self.output.play = AsyncMock(side_effect=play)
        bot.notice_output = self.output
        self.status = NS(pid=12, guild_id='guild', online=True, voice_connected=True,
            standby=False, handover_hold=False, shutdown_requested=threading.Event())
        logger = patch.object(module, 'log', Mock())
        logger.start()
        self.addCleanup(logger.stop)

    async def asyncTearDown(self):
        await self.drain()

    async def drain(self):
        if self.tasks:
            await asyncio.gather(*self.tasks)

    def command(self, action, **values):
        return dict(id='req', action=action, pid=12, sent_at=time.time(), guild_id='guild', **values)

    async def test_accepted_play_posts_same_text_in_configured_channel(self):
        result = await handle_notice_command(self.bot, self.command('play'), self.status)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['state'], 'playing')
        await self.drain()
        self.channel.send.assert_awaited_once()
        embed = self.channel.send.call_args.kwargs['embed']
        self.assertIn('알리미', embed.title)
        self.assertEqual(embed.description, self.output.notice_text)

    async def test_boss_busy_or_preparing_never_posts_notice(self):
        self.output.play.side_effect = None
        self.output.play.return_value = False
        result = await handle_notice_command(self.bot, self.command('play'), self.status)
        self.assertFalse(result['accepted'])
        self.assertEqual(self.tasks, [])
        self.channel.send.assert_not_awaited()

    async def test_resume_and_repeated_requests_do_not_duplicate_chat(self):
        await handle_notice_command(self.bot, self.command('play'), self.status)
        await self.drain()
        self.output.state = 'paused'
        await handle_notice_command(self.bot, self.command('resume'), self.status)
        self.bot._queue_notice_text(self.output)
        await self.drain()
        self.channel.send.assert_awaited_once()
        self.assertEqual(len(self.tasks), 1)

    async def test_all_notice_kinds_use_same_publication_path(self):
        texts = ['서버 오픈 안내', '이벤트 종료 안내', '서버 이전 안내', '직업 추가 안내', '보스 참여 안내',
                 '정기점검 연장 공지가 올라왔습니다. 점검은 8시부터 12시까지입니다.',
                 '정기점검 연장 공지가 올라왔습니다. 점검은 8시부터 13시까지입니다.',
                 '정기점검 연장 공지가 올라왔습니다. 점검은 8시부터 14시 30분까지입니다.']
        for index, text in enumerate(texts):
            output = NS(scope_id=f'notice:{index}', state='playing', notice_text=text,
                notice_text_generation=7, notice_text_announced=False)
            self.bot._queue_notice_text(output)
        await self.drain()
        self.assertEqual([call.kwargs['embed'].description for call in self.channel.send.call_args_list], texts)

    async def test_long_notice_preserves_text_across_valid_sized_messages(self):
        self.output.notice_text = '길드 공지입니다. '*1000
        await handle_notice_command(self.bot, self.command('play'), self.status)
        await self.drain()
        parts = [call.kwargs['embed'].description for call in self.channel.send.call_args_list]
        self.assertEqual(''.join(parts), self.output.notice_text.strip())
        self.assertTrue(all(len(part) <= 3500 for part in parts))

    async def test_handover_while_resolving_channel_cancels_previous_generation(self):
        async def resolve():
            self.generation += 1
            return self.channel
        self.bot._resolve_text_channel.side_effect = resolve
        await handle_notice_command(self.bot, self.command('play'), self.status)
        await self.drain()
        self.channel.send.assert_not_awaited()

    async def test_chat_failure_does_not_change_audio_result(self):
        self.channel.send.side_effect = RuntimeError('fake permission failure')
        result = await handle_notice_command(self.bot, self.command('play'), self.status)
        await self.drain()
        self.assertTrue(result['accepted'])
        self.assertEqual(self.output.state, 'playing')
        self.output.stop.assert_not_awaited()

    async def test_completed_notice_can_still_publish_its_started_message(self):
        self.output.state = 'completed'
        self.output.released = True
        self.bot._queue_notice_text(self.output)
        await self.drain()
        self.channel.send.assert_awaited_once()

    async def test_cancelled_or_standby_notice_never_posts(self):
        self.bot._send_allowed.return_value = False
        self.output.state = 'playing'
        self.bot._queue_notice_text(self.output)
        await self.drain()
        self.channel.send.assert_not_awaited()

    async def test_prepare_transfers_text_silently(self):
        self.bot.notice_output = None
        source = NS(cleanup=Mock())
        self.bot._create_audio_source = Mock(return_value=source)
        async def install(*args, **kwargs):
            self.bot.notice_output = self.output
            return True
        self.bot._install_prepared_notice_output = AsyncMock(side_effect=install)
        with patch.object(Path, 'is_file', return_value=True):
            result = await handle_notice_command(self.bot,
                self.command('prepare', path=str(Path('offline-notice.mp3').absolute()), text='새 공지 문구'), self.status)
        self.assertTrue(result['accepted'])
        self.assertEqual(self.output.notice_text, '새 공지 문구')
        self.assertEqual(self.tasks, [])
        self.channel.send.assert_not_awaited()


class PreparedTextTests(unittest.TestCase):
    def test_long_korean_notice_uses_bounded_utf8_local_request(self):
        text = '알'*10000
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(dict(pid=12, id='req', accepted=True)).encode()
        with patch('notice_output_bridge.urllib.request.urlopen', return_value=response) as urlopen:
            request_notice(12345, 'offline-secret', 12, dict(id='req', action='prepare', text=text))
        data = urlopen.call_args.args[0].data
        self.assertLess(len(data), 65536)
        self.assertEqual(json.loads(data)['text'], text)

    def test_current_prepared_lease_carries_its_exact_speech_text(self):
        pool = PreparedSpeechPool()
        key = (('9', '18'), 'voice', '실제 송출 문구')
        entry = dict(path='offline-notice.mp3', leases=0)
        pool.cache[key] = entry
        pool.bindings[('9', 'request')] = ((1, 0), key)
        request = dict(id='request', tts_text='실제 송출 문구', token=('9', 'request', 1, 0))
        with patch.object(Path, 'is_file', return_value=True):
            lease = pool.acquire('9', request)
        self.assertEqual(lease.text, request['tts_text'])
        sender = Mock(return_value=dict(accepted=True, state='ready'))
        DiscordNoticeTransport(sender).prepare_prepared(lease)
        self.assertEqual(sender.call_args.args[0]['text'], request['tts_text'])
        self.assertEqual(sender.call_args.args[0]['action'], 'prepare')


if __name__ == '__main__':
    unittest.main()

"""Isolated source checks; no Discord/Tk runtime, network, audio or AppData writes."""
import ast
import asyncio
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from discord_connection_diagnostics import (
    VoiceConnectionDiagnostics, gateway_heartbeat_latency, voice_connection_snapshot,
)
from discord_voice_connection_order import VoiceLeaveBarrier


def isolated_bot_type():
    tree = ast.parse(Path(__file__).with_name("boss_timer_discord_bot.py").read_text(encoding="utf-8-sig"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DiscordScheduleBot")
    methods = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name in {"_observe_gateway_packet", "_authority_voice_client_type",
                                 "_voice_leave_barrier", "_confirm_voice_leave", "_wait_for_voice_leave"}]
    cls = ast.ClassDef(name="Bot", bases=[], keywords=[], body=methods, decorator_list=[])
    env = {"json": json, "time": time, "log": Mock(), "STATUS": NS(runtime_id="test"),
           "ConnectionPolicy": NS(has_authority=lambda *a, **k: True),
           "VoiceConnectionDiagnostics": VoiceConnectionDiagnostics,
           "gateway_heartbeat_latency": gateway_heartbeat_latency}
    env["VoiceLeaveBarrier"] = VoiceLeaveBarrier
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), "<source>", "exec"), env)
    return env["Bot"], env


class GatewayLatencyTests(unittest.TestCase):
    def test_sdk_rtt_is_used_without_raw_send_observation(self):
        self.assertEqual(gateway_heartbeat_latency(NS(latency=.031)), .031)

    def test_unavailable_or_nonfinite_measurements_remain_unknown(self):
        for value in (float("nan"), float("inf"), -.1, None, "invalid"):
            self.assertIsNone(gateway_heartbeat_latency(NS(latency=value)))
        self.assertIsNone(gateway_heartbeat_latency(NS()))

    def test_old_raw_send_timestamp_cannot_accumulate_into_latency(self):
        Bot, env = isolated_bot_type()
        bot = Bot()
        bot.gateway_last_heartbeat_sent_at = time.monotonic() - 9000
        bot.gateway_last_heartbeat_latency = None
        bot._observe_gateway_packet('{"op":11}')
        self.assertIsNotNone(bot.gateway_last_heartbeat_ack_at)
        self.assertIsNone(bot.gateway_last_heartbeat_latency)
        bot._observe_gateway_packet('{"op":10,"d":{"heartbeat_interval":41250}}')
        self.assertIsNone(bot.gateway_last_heartbeat_ack_at)

    def test_real_sdk_measurement_is_cached_before_close_and_reset_for_new_socket(self):
        Bot, env = isolated_bot_type()
        bot = Bot()
        bot.client = NS(latency=.045)
        bot.gateway_last_heartbeat_latency = None
        bot._observe_gateway_packet('{"op":11}')
        self.assertEqual(bot.gateway_last_heartbeat_latency, .045)
        bot.client.latency = float("inf")
        bot._observe_gateway_packet('{"op":7}')
        self.assertEqual(bot.gateway_last_heartbeat_latency, .045)
        bot._observe_gateway_packet('{"op":10}')
        self.assertIsNone(bot.gateway_last_heartbeat_latency)


class VoiceDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.lines = []
        self.socket = NS(close_code=None)
        self.ws = NS(ws=self.socket, poll_event=AsyncMock(return_value="result"))
        self.original_connect = AsyncMock(return_value=self.ws)
        self.state = NS(_connect_websocket=self.original_connect, state=NS(name="connected"),
                        reconnect=False, _runner=None, _connector=None, _expecting_disconnect=False)
        self.voice = NS(channel=NS(id=123), authority_generation=7, _connection=self.state)
        self.observer = VoiceConnectionDiagnostics(self.voice, self.state, self.lines.append)

    async def connect(self, resume=False):
        self.observer.install()
        return await self.state._connect_websocket(resume=resume)

    async def test_success_preserves_arguments_and_return_values(self):
        ws = await self.connect(True)
        self.assertIs(ws, self.ws)
        self.original_connect.assert_awaited_once_with(resume=True)
        self.assertEqual(await ws.poll_event(), "result")
        self.assertIn("resume=1", self.lines[0])

    async def test_close_code_and_sdk_state_are_recorded_before_same_exception_is_raised(self):
        class Closed(Exception):
            code = 4015
        error = Closed("secret-token-and-endpoint")
        self.ws.poll_event = AsyncMock(side_effect=error)
        ws = await self.connect()
        with self.assertRaises(Closed) as caught:
            await ws.poll_event()
        self.assertIs(caught.exception, error)
        line = self.lines[-1]
        self.assertIn("close_code=4015", line)
        self.assertIn("sdk_state=connected", line)
        self.assertIn("sdk_reconnect=0", line)
        self.assertNotIn("secret-token", line)

    async def test_timeout_is_distinct_from_socket_close(self):
        self.ws.poll_event = AsyncMock(side_effect=TimeoutError())
        ws = await self.connect()
        with self.assertRaises(TimeoutError):
            await ws.poll_event()
        self.assertIn("failure_kind=timeout", self.lines[-1])

    async def test_intentional_close_reason_and_cancellation_are_preserved(self):
        self.voice.boss_disconnect_reason = "authority_lost"
        self.ws.poll_event = AsyncMock(side_effect=asyncio.CancelledError())
        ws = await self.connect()
        with self.assertRaises(asyncio.CancelledError):
            await ws.poll_event()
        self.assertIn("voice_sdk_reader_cancelled", self.lines[-1])
        self.assertIn("program_reason=authority_lost", self.lines[-1])

    async def test_logging_failure_cannot_mask_connection_failure(self):
        error = TimeoutError()
        self.observer.write = Mock(side_effect=OSError())
        self.original_connect.side_effect = error
        self.observer.install()
        with self.assertRaises(TimeoutError) as caught:
            await self.state._connect_websocket(resume=False)
        self.assertIs(caught.exception, error)

    async def test_sdk_replaces_websocket_on_reconnect_and_new_reader_is_observed(self):
        await self.connect()
        second = NS(ws=NS(close_code=4014), poll_event=AsyncMock(side_effect=TimeoutError()))
        self.original_connect.return_value = second
        ws = await self.state._connect_websocket(resume=True)
        self.assertIs(ws, second)
        with self.assertRaises(TimeoutError):
            await ws.poll_event()
        self.assertIn("voice_sdk_websocket_interrupted", self.lines[-1])

    def test_snapshot_omits_tokens_endpoints_and_session_ids(self):
        self.state.token = "token-secret"
        self.state.endpoint = "endpoint-secret"
        self.state.session_id = "session-secret"
        self.assertNotIn("secret", str(voice_connection_snapshot(self.voice)))

    def test_missing_sdk_hook_does_not_change_state(self):
        state = NS(state=NS(name="disconnected"))
        VoiceConnectionDiagnostics(self.voice, state, self.lines.append).install()
        self.assertFalse(hasattr(state, "_connect_websocket"))
        self.assertIn("voice_diagnostic_unavailable", self.lines[-1])


class AuthorityDiagnosticIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_authority_guards_and_sdk_callbacks_keep_their_original_calls(self):
        class BaseVoice:
            def __init__(self, client, channel):
                self.channel = channel
                self.guild = channel.guild
                self.stop = Mock()
                self.base_cleanup = Mock()
                self.state_updates = AsyncMock(return_value="state-result")
                self.server_updates = AsyncMock(return_value="server-result")
                self._connection = self.create_connection_state()

            def create_connection_state(self):
                self.original_voice_connect = AsyncMock()
                self.original_voice_disconnect = AsyncMock()
                return NS(_voice_connect=self.original_voice_connect,
                          _voice_disconnect=self.original_voice_disconnect,
                          _connect_websocket=AsyncMock(return_value=NS(poll_event=AsyncMock())),
                          disconnect=AsyncMock(), state=NS(name="connected"))

            async def on_voice_state_update(self, data):
                return await self.state_updates(data)

            async def on_voice_server_update(self, data):
                return await self.server_updates(data)

            def cleanup(self):
                self.base_cleanup()

        Bot, env = isolated_bot_type()
        bot = Bot()
        bot.discord = NS(VoiceClient=BaseVoice)
        bot.connection_policy = NS(snapshot=lambda: {"authority_generation": 7})
        bot.authority_disconnect_generation = -1
        voice = bot._authority_voice_client_type(7)(None, NS(id=123, guild=NS(id=99, voice_client=None)))
        voice.guild.voice_client = voice
        await voice._connection._voice_connect(self_deaf=True)
        voice.original_voice_connect.assert_awaited_once_with(self_deaf=True)
        await voice._connection._voice_disconnect()
        voice.original_voice_disconnect.assert_awaited_once_with()
        state_data = {"channel_id": None, "session_id": "private-session"}
        server_data = {"token": "private-token", "endpoint": "private-endpoint"}
        self.assertEqual(await voice.on_voice_state_update(state_data), "state-result")
        self.assertEqual(await voice.on_voice_server_update(server_data), "server-result")
        voice.state_updates.assert_awaited_once_with(state_data)
        voice.server_updates.assert_awaited_once_with(server_data)
        await voice.disconnect(force=True)
        voice._connection.disconnect.assert_awaited_once_with(force=True, wait=True)
        voice.base_cleanup.assert_called_once()
        messages = "\n".join(call.args[0] for call in env["log"].call_args_list)
        self.assertIn("voice_sdk_channel_changed", messages)
        self.assertIn("voice_sdk_server_update", messages)
        self.assertNotIn("private-", messages)


class VoiceLeaveOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_ack_blocks_replacement_even_after_registry_cleanup(self):
        gate = VoiceLeaveBarrier()
        self.assertTrue(gate.begin())
        gate.finish_send()
        waiter = asyncio.create_task(gate.wait(timeout=.2))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        gate.confirm()
        self.assertTrue(await waiter)

    async def test_ack_before_send_returns_does_not_release_next_join_early(self):
        gate = VoiceLeaveBarrier()
        gate.begin()
        gate.confirm()
        self.assertTrue(gate.pending)
        self.assertFalse(await gate.wait(timeout=.005))
        gate.finish_send()
        self.assertTrue(await gate.wait())

    async def test_timeout_and_cancellation_do_not_discard_outstanding_leave(self):
        gate = VoiceLeaveBarrier()
        gate.begin()
        gate.finish_send()
        self.assertFalse(await gate.wait(timeout=.005))
        self.assertTrue(gate.pending)
        waiter = asyncio.create_task(gate.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertTrue(gate.pending)
        gate.confirm()
        self.assertTrue(await gate.wait())

    async def test_duplicate_leave_is_coalesced_and_guilds_are_isolated(self):
        Bot, env = isolated_bot_type()
        bot = Bot()
        first = bot._voice_leave_barrier(99)
        self.assertIs(first, bot._voice_leave_barrier('99'))
        first.begin()
        self.assertFalse(first.begin())
        first.finish_send()
        bot._confirm_voice_leave(100)
        self.assertTrue(first.pending)
        bot._confirm_voice_leave(99)
        self.assertTrue(await first.wait())

    async def test_old_sdk_leave_then_new_join_and_late_old_cleanup(self):
        class BaseVoice:
            def __init__(self, client, channel):
                self.channel, self.guild = channel, channel.guild
                self.stop = Mock()
                self._connection = self.create_connection_state()

            def create_connection_state(self):
                self.leave = AsyncMock()
                self.join = AsyncMock()
                return NS(_voice_connect=self.join, _voice_disconnect=self.leave,
                          state=NS(name='connected'))

            def cleanup(self):
                self.guild.voice_client = None

        Bot, env = isolated_bot_type()
        bot = Bot()
        bot.discord = NS(VoiceClient=BaseVoice)
        bot.connection_policy = NS(snapshot=lambda: {'authority_generation':7})
        bot.authority_disconnect_generation = -1
        guild = NS(id=99, voice_client=None)
        Voice = bot._authority_voice_client_type(7)
        old = Voice(None, NS(id=123, guild=guild))
        guild.voice_client = old
        await old._connection._voice_disconnect()
        old.cleanup()  # The exact SDK race: removed before Discord's leave ACK.
        new = Voice(None, NS(id=123, guild=guild))
        guild.voice_client = new
        new_join = asyncio.create_task(new._connection._voice_connect(self_deaf=True))
        await asyncio.sleep(0)
        new.join.assert_not_awaited()
        await old._connection._voice_disconnect()  # A superseded same-generation reader.
        old.leave.assert_awaited_once()
        bot._confirm_voice_leave(99)  # Global callback works without the old SDK object.
        await new_join
        new.join.assert_awaited_once_with(self_deaf=True)
        old.cleanup()
        self.assertIs(guild.voice_client, new)
        await old._connection._voice_disconnect()
        old.leave.assert_awaited_once()

    async def test_permission_lost_while_waiting_prevents_join(self):
        class BaseVoice:
            def __init__(self, client, channel):
                self.channel, self.guild = channel, channel.guild
                self._connection = self.create_connection_state()

            def create_connection_state(self):
                self.join = AsyncMock()
                return NS(_voice_connect=self.join, _voice_disconnect=AsyncMock())

        Bot, env = isolated_bot_type()
        bot = Bot()
        bot.discord = NS(VoiceClient=BaseVoice)
        generation = {'authority_generation':7}
        bot.connection_policy = NS(snapshot=lambda: generation)
        bot.authority_disconnect_generation = -1
        guild = NS(id=99, voice_client=None)
        voice = bot._authority_voice_client_type(7)(None, NS(id=123, guild=guild))
        guild.voice_client = voice
        gate = bot._voice_leave_barrier(99)
        gate.begin()
        gate.finish_send()
        joining = asyncio.create_task(voice._connection._voice_connect())
        await asyncio.sleep(0)
        generation['authority_generation'] = 8
        bot._confirm_voice_leave(99)
        with self.assertRaises(RuntimeError):
            await joining
        voice.join.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

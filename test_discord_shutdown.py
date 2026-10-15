"""Offline shutdown fences; no GUI, real bot, process termination or network."""
import ast
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from discord_authority import DiscordAuthority, AuthorityError


def methods(path, names, namespace):
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    nodes = [node for owner in tree.body if isinstance(owner, ast.ClassDef)
             for node in owner.body if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef)
             if node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<shutdown methods>', 'exec'), namespace)
    return tree


class GuiShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.namespace = dict(ConnectionPolicy=Mock(), tk=NS(TclError=RuntimeError), threading=threading)
        names = {'_start_discord_bot_runtime', '_recover_discord_bot_runtime',
                 '_reconnect_discord_bot_runtime_from_request', '_monitor_discord_bot_voice_bridge_health',
                 '_schedule_discord_bot_status_poll', '_stop_discord_bot_runtime_core'}
        tree = methods('boss_timer_gui.py', names, cls.namespace)
        closing = deepcopy(next(node for owner in tree.body if isinstance(owner, ast.ClassDef)
                                for node in owner.body if isinstance(node, ast.FunctionDef) and node.name=='on_close'))
        # Execute shutdown initiation only, omitting unrelated window/settings cleanup.
        cutoff = next(index for index, node in enumerate(closing.body) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Attribute) and target.attr=='running' for target in node.targets))
        closing.body = closing.body[:cutoff]
        exec(compile(ast.Module(body=[closing],type_ignores=[]), '<close initiation>', 'exec'), cls.namespace)

    def test_exit_and_process_stop_block_recovery_before_reading_settings(self):
        for flag in ('discord_bot_exit_requested', 'discord_bot_shutdown_in_progress'):
            app = NS(**{flag:True})
            self.namespace['_recover_discord_bot_runtime'](app, 'missing process')
            self.namespace['_monitor_discord_bot_voice_bridge_health'](app, {})
            self.assertFalse(self.namespace['_start_discord_bot_runtime'](app))
            self.assertFalse(self.namespace['_reconnect_discord_bot_runtime_from_request'](app,{})[0])

    def test_exit_cancels_status_timer_and_does_not_create_another(self):
        app = NS(discord_bot_exit_requested=True, discord_bot_status_after_id='timer', root=Mock())
        self.namespace['_schedule_discord_bot_status_poll'](app)
        app.root.after_cancel.assert_called_once_with('timer')
        app.root.after.assert_not_called()

    def test_process_stop_pauses_voice_policy_before_shutdown_work(self):
        policy=NS(pause=Mock(), snapshot=Mock(return_value={}))
        app=NS(_query_discord_bot_status_port=Mock(return_value={}), discord_bot_process=None,
               _get_discord_bot_config_storage_path=Mock(return_value='profile.ini'), _append_debug_log=Mock())
        self.namespace['ConnectionPolicy']=Mock(return_value=policy)
        self.assertTrue(self.namespace['_stop_discord_bot_runtime_core'](app))
        policy.pause.assert_called_once()
        self.assertFalse(app.discord_bot_running)

    def test_close_fences_callbacks_and_releases_record_after_verified_stop(self):
        coordinator=NS(alive=True,release_after_stop=Mock())
        app=NS(_close_desktop_banner=Mock(),_close_web_service=Mock(),_reset_master_developer_author_clicks=Mock(),
               _stop_discord_bot_runtime_core=Mock(return_value=True), discord_handover=coordinator)
        def check_flags():
            self.assertTrue(app.discord_bot_exit_requested)
            self.assertFalse(app.discord_bot_expected_running)
        app._close_web_service.side_effect=check_flags
        with patch.object(threading,'Thread',side_effect=lambda **kw:NS(start=kw['target'])):
            self.namespace['on_close'](app)
        coordinator.release_after_stop.assert_called_once()
        self.assertFalse(coordinator.alive)

    def test_exiting_coordinator_cannot_renew_or_grant(self):
        for flag in ('discord_bot_exit_requested','discord_bot_shutdown_in_progress'):
            node=DiscordAuthority.__new__(DiscordAuthority)
            node.app=NS(**{flag:True})
            with self.assertRaises(AuthorityError):
                node._assert_profile()


class BotShutdownTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.status=NS(shutdown_requested=threading.Event(), runtime_id='runtime')
        cls.namespace=dict(STATUS=cls.status, asyncio=asyncio, log=Mock())
        methods('boss_timer_discord_bot.py',{'_send_allowed','_connection_is_blocked','_shutdown_watch_loop'},cls.namespace)

    async def asyncSetUp(self):
        self.status.shutdown_requested.set()

    async def test_shutdown_blocks_voice_and_audio_even_if_old_lease_is_valid(self):
        app=NS(_connection_state=Mock(side_effect=AssertionError('old lease must not be consulted')))
        self.assertTrue(self.namespace['_connection_is_blocked'](app))
        self.assertFalse(self.namespace['_send_allowed'](app))

    async def test_shutdown_cancels_inflight_join_before_closing_voice_and_gateway(self):
        pending=asyncio.create_task(asyncio.sleep(3600))
        guild=NS(me=NS(voice=NS(channel=NS(id=777))), change_voice_state=AsyncMock())
        bot=NS(authority_connect_task=pending, notice_output=None,
               connection_policy=NS(snapshot=Mock(return_value={})),
               voice_client=NS(), _disconnect_authority_voice=AsyncMock(),
               client=NS(get_guild=Mock(return_value=guild),close=AsyncMock()))
        await self.namespace['_shutdown_watch_loop'](bot)
        self.assertTrue(pending.cancelled())
        bot._disconnect_authority_voice.assert_awaited_once()
        guild.change_voice_state.assert_not_awaited()
        bot.client.close.assert_awaited_once()

    async def test_standby_shutdown_does_not_leave_other_pcs_shared_bot_voice(self):
        guild=NS(me=NS(voice=NS(channel=NS(id=777))), change_voice_state=AsyncMock())
        bot=NS(authority_connect_task=None, notice_output=None, voice_client=None,
               connection_policy=NS(snapshot=Mock(return_value={})),
               _disconnect_authority_voice=AsyncMock(), client=NS(get_guild=Mock(return_value=guild),close=AsyncMock()))
        await self.namespace['_shutdown_watch_loop'](bot)
        bot._disconnect_authority_voice.assert_not_awaited()
        guild.change_voice_state.assert_not_awaited()
        bot.client.close.assert_awaited_once()

    async def test_parent_exit_fences_its_current_voice_request_before_disconnect(self):
        policy=NS(snapshot=Mock(return_value=dict(runtime_id='runtime',voice_requested=True,authority_request='owned')),
                  request_stop=Mock(),pause=Mock())
        bot=NS(authority_connect_task=None,notice_output=None,voice_client=None,connection_policy=policy,
               client=NS(close=AsyncMock()))
        await self.namespace['_shutdown_watch_loop'](bot)
        policy.request_stop.assert_called_once_with('runtime','owned')
        policy.pause.assert_called_once()


if __name__=='__main__':
    unittest.main()

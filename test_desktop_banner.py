"""Isolated notice relay checks; never send real notifications or start bots."""
import json
import ast
import asyncio
from pathlib import Path
import tempfile
import time
import unittest
import sys
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from desktop_banner import BannerInbox, DiscordBannerRelay, WindowsBanner, compact_text, message_notice, notice_style


def message(description="스카디 5분 남았습니다.", **values):
    return dict({"id": "100", "channel_id": "10", "author": {"id": "20"},
                 "content": "", "embeds": [{"title": "✦ 스카디 ✦ 오후 1:00",
                 "description": "```ansi\n\x1b[36m" + description + "\x1b[0m\n```"}]}, **values)


class BannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.relay = DiscordBannerRelay(self.temporary.name)
        self.inbox = self.relay.inbox

    def observe(self, data, **values):
        return self.relay.observe(data, channel_id="10", bot_id="20", **values)

    def test_first_warning_edit_and_fresh_spawn(self):
        self.assertIn("5분", self.observe(message())[1])
        changed = message("스카디 5분 남았습니다.\n스카디 1분 남았습니다.")
        self.assertEqual(self.observe(changed, edited=True)[1], "스카디 1분 남았습니다.")
        spawned = message("스카디 5분 남았습니다.\n스카디 1분 남았습니다.\n스카디 젠", id="101")
        self.assertEqual(self.observe(spawned)[1], "스카디 젠")

    def test_duplicate_and_embed_metadata_edit_are_ignored(self):
        original = message()
        self.observe(original)
        self.assertIsNone(self.observe(original))
        original["embeds"][0]["footer"] = {"text": "INTERNAL_SECRET"}
        self.assertIsNone(self.observe(original, edited=True))

    def test_changed_title_alone_is_not_another_notice(self):
        self.observe(message())
        modified = message()
        modified["embeds"][0]["title"] = "✦ 스카디 ✦ 오후 1:01"
        self.assertIsNone(self.observe(modified, edited=True))

    def test_foreign_channel_user_and_private_reply_are_ignored(self):
        self.assertIsNone(self.observe(message(channel_id="11")))
        self.assertIsNone(self.observe(message(author={"id": "21"})))
        self.assertIsNone(self.observe(message(flags=64)))
        self.assertIsNone(self.observe(message(), edited=True))

    def test_registered_other_administrator_bot_is_allowed(self):
        self.assertIsNotNone(self.observe(message(author={"id": "21"}), bot_ids=["21"]))

    def test_all_notice_types_and_footer_privacy(self):
        for title in ("관리자 접속 시작", "관리자 접속 완료", "알리미 안내", "음성 연결 복구"):
            data = message(embeds=[{"title": title, "description": "안내 내용",
                            "fields": [{"name": "담당자", "value": "나츠"}],
                            "footer": {"text": "PRIVATE_PROTOCOL_DATA"}}])
            result = message_notice(data)
            self.assertEqual(result[0], title)
            self.assertIn("담당자: 나츠", result[1])
            self.assertNotIn("PRIVATE", result[1])

    def test_long_notice_and_ansi_are_readable(self):
        result = compact_text("```ansi\n\x1b[31m" + "내용 " * 100 + "\x1b[0m\n```", 180)
        self.assertLessEqual(len(result), 180)
        self.assertTrue(result.endswith("…"))
        self.assertNotIn("\x1b", result)
        self.assertNotIn("```", result)

    def test_disabled_has_no_backlog(self):
        self.inbox.set_enabled(False)
        self.inbox.publish("제목", "내용", "first", "10")
        self.inbox.set_enabled(True)
        self.assertEqual(self.inbox.drain("10", True), [])

    def test_enabled_deduplicated_delivery(self):
        self.inbox.set_enabled(True)
        self.inbox.publish("제목", "내용", "first", "10")
        self.assertEqual(len(self.inbox.drain("10", True)), 1)
        self.inbox.publish("제목", "내용", "first", "10")
        self.assertEqual(self.inbox.drain("10", True), [])

    def test_stale_other_channel_disabled_and_malformed_events_are_discarded(self):
        self.inbox.set_enabled(True)
        self.inbox.publish("제목", "내용", "first", "11")
        (self.inbox.directory / "invalid.json").write_text("broken", encoding="utf-8")
        old = {"title": "제목", "body": "내용", "key": "old", "channel_id": "10",
               "created_at": time.time() - 60}
        (self.inbox.directory / "old.json").write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(self.inbox.drain("10", True), [])
        self.inbox.publish("제목", "내용", "disabled", "10")
        self.assertEqual(self.inbox.drain("10", False), [])
        self.assertEqual(list(self.inbox.directory.glob("*.json")), [self.inbox.control])

    def test_native_output_keeps_user_text_out_of_shell_code(self):
        banner = WindowsBanner()
        banner.set_enabled(True)
        body = "보스 & <안내> $(무시); '한글'"
        banner.jobs.put((time.time(), "보탐매니저", body, True))
        process = MagicMock(returncode=0)
        def communicate(payload, timeout):
            self.assertEqual(json.loads(payload)["body"], body)
            self.assertTrue(json.loads(payload)["quiet"])
            banner.stop.set()
            return None, b""
        process.communicate.side_effect = communicate
        with patch.dict(sys.modules, {"winreg": MagicMock()}), \
                patch("desktop_banner.os.name", "nt"), \
                patch("desktop_banner.subprocess.Popen", return_value=process) as launch:
            banner._run()
        self.assertNotIn(body, " ".join(launch.call_args.args[0]))
        self.assertEqual(launch.call_args.kwargs["creationflags"], 0x08000000)
        self.assertTrue(banner.errors.empty())

    def test_native_failure_is_reported_without_raising_to_caller(self):
        banner = WindowsBanner()
        banner.set_enabled(True)
        banner.jobs.put((time.time(), "제목", "내용", False))
        def fail(*args, **kwargs):
            banner.stop.set()
            raise OSError("native_notification_unavailable")
        with patch.dict(sys.modules, {"winreg": MagicMock()}), \
                patch("desktop_banner.os.name", "nt"), \
                patch("desktop_banner.subprocess.Popen", side_effect=fail):
            banner._run()
        self.assertEqual(banner.errors.get_nowait(), "native_notification_unavailable")

    def test_notice_colors_survive_edit_and_ipc(self):
        def colored(timing, warning_color):
            return f"\x1b[35m엘드르 \x1b[1;{warning_color}m{timing}\x1b[0m"
        first = message(embeds=[{"title": "✦ 엘드르 ✦", "description":
                        "```ansi\n" + colored("5분 남았습니다.", 33) + "\n```"}])
        self.observe(first)
        edited = message(embeds=[{"title": "✦ 엘드르 ✦", "description":
                         "```ansi\n" + colored("5분 남았습니다.", 33) + "\n"
                         + colored("1분 남았습니다.", 31) + "\n```"}])
        notice = self.observe(edited, edited=True)
        self.inbox.set_enabled(True)
        self.inbox.publish(*notice)
        result = self.inbox.drain("10", True)[0]
        spans = result["style"]["spans"]
        self.assertEqual("".join(span["text"] for span in spans), result["body"])
        self.assertEqual(spans[0]["color"], "#c084fc")
        self.assertEqual(spans[1]["color"], "#fb7185")
        self.assertNotIn("5분", result["body"])

    def test_long_colored_notice_matches_compacted_text(self):
        body = "긴 공지 " * 100
        style = notice_style({"content": "\x1b[36m" + body + "\x1b[0m"}, body)
        self.assertEqual("".join(span["text"] for span in style["spans"]), compact_text(body, 180))
        self.assertEqual(style["spans"][0]["color"], "#22d3ee")


class BotRelayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Use the real callback without importing/starting the bot application.
        source = Path("boss_timer_discord_bot.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        method = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.AsyncFunctionDef) and node.name == "_relay_desktop_banner")
        cls.namespace = {"asyncio": asyncio, "DiscordBannerRelay": DiscordBannerRelay,
                         "log": lambda *_: None}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "relay-callback", "exec"), cls.namespace)
        cls.host_class = type("Host", (), {"_relay_desktop_banner": cls.namespace[method.name]})

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.namespace["get_user_config_dir"] = lambda: self.temporary.name
        self.host = self.host_class()
        self.host.config = {"text_channel_id": "10"}
        self.host.client = SimpleNamespace(user=SimpleNamespace(id=20))
        self.inbox = BannerInbox(self.temporary.name)
        self.inbox.set_enabled(True)

    async def test_own_notice_and_edit_do_not_need_authority_or_registry_queries(self):
        await self.host._relay_desktop_banner(message())
        self.assertEqual(len(self.inbox.drain("10", True)), 1)
        update = message("스카디 5분 남았습니다.\n스카디 1분 남았습니다.")
        update.pop("author")
        await self.host._relay_desktop_banner(update, edited=True)
        self.assertEqual(self.inbox.drain("10", True)[0]["body"], "스카디 1분 남았습니다.")

    async def test_other_channel_and_user_chat_do_not_query_authority_registry(self):
        await self.host._relay_desktop_banner(message(channel_id="11"))
        await self.host._relay_desktop_banner(message(author={"id": "21", "bot": False}))
        self.assertEqual(self.inbox.drain("10", True), [])

    async def test_connected_administrator_bot_notice_is_received_without_own_authority(self):
        self.host.connection_policy = SimpleNamespace(snapshot=lambda: {"query_members": [
            {"online": True, "bot_user_id": "21"}]})
        await self.host._relay_desktop_banner(message(author={"id": "21", "bot": True}))
        self.assertEqual(len(self.inbox.drain("10", True)), 1)


if __name__ == "__main__":
    unittest.main()

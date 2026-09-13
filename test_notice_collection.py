from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import threading
import sys
import unittest
from unittest.mock import Mock

from notice_module.payload.cafe_source import (
    LIST_URL, KST, Page, canonical_article, category_for, date_of, parse_pinned, parse_article,
)
from notice_module.payload.notice_collector import NoticeCollector
from notice_module.payload.notice_management import NoticeStore, NoticeError
from notice_module.payload.main import NoticePlugin


def row(key="CT9G/100", title="서버 이전 안내", date="26.09.13", **kwargs):
    css = kwargs.get("css", "notice cmt_on")
    return f'''<li class="{css}"><a href="/odin/{key}?" class="link_cafe">
    <span class="txt_notice">공지</span><span class="txt_detail">{title}</span>
    <span class="created_at">{date}</span><span class="view_count">9999</span></a>
    <a class="link_cmt" href="/odin/{key}/comments">123</a></li>'''


def listing(*rows):
    return '<div id="noticeContainer"><div id="notices"><ul class="list_cafe">' + ''.join(rows) + '</ul></div></div>'


def article(text="<p>판매 기간 안내</p>", title="서버 이전 안내", views="100"):
    return f'''<h3><span class="article_title">{title}</span></h3>
    <span class="num_subject">26.09.13</span><span class="num_subject">{views}</span>
    <div id="article" class="tx-content-container">{text}</div>
    <div class="comments">댓글 내용은 수집하지 않음</div>'''


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 13, 18, 0, tzinfo=KST)

    def test_successful_parse_does_not_raise_generator_exit_in_parser(self):
        exits = []
        def trace(frame, event, arg):
            if event == "exception" and arg[0] is GeneratorExit and frame.f_code.co_filename.endswith("cafe_source.py"):
                exits.append(frame.f_code.co_name)
            return trace
        previous = sys.gettrace()
        try:
            sys.settrace(trace)
            item = parse_pinned(listing(row()), self.now)[0]
            parse_article(article(), item, self.now)
            category_for("계정 보호 안내")
        finally:
            sys.settrace(previous)
        self.assertEqual(exits, [])

    def test_only_pinned_no_lower_numbered_duplicate_or_javascript(self):
        html = listing(row()) + '<ul class="list_cafe">' + row("CT9G/101", "일반 글") + '</ul>'
        html += '<script>articles.push({dataid:102, title:"읽지 않을 글"})</script>'
        parsed = parse_pinned(html, self.now)
        self.assertEqual([r["id"] for r in parsed], ["CT9G/100"])
        self.assertEqual(parsed[0]["title"], "서버 이전 안내")

    def test_cross_board_pinned_and_old_dates_remain_in_catalog(self):
        rows = parse_pinned(listing(row("DEH7/275", "업데이트 상세 내역", "26.09.08"),
                                    row("CT9G/1952", "이벤트 아이템", "26.09.02")), self.now)
        self.assertEqual(rows[0]["id"], "DEH7/275")
        self.assertEqual(rows[1]["published_date"], "2026-09-02")

    def test_excluded_permanent_notices(self):
        for title in ("계정 보호 조치 안내", "PC 이용 시 런처 관련 오류", "카카오게임즈 운영정책 개정 안내"):
            self.assertIsNone(category_for(title))
        self.assertEqual(category_for("임시점검 연장 안내"), "maintenance")
        self.assertEqual(category_for("신규 전직 알케미스트"), "class_change")

    def test_truncation_login_and_changed_markup_are_not_empty_success(self):
        for html in ("<html>로그인</html>", listing(row())[:-12], listing(row(css="new_notice_class")),
                     listing('<li class="notice"><span>제목만 있음</span></li>')):
            with self.subTest(html=html), self.assertRaises(NoticeError):
                parse_pinned(html, self.now)
        self.assertEqual(parse_pinned(listing(), self.now), [])

    def test_duplicate_pinned_row_is_deduplicated(self):
        self.assertEqual(len(parse_pinned(listing(row(), row()), self.now)), 1)

    def test_foreign_urls_refused(self):
        for url in ("https://example.com/odin/CT9G/100", "javascript:alert(1)",
                    "https://m.cafe.daum.net/another/CT9G/100", "http://m.cafe.daum.net/odin/CT9G/100"):
            with self.assertRaises(NoticeError):
                canonical_article(url)

    def test_body_keeps_table_context_excludes_struck_and_hidden_text(self):
        markup = '''<p>판매 기간</p><table><tr><td>1회차</td><td><s>12:00</s><b>13:00</b></td></tr></table>
        <span style="text-decoration: line-through">옛 날짜</span><span style="display: none">숨김</span>
        <script>do_not_run()</script><img src="https://example.com/event.png">'''
        parsed = parse_article(article(markup), parse_pinned(listing(row()), self.now)[0], self.now)
        self.assertIn("1회차 | 13:00 |", parsed["body"])
        for forbidden in ("12:00", "옛 날짜", "숨김", "do_not_run", "댓글 내용"):
            self.assertNotIn(forbidden, parsed["body"])
        self.assertEqual(len(parsed["images"]), 1)

    def test_comments_views_and_style_do_not_change_content_hash(self):
        item = parse_pinned(listing(row()), self.now)[0]
        first = parse_article(article('<p>공지 내용</p>', views="10"), item, self.now)
        second = parse_article(article('<p style="color:red">공지 내용</p>', views="9000"), item, self.now)
        self.assertEqual(first["content_hash"], second["content_hash"])

    def test_image_only_article_requires_review(self):
        item = parse_pinned(listing(row()), self.now)[0]
        result = parse_article(article('<img src="https://example.com/event.png">'), item, self.now)
        self.assertEqual(result["body"], "")
        self.assertIn("확인 필요", result["review_note"])

    def test_incomplete_body_preserves_old_data_via_exception(self):
        item = parse_pinned(listing(row()), self.now)[0]
        with self.assertRaises(NoticeError):
            parse_article('<span class="article_title">제목</span><div id="article" class="tx-content-container">중간', item, self.now)

    def test_relative_and_absolute_dates(self):
        self.assertEqual(date_of("26.09.09", self.now), "2026-09-09")
        self.assertEqual(date_of("2026.09.09", self.now), "2026-09-09")
        self.assertEqual(date_of("2시간 4분 전", self.now), "2026-09-13")
        self.assertIsNone(date_of("날짜 불명", self.now))


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boss-notice-collector-unit-")
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 9, 13, 18, 0, tzinfo=KST)
        self.root = Path(self.temp.name)
        self.store = NoticeStore(self.root, "odin9", clock=lambda: self.now)
        self.html = listing(row())
        self.body = article()
        self.fetch = Mock(side_effect=lambda url, cached=None: Page(self.html if url == LIST_URL else self.body, etag='"test"'))
        self.collector = NoticeCollector(self.fetch)

    def test_collects_sources_without_creating_audio_events(self):
        self.assertTrue(self.collector.collect_once(self.store))
        state = self.store.snapshot()
        self.assertEqual(state["events"], {})
        self.assertEqual(state["articles"]["CT9G/100"]["revision"], 1)
        self.assertTrue(state["articles"]["CT9G/100"]["baseline"])
        self.assertEqual(state["collection"]["status"], "수집 완료")

    def test_due_time_persists_and_manual_check_bypasses_hours(self):
        self.collector.collect_once(self.store)
        self.fetch.reset_mock()
        self.assertFalse(self.collector.collect_once(self.store))
        self.fetch.assert_not_called()
        self.now = self.now.replace(hour=2) + timedelta(days=1)
        self.assertFalse(self.collector.collect_once(self.store))
        self.assertTrue(self.collector.collect_once(self.store, manual=True))

    def test_disabled_collection_cannot_be_forced(self):
        settings = self.store.snapshot()["settings"]
        settings["collection_enabled"] = False
        self.store.configure(settings)
        self.assertFalse(self.collector.collect_once(self.store, manual=True))
        self.fetch.assert_not_called()

    def test_permanent_and_disabled_categories_do_not_fetch_bodies(self):
        self.html = listing(row(), row("CT9G/101", "계정 보호 안내"), row("DEH7/10", "업데이트 공지"))
        settings = self.store.snapshot()["settings"]
        settings["categories"]["update"] = False
        self.store.configure(settings)
        self.collector.collect_once(self.store)
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(len(self.store.snapshot()["articles"]), 1)

    def test_identical_content_does_not_increment_revision(self):
        self.collector.collect_once(self.store)
        old = self.store.snapshot()["articles"]["CT9G/100"]
        self.collector.collect_once(self.store, manual=True)
        new = self.store.snapshot()["articles"]["CT9G/100"]
        self.assertEqual(old["revision"], new["revision"])
        self.assertEqual(old["changed_at"], new["changed_at"])
        self.body = article('<p>판매 시간 수정</p>')
        self.collector.collect_once(self.store, manual=True)
        self.assertEqual(self.store.snapshot()["articles"]["CT9G/100"]["revision"], 2)

    def test_http_not_modified_reuses_body(self):
        self.collector.collect_once(self.store)
        self.collector.fetch = lambda url, cached=None: Page(self.html) if url == LIST_URL else Page(not_modified=True)
        self.collector.collect_once(self.store, manual=True)
        self.assertEqual(self.store.snapshot()["articles"]["CT9G/100"]["revision"], 1)

    def test_failed_list_does_not_remove_notices_or_alerts(self):
        self.collector.collect_once(self.store)
        self.store.register({"id": "test-alert", "title": "판매 안내", "source_key": "CT9G/100/open"})
        self.html = "<html>차단/로그인</html>"
        self.assertFalse(self.collector.collect_once(self.store, manual=True))
        state = self.store.snapshot()
        self.assertTrue(state["articles"]["CT9G/100"]["pinned"])
        self.assertNotIn("retired_at", state["events"]["test-alert"])

    def test_partial_body_failure_preserves_old_body_and_defers_removal(self):
        self.collector.collect_once(self.store)
        old = self.store.snapshot()["articles"]["CT9G/100"]["body"]
        self.body = "<html>본문 오류</html>"
        self.collector.collect_once(self.store, manual=True)
        state = self.store.snapshot()
        self.assertEqual(state["articles"]["CT9G/100"]["body"], old)
        self.assertEqual(state["collection"]["status"], "부분 수집 실패")
        self.html = listing(row("CT9G/101"))
        self.collector.collect_once(self.store, manual=True)
        self.assertTrue(self.store.snapshot()["articles"]["CT9G/100"]["pinned"])

    def test_complete_empty_list_retires_sources_and_related_events(self):
        self.collector.collect_once(self.store)
        self.store.register({"id": "test-alert", "title": "판매 안내", "source_key": "CT9G/100/open"})
        self.html = listing()
        self.collector.collect_once(self.store, manual=True)
        state = self.store.snapshot()
        self.assertFalse(state["articles"]["CT9G/100"]["pinned"])
        self.assertEqual(state["events"]["test-alert"]["retired_reason"], "공지 해제")
        self.now += timedelta(days=30)
        self.assertEqual(self.store.snapshot()["articles"], {})

    def test_server_switch_does_not_redirect_results(self):
        other = NoticeStore(self.root, "odin8", clock=lambda: self.now)
        self.collector.collect_once(self.store)
        self.assertEqual(other.snapshot().get("articles", {}), {})
        self.assertIn("CT9G/100", self.store.snapshot()["articles"])

    def test_disable_while_fetching_discards_results(self):
        def fetch(url, cached=None):
            if url != LIST_URL:
                settings = self.store.snapshot()["settings"]
                settings["collection_enabled"] = False
                self.store.configure(settings)
            return Page(self.html if url == LIST_URL else self.body)
        self.collector.fetch = fetch
        self.assertFalse(self.collector.collect_once(self.store))
        self.assertEqual(self.store.snapshot().get("articles", {}), {})

    def test_stop_while_fetching_discards_results(self):
        def fetch(url, cached=None):
            self.collector.stop_event.set()
            return Page(self.html)
        self.collector.fetch = fetch
        self.assertFalse(self.collector.collect_once(self.store))
        self.assertEqual(self.store.snapshot().get("articles", {}), {})

    def test_old_request_token_cannot_replace_newer_collection(self):
        first, _ = self.store.claim_collection(manual=True)
        second, _ = self.store.claim_collection(manual=True)
        self.assertFalse(self.store.finish_collection(first, [], {}, {}))
        self.assertEqual(self.store.snapshot()["collection"]["token"], second)

    def test_worker_is_bound_to_captured_server_and_never_reads_tk(self):
        host = Mock(data_root=self.root)
        plugin = NoticePlugin(host)
        plugin.stopped = False
        called = []
        plugin.collector = Mock()
        plugin.collector.collect_once.side_effect = lambda store, **_: called.append(store.server_id)
        self.assertTrue(plugin._launch_collection("odin9"))
        plugin.collection_worker.join(timeout=2)
        self.assertEqual(called, ["odin9"])
        host.get_server.assert_not_called()
        host.root.assert_not_called()


if __name__ == "__main__":
    unittest.main()

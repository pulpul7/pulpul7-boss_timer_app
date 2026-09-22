"""All-scenario defaults, server isolation and safe updates; no live audio."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from copy import deepcopy

from notice_module.payload.notice_templates import (TEMPLATES, EXAMPLES, render_template, validate_template,
                                                    migrate_participation_templates)
from notice_module.payload.notice_management import NoticeStore, NoticeError, KST
from notice_module.payload.notice_analysis import notice_events, analyze_notice
from notice_module.payload.notice_seasons import season_text, CHEERS
from notice_module.payload.notice_template_ui import NoticeTemplateWindow
from test_notice_analysis import source, TRANSFER


class CatalogTests(unittest.TestCase):
    def test_every_scenario_has_valid_default_and_example(self):
        self.assertEqual(len(TEMPLATES), 42)
        for key, item in TEMPLATES.items():
            with self.subTest(key=key):
                self.assertTrue(item["condition"])
                self.assertTrue(render_template({}, key, EXAMPLES))

    def test_unknown_period_cannot_claim_a_missing_deadline(self):
        for key in TEMPLATES:
            if key.startswith("unknown."):
                with self.assertRaises(ValueError):
                    validate_template(key, "{종료시간}까지 구매하세요.")

    def test_only_named_fields_are_allowed(self):
        for text in ("{공지제목.__class__}", "{공지제목[0]}", "{공지제목!r}", "{공지제목:>10000000}", "{잘못된항목}", "{", "{}"):
            with self.assertRaises(ValueError):
                validate_template("discovery.general", text)
        self.assertEqual(render_template({"tts_templates": {"discovery.general": "{{안내}} {공지제목}"}},
                                         "discovery.general", {"공지제목": "내용"}), "{안내} 내용")

    def test_missing_actual_value_is_not_replaced_by_example(self):
        with self.assertRaises(ValueError):
            render_template({}, "transfer.preview", {})

    def test_manual_and_evening_share_catalog_but_dawn_morning_stay_separate(self):
        self.assertNotIn('participation.evening', TEMPLATES)
        for key in ('participation.general', 'participation.dawn', 'participation.morning'):
            self.assertIn(key, TEMPLATES)
        text = render_template({}, 'participation.general', {'알림제목': '길드던전', '시작시간': '09월 10일 22시 30분',
            '_start_date': '2026-09-10'}, now=datetime(2026, 9, 10, 18, tzinfo=KST))
        self.assertIn('오늘 22시 30분 길드던전', text)

    def test_merge_preserves_custom_text_flags_and_history_once(self):
        state = {'generation': 0, 'tts_templates': {'participation.evening': '{보스목록} 참여해 주세요'},
                 'tts_template_enabled': {'participation.evening': False},
                 'events': {'active': {'tts_template': 'participation.evening', 'tts_text': '직접 쓴 문장', 'manual_tts': True},
                            'history': {'tts_template': 'participation.evening', 'retired_at': 'yesterday'}}}
        history = deepcopy(state['events']['history'])
        migrate_participation_templates(state)
        self.assertEqual(state['tts_templates']['participation.general'], '{보스목록} 참여해 주세요')
        self.assertFalse(state['tts_template_enabled']['participation.general'])
        self.assertEqual(state['events']['active']['tts_template'], 'participation.general')
        self.assertEqual(state['events']['active']['tts_text'], '직접 쓴 문장')
        self.assertEqual(state['events']['history'], history)
        before = deepcopy(state)
        migrate_participation_templates(state)
        self.assertEqual(state, before)
        state = {'generation': 0, 'tts_templates': {'participation.general': '원래 문장', 'participation.evening': '저녁 문장'}}
        migrate_participation_templates(state)
        self.assertEqual(state['tts_templates']['participation.general'], '원래 문장')
        self.assertEqual(state['tts_templates']['participation.evening'], '저녁 문장')

    def test_same_day_range_omits_only_repeated_date_without_mutating_values(self):
        values = dict(EXAMPLES)
        before = dict(values)
        text = render_template({}, "maintenance.regular", values)
        self.assertIn("09월 20일 18시 30분부터 23시 59분까지입니다.", text)
        self.assertEqual(values, before)

    def test_different_day_month_or_year_keeps_end_date(self):
        for end in ("09월 21일 01시 00분", "10월 20일 23시 59분"):
            values = dict(EXAMPLES, 종료시간=end)
            self.assertIn(end, render_template({}, "maintenance.regular", values))
        values = dict(EXAMPLES, _start_date="2026-09-20", _end_date="2027-09-20")
        self.assertIn(values["종료시간"], render_template({}, "maintenance.regular", values))

    def test_end_only_and_end_before_start_keep_date(self):
        self.assertIn(EXAMPLES["종료시간"], render_template({}, "transfer.start", EXAMPLES))
        state = {"tts_templates": {"transfer.start": "종료 {종료일시}, 시작 {시작일시}"}}
        self.assertEqual(render_template(state, "transfer.start", EXAMPLES),
                         "종료 09월 20일 23시 59분, 시작 09월 20일 18시 30분")

    def test_custom_range_and_literal_text(self):
        state = {"tts_templates": {"transfer.start": "{{판매}} {시작일시} ~ {종료일시}입니다."}}
        self.assertEqual(render_template(state, "transfer.start", EXAMPLES),
                         "{판매} 09월 20일 18시 30분 ~ 23시 59분입니다.")
        literal = "9월 20일 18시부터 9월 20일 23시까지"
        self.assertEqual(render_template({"tts_templates": {"transfer.start": literal}},
                                         "transfer.start", EXAMPLES), literal)

    def test_analysis_keeps_full_dates_but_shortens_same_day_speech(self):
        article = source("점검 일시: 9/10 07:00 ~ 11:00", "maintenance", "임시점검 안내")
        events = notice_events(article, analyze_notice(article))
        item = next(event for event in events if event["tts_template"] == "maintenance.temporary")
        self.assertIn("09월 10일 07시 00분부터 11시 00분까지", item["tts_text"])
        self.assertEqual(item["tts_values"]["종료시간"], "09월 10일 11시 00분")
        self.assertEqual(item["tts_values"]["_start_date"], "2026-09-10")
        self.assertEqual(item["tts_values"]["_end_date"], "2026-09-10")
        self.assertTrue(item["event_until"])

    def test_analysis_links_all_generated_events_to_catalog(self):
        articles = [source(TRANSFER, title="18차 서버 이전 안내"),
                    source("기간 미정", "class_change", "신규 직업 안내"),
                    source("점검 일시: 9/10 07:00 ~ 11:00", "maintenance", "임시점검 연장 안내"),
                    source("업데이트", "update", "업데이트 후 확인된 문제 안내")]
        for article in articles:
            events = notice_events(article, analyze_notice(article))
            self.assertTrue(events)
            for item in events:
                self.assertIn(item["tts_template"], TEMPLATES)
                self.assertEqual(item["tts_text"], render_template({}, item["tts_template"], item["tts_values"]))


class StoreTemplateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-notice-template-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = datetime(2026, 9, 10, 18, tzinfo=KST)
        self.store = NoticeStore(self.root, "odin9", clock=lambda: self.now)
        settings = self.store.snapshot()["settings"]
        settings["output_enabled"] = True
        self.store.configure(settings)
        self.event = {"id": "event", "title": "일반 공지", "category": "general", "source_key": "CT9G/1/general",
                      "tts_template": "discovery.general", "tts_values": {"공지제목": "실제 제목"},
                      "valid_from": self.now, "valid_until": self.now + timedelta(hours=1)}

    def save(self, key, text, revision=None):
        state = self.store.snapshot()
        templates = {name: state.get("tts_templates", {}).get(name, spec["text"]) for name, spec in TEMPLATES.items()}
        templates[key] = text
        return self.store.save_tts_templates(templates, expected_revision=state.get("tts_templates_revision", 0) if revision is None else revision)

    def set_type(self, key, enabled):
        state = self.store.snapshot()
        templates = {name: state.get('tts_templates', {}).get(name, item['text']) for name, item in TEMPLATES.items()}
        flags = {name: state.get('tts_template_enabled', {}).get(name, True) for name in TEMPLATES}
        flags[key] = enabled
        return self.store.save_tts_templates(templates, expected_revision=state.get('tts_templates_revision', 0), enabled=flags)

    def test_type_disabled_before_registration_persists_and_is_server_scoped(self):
        self.set_type('discovery.general', False)
        self.store = NoticeStore(self.root, 'odin9', clock=lambda: self.now)
        self.store.register(self.event, collected=True)
        self.assertIsNone(self.store.delivery_token('event'))
        other = NoticeStore(self.root, 'odin8', clock=lambda: self.now)
        self.assertNotIn('discovery.general', other.snapshot()['tts_template_enabled'])
        self.set_type('discovery.general', True)
        self.assertIsNotNone(self.store.delivery_token('event'))

    def test_type_switch_blocks_edited_text_and_invalidates_off_on_token(self):
        self.store.register(self.event, collected=True)
        self.store.edit_tts('event', '개별 편집')
        token = self.store.delivery_token('event')
        before = self.store.snapshot()['events']['event']
        self.set_type('discovery.general', False)
        self.assertIsNone(self.store.automatic_candidate())
        self.assertFalse(self.store.complete_delivery(token))
        self.assertEqual(before, self.store.snapshot()['events']['event'])
        self.set_type('discovery.general', True)
        self.assertFalse(self.store.complete_delivery(token))
        self.assertIsNotNone(self.store.delivery_token('event'))
        self.assertEqual(before, self.store.snapshot()['events']['event'])

    def test_toggle_does_not_replay_complete_or_override_individual_disable(self):
        self.store.register(self.event, collected=True)
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token('event')))
        self.set_type('discovery.general', False)
        self.set_type('discovery.general', True)
        self.assertIsNone(self.store.delivery_token('event'))
        self.store.register(dict(self.event, id='other'))
        self.store.set_enabled('other', False)
        self.set_type('discovery.general', False)
        self.set_type('discovery.general', True)
        self.assertFalse(self.store.snapshot()['events']['other']['enabled'])

    def test_manual_custom_participation_also_obeys_switch(self):
        self.store.register(dict(self.event, id='manual-guild', title='길드던전', category='participation',
            tts_template='', tts_text='사용자 길던 문장', event_from=self.now + timedelta(hours=3)))
        context = dict(id='boss1', server_id='odin9', at=self.now, kind='boss', chapter=7,
                       major=True, phase='one_minute_complete')
        # Manual entries now feed the shared evening summary, never extra speech.
        self.assertIsNone(self.store.delivery_token('manual-guild', opportunity=context))
        from notice_module.payload.notice_schedule import synchronize_schedule
        synchronize_schedule(self.store, dict(server_id='odin9', season='18', events=[]))
        first = next(item for item in self.store.snapshot()['events'].values() if item['id'].endswith('/evening_first'))
        self.assertIsNotNone(self.store.delivery_token(first['id']))
        self.set_type('participation.general', False)
        self.assertIsNone(self.store.delivery_token(first['id']))
        self.assertIsNone(self.store.opportunity_candidate(context))
        self.assertEqual(self.store.snapshot()['events']['manual-guild']['tts_text'], '사용자 길던 문장')

    def test_invalid_flags_and_stale_checkbox_editor_do_not_overwrite(self):
        state = self.store.snapshot()
        templates = {key: item['text'] for key, item in TEMPLATES.items()}
        with self.assertRaises(NoticeError):
            self.store.save_tts_templates(templates, expected_revision=0, enabled={'discovery.general': 'false'})
        self.assertEqual(self.store.snapshot(), state)
        self.set_type('discovery.general', False)
        with self.assertRaises(NoticeError):
            self.store.save_tts_templates(templates, expected_revision=0, enabled=dict.fromkeys(TEMPLATES, True))
        self.assertFalse(self.store.snapshot()['tts_template_enabled']['discovery.general'])

    def test_defaults_can_be_saved_with_no_events_and_survive_restart(self):
        self.save("discovery.general", "새 소식입니다. {공지제목}")
        self.store = NoticeStore(self.root, "odin9", clock=lambda: self.now)
        self.store.register(self.event, collected=True)
        self.assertEqual(self.store.snapshot()["events"]["event"]["tts_text"], "새 소식입니다. 실제 제목")
        other = NoticeStore(self.root, "odin8", clock=lambda: self.now)
        self.assertFalse(other.snapshot().get("tts_templates"))

    def test_pending_auto_text_updated_and_old_delivery_token_invalidated(self):
        self.store.register(self.event, collected=True)
        token = self.store.delivery_token("event")
        before = self.store.snapshot()["events"]["event"]
        self.save("discovery.general", "확인해 주세요. {공지제목}")
        after = self.store.snapshot()["events"]["event"]
        self.assertEqual(after["tts_text"], "확인해 주세요. 실제 제목")
        self.assertEqual(before["valid_until"], after["valid_until"])
        self.assertFalse(self.store.complete_delivery(token))

    def test_individual_text_and_disabled_flag_preserved(self):
        self.store.register(self.event, collected=True)
        self.store.edit_tts("event", "개별 편집 문장")
        self.store.set_enabled("event", False)
        self.save("discovery.general", "새 기본 문구 {공지제목}")
        self.store.register(self.event, collected=True)
        item = self.store.snapshot()["events"]["event"]
        self.assertEqual(item["tts_text"], "개별 편집 문장")
        self.assertEqual(item["generated_tts_text"], "새 기본 문구 실제 제목")
        self.assertFalse(item["enabled"])
        self.assertFalse(item["tts_review_required"])

    def test_completed_is_not_replayed_on_default_change_or_recollection(self):
        self.store.register(self.event, collected=True)
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token("event")))
        self.save("discovery.general", "기본 문구 변경 {공지제목}")
        self.store.register(self.event, collected=True)
        self.assertIsNone(self.store.delivery_token("event"))

    def test_history_is_unchanged(self):
        self.store.register(self.event, collected=True)
        self.store.discard("event")
        before = self.store.snapshot()["events"]["event"]
        self.save("discovery.general", "새 문장")
        self.assertEqual(before, self.store.snapshot()["events"]["event"])

    def test_blank_disables_text_and_restore_uses_factory_default(self):
        self.store.register(self.event, collected=True)
        self.save("discovery.general", "")
        self.assertIsNone(self.store.delivery_token("event"))
        self.save("discovery.general", TEMPLATES["discovery.general"]["text"])
        self.assertIsNotNone(self.store.delivery_token("event"))
        self.assertNotIn("discovery.general", self.store.snapshot()["tts_templates"])

    def test_conflicting_editor_rejected(self):
        self.save("discovery.general", "첫 창")
        with self.assertRaises(NoticeError):
            self.save("discovery.general", "오래된 창", revision=0)
        self.assertEqual(self.store.snapshot()["tts_templates"]["discovery.general"], "첫 창")

    def test_invalid_template_saves_nothing(self):
        before = self.store.snapshot()
        with self.assertRaises(NoticeError):
            self.save("discovery.general", "{종료시간}")
        self.assertEqual(before, self.store.snapshot())

    def test_season_five_slots_still_rotate(self):
        state = {"tts_templates": {"season.close.2": "{차수}차도 수고하셨습니다."}}
        self.assertEqual(season_text(state, 18), CHEERS[0])
        self.assertEqual(season_text(state, 19), "19차도 수고하셨습니다.")
        self.assertEqual(season_text(state, 23), CHEERS[0])

    def test_legacy_collected_event_links_from_stored_analysis(self):
        article = source("일반 공지입니다.", "general", "실제 제목")
        article["first_seen"] = self.now.isoformat()
        article["analysis"] = analyze_notice(article)
        event = notice_events(article, article["analysis"])[0]
        event.pop("tts_template")
        event.pop("tts_values")
        self.store.register(event, collected=True)
        with self.store._transaction() as state:
            state["articles"] = {article["id"]: article}
        self.save("discovery.general", "기본값 갱신 {공지제목}")
        self.assertEqual(self.store.snapshot()["events"][event["id"]]["tts_text"], "기본값 갱신 실제 제목")


class TemplateUiActions(unittest.TestCase):
    def test_checkbox_only_edit_is_saved_and_close_confirms(self):
        ui = NoticeTemplateWindow.__new__(NoticeTemplateWindow)
        ui.owner, ui.store, ui.status, ui.window = Mock(), Mock(), Mock(), Mock()
        ui.owner._guard.return_value = True
        ui.owner.app._show_centered_messagebox.return_value = False
        ui.drafts = ui.saved = {'discovery.general': '문장'}
        ui.enabled, ui.saved_enabled = {'discovery.general': False}, {'discovery.general': True}
        ui._capture, ui._populate = Mock(), Mock()
        ui.revision = 0
        ui.close()
        ui.window.destroy.assert_not_called()
        ui.owner.app._show_centered_messagebox.assert_called_once()
        ui.save()
        ui.store.save_tts_templates.assert_called_once_with(ui.drafts, expected_revision=0, enabled=ui.enabled)
        self.assertEqual(ui.enabled, ui.saved_enabled)

    def test_participation_form_has_hints_not_submittable_examples(self):
        from notice_module.payload.notice_management_ui import NoticeManagementWindow
        ui = NoticeManagementWindow.__new__(NoticeManagementWindow)
        ui._form = Mock()
        ui._add(participation=True)
        fields = ui._form.call_args.args[1]
        examples = ui._form.call_args.kwargs['examples']
        self.assertTrue(all(value == '' for key, label, value in fields))
        self.assertEqual(set(examples), {key for key, label, value in fields})
        self.assertIn('길드던전', examples['tts'])

    def test_placeholder_never_writes_example_into_form_value(self):
        from notice_module.payload.ui_helpers import example_entry
        variable = Mock()
        variable.get.return_value = ''
        with patch('notice_module.payload.ui_helpers.ttk.Entry') as entry, patch('notice_module.payload.ui_helpers.tk.Label') as label:
            entry.return_value.focus_get.return_value = None
            example_entry(Mock(), variable, '길드던전')
            label.return_value.place.assert_called_once()
            variable.set.assert_not_called()
            variable.get.return_value = '직접 입력'
            refresh = variable.trace_add.call_args.args[1]
            refresh()
            label.return_value.place_forget.assert_called_once()
            variable.set.assert_not_called()

    def test_preview_uses_example_not_store_or_discord(self):
        ui = NoticeTemplateWindow.__new__(NoticeTemplateWindow)
        ui.owner = Mock()
        ui.owner._guard.return_value = True
        ui.preview, ui.store, ui.status = Mock(), Mock(), Mock()
        ui._render_sample = Mock(return_value="예시 문장")
        ui.listen()
        ui.preview.start.assert_called_once_with("예시 문장")
        self.assertEqual(ui.store.mock_calls, [])


if __name__ == "__main__":
    unittest.main()

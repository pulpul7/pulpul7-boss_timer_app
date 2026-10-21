"""Public selection catalog and update transport; no GUI or network."""
from copy import deepcopy
import ast
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from web_schedule_snapshot import _collect_catalog, _catalog_id, collect_snapshot, validate_snapshot
from web_schedule_cache import ScheduleCache
from web_service_common import PROTOCOL, schedule_path, write_json


def app_fixture():
    definitions = [dict(boss_name="티르", alias="티", area="요툰하임", star=True),
                   dict(boss_name="코요아", alias="", area="요툰하임"),
                   dict(boss_name="절대 보스", alias="절보", area="요툰하임", absolute=True),
                   dict(boss_name="신규 보스", alias="신보", area="새 지역"),
                   dict(boss_name="던전 보스", alias="던보", area="던전")]
    aliases = {item["boss_name"]: item.get("alias") or item["boss_name"] for item in definitions}
    aliases.update({"지옥성채": "성채", "지옥성채 정예": "성채 정예"})
    canonical = lambda name: next((key for key, value in aliases.items() if value == name), name)
    definitions_by_name = {row["boss_name"]: row for row in definitions}
    app = SimpleNamespace(schedule_boss_definitions=definitions_by_name,
                          schedule_area_definitions={"새 지역": {"label": "9챕"}},
                          fixed_boss_entries=[dict(boss_name="월드보스", days=["화"], time_text="20:00:00"),
                                              dict(boss_name="지옥성채", days=["화"], time_text="21:30:00"),
                                              dict(boss_name="지옥성채", days=["수"], time_text="21:30:00"),
                                              dict(boss_name="지옥성채 정예"),
                                              dict(boss_name="방어전 신청")],
                          _get_schedule_boss_definition_items=lambda: definitions,
                          _is_schedule_boss_header_name=lambda name: False,
                          _get_display_boss_name=lambda name: aliases.get(name, name),
                          _get_canonical_boss_name=canonical,
                          _get_schedule_boss_definition_name=lambda name: canonical(name) if isinstance(name, str) else canonical(name.get("boss_name", "")),
                          _schedule_boss_has_star=lambda name: bool(definitions_by_name.get(canonical(name), {}).get("star")))
    return app, aliases


class BossCatalogTests(unittest.TestCase):
    def test_special_events_and_configured_anchor_variants(self):
        app, aliases = app_fixture()
        app.fixed_boss_entries += [dict(boss_name="무스펠 닻", time_text="22:30:00"),
                                  dict(boss_name="알브닻", time_text="20:30:00"),
                                  dict(boss_name="니다벨   닻", time_text="18:30:00"),
                                  dict(boss_name="니다벨닻", time_text="18:30:00"),
                                  dict(boss_name="길드 던전", time_text="21:00:00")]
        # A common display alias must not merge regional anchor entries.
        app._get_canonical_boss_name = lambda name: "닻" if str(name).replace(" ", "").endswith("닻") else name
        rows = _collect_catalog(app)
        anchors = [row for row in rows if row["kind"] == "fixed" and row["name"].endswith("닻")]
        self.assertEqual([row["label"] for row in anchors], ["니다벨 닻", "알브 닻", "무스펠 닻"])
        special = [row for row in rows if row["group"] == "특수 이벤트"]
        self.assertEqual([row["name"] for row in special], ["길던", "사용자이벤트", "체크된침공", "침공"])
        self.assertEqual([row["label"] for row in special][-2:], ["체크된침공", "전체침공"])
        self.assertEqual(next(row for row in special if row['name'] == '침공')['id'], _catalog_id('boss', '침공'))
        self.assertEqual(len({row["id"] for row in anchors}), 3)
        app.fixed_boss_entries = [dict(boss_name="닻", time_text="18:30:00"), dict(boss_name="닻", time_text="20:30:00")]
        self.assertEqual([r["label"] for r in _collect_catalog(app) if r["kind"] == "fixed"], ["닻"])

    def test_chapter_cards_follow_real_ocr_top_and_bottom_slots(self):
        source = Path('boss_timer_gui.py').read_text(encoding='utf-8-sig')
        def table(name):
            start = source.index(name + ' = {')
            end = source.index('\n}\n', start) + 2
            return ast.literal_eval(source[start:end].split('=', 1)[1].strip())
        orders, grids = table('RECORD_BOOK_BOSS_ORDER'), table('SCHEDULE_OCR_SLOT_GRID')
        app, _ = app_fixture()
        definitions = [dict(boss_name=name, area=area, alias=name)
                       for area, names in orders.items() for name in reversed(names)]
        app._get_schedule_boss_definition_items = lambda: definitions
        rows = _collect_catalog(app, boss_orders=orders, slot_grid=grids)
        for area, names in orders.items():
            for index, name in enumerate(names):
                row = next(item for item in rows if item['kind'] == 'boss' and item['name'] == name)
                top = grids[area]['top']
                self.assertEqual(row['grid_columns'], max(grids[area].values()))
                self.assertEqual(row['grid_row'], 1 if index < top else 2)
                self.assertEqual(row['grid_column'], index + 1 if index < top else index - top + 1)

    def test_invalid_slot_cannot_enter_catalog(self):
        data = self.snapshot()
        data['boss_catalog'][0].update(grid_columns=3, grid_row=2, grid_column=4)
        with self.assertRaises(ValueError): validate_snapshot(data)

    def test_chapters_future_chapter_dungeon_and_fixed_priority(self):
        app, _ = app_fixture()
        rows = _collect_catalog(app)
        self.assertEqual([row["label"] for row in rows[:3]], ["티", "코요아", "절보"])
        self.assertEqual([row["group"] for row in rows[:5]], ["2챕", "2챕", "2챕", "9챕", "던전"])
        self.assertTrue(rows[2]["absolute"])
        fixed = [row for row in rows if row["kind"] == "fixed"]
        self.assertEqual(fixed[0]["name"], "지옥성채")
        self.assertTrue(fixed[0]["major"])
        self.assertFalse(next(row for row in fixed if row["name"] == "지옥성채 정예")["major"])
        self.assertEqual(sum(row["name"] == "지옥성채" for row in fixed), 1)
        self.assertNotIn("방어전 신청", [row["name"] for row in fixed])

    def test_alias_change_does_not_change_selection_identity(self):
        app, aliases = app_fixture()
        before = _collect_catalog(app)
        app.schedule_boss_definitions["티르"]["alias"] = "티르 새약칭"
        aliases["지옥성채"] = "새 성채"
        after = _collect_catalog(app)
        self.assertEqual([row["id"] for row in before], [row["id"] for row in after])
        self.assertEqual(after[0]["label"], "티르 새약칭")
        self.assertEqual(next(row for row in after if row["name"] == "지옥성채")["label"], "새 성채")

    def test_fixed_rename_preserves_catalog_identity_and_configured_colors(self):
        from fixed_boss_aliases import GROUPS
        from web_schedule_snapshot import _major, _web_colors
        app, _ = app_fixture()
        for canonical, aliases in GROUPS:
            for name in aliases:
                with self.subTest(name=name):
                    app.fixed_boss_entries = [dict(boss_name=name)]
                    row = next(row for row in _collect_catalog(app) if row['id'] == _catalog_id('fixed', canonical))
                    self.assertTrue(row['legacy_ids'])
                    if canonical in {'쟁탈전', '방어전', '점령전', '길던'}:
                        self.assertTrue(_major('fixed', name, False))
                    if canonical != '길던':
                        self.assertEqual(_web_colors(app, 'fixed', name, '#123456', '#654321'), ('#123456', '#654321'))

    def test_older_snapshots_restore_legacy_ids_and_merge_alias_duplicates(self):
        from test_fixed_boss_aliases import old_id
        data = self.snapshot()
        prototype = dict(data['boss_catalog'][0], kind='fixed', name='니다벨 닻', label='니다벨 닻')
        old, identity = old_id('fixed', '니다 닻'), _catalog_id('fixed', '니다벨 닻')
        data['boss_catalog'] = [dict(prototype, id=old), dict(prototype, id=identity, major=True)]
        data['events'] = [dict(id='b'*24, kind='fixed', name='니다 닻', catalog_id=old,
                              scheduled_at=data['captured_at'], end_at=data['captured_at'], note='')]
        data['active'] = [dict(name='니다 닻', status='출현 중', kind='fixed', catalog_id=old)]
        result = validate_snapshot(data)
        self.assertEqual(len(result['boss_catalog']), 1)
        self.assertEqual(result['boss_catalog'][0]['id'], identity)
        self.assertIn(old, result['boss_catalog'][0]['legacy_ids'])
        self.assertTrue(result['boss_catalog'][0]['major'])
        self.assertEqual(result['events'][0]['catalog_id'], identity)
        self.assertEqual(result['active'][0]['catalog_id'], identity)

    def test_guild_contest_deadline_is_not_a_selectable_boss(self):
        app, _ = app_fixture()
        for name in ("길드 쟁탈전 신청마감", "길드쟁탈전신청마감", "길드  쟁탈전 신청 마감 ★", "쟁탈전 신청마감"):
            with self.subTest(name=name):
                app.fixed_boss_entries = [dict(boss_name=name), dict(boss_name="쟁탈전")]
                before = deepcopy(app.fixed_boss_entries)
                rows = _collect_catalog(app)
                self.assertEqual([row["name"] for row in rows if row["kind"] == "fixed"], ["쟁탈전"])
                self.assertEqual(app.fixed_boss_entries, before)
                # A display alias must not let an excluded source back in.
                app._get_canonical_boss_name = lambda value: "신청 마감" if value == name else value
                rows = _collect_catalog(app)
                self.assertEqual([row["name"] for row in rows if row["kind"] == "fixed"], ["쟁탈전"])

    def test_snapshot_links_alias_invasion_and_fixed_rows_to_catalog(self):
        app, _ = app_fixture()
        now = datetime(2026, 10, 13, 18)
        app._get_schedule_reference_datetime = lambda: now
        app._get_schedule_visible_cutoff_datetime = lambda: None
        app.schedule_break_rows_enabled_var = SimpleNamespace(get=lambda: True)
        app._collect_schedule_share_rows = lambda start, end, **kw: (
            [dict(kind="event", boss_text="티", scheduled_at=now, has_star=True),
             dict(kind="event", boss_text="침공 티르", scheduled_at=now, is_invasion=True),
             dict(kind="event", boss_text="성채", scheduled_at=now.replace(hour=21, minute=30), is_fixed=True)], start, end)
        original_resolver = app._get_schedule_boss_definition_name
        app._get_schedule_boss_definition_name = lambda name: original_resolver((name if isinstance(name, str) else name.get('boss_name', '')).replace("침공 ", ""))
        app._schedule_boss_has_star = lambda name: str(name).replace("침공 ", "") in {"티", "티르"}
        app._get_schedule_invasion_effective_colors = lambda: ("#ffffff", "#123456")
        app._get_schedule_boss_display_name = lambda item, **kw: app._get_display_boss_name(item["boss_name"])
        app._normalize_fixed_boss_time_text = lambda text: text
        app._is_fixed_boss_entry_active_on_date = lambda *args, **kw: True
        app.schedule_control_events = []; app.schedule_active_entries = [dict(boss_name='침공 티르', is_invasion=True, state='active')]
        app.current_season_no = 18; app.season_history_map = {}; app._get_schedule_share_server_name = lambda: "오9"
        payload = collect_snapshot(app)
        rows = {row["kind"]: row for row in payload["events"]}
        self.assertEqual(rows["boss"]["catalog_id"], _catalog_id("boss", "티르"))
        self.assertEqual(rows["invasion"]["catalog_id"], _catalog_id("boss", "티르"))
        self.assertEqual(rows["fixed"]["catalog_id"], _catalog_id("fixed", "지옥성채"))
        self.assertEqual(payload['active'][0]['kind'], 'invasion')
        self.assertEqual(payload['active'][0]['catalog_id'], _catalog_id('boss', '티르'))
        clean = validate_snapshot(dict(payload, revision=1, updated_at=payload['captured_at']))
        self.assertEqual(clean['active'][0]['kind'], 'invasion')

    def test_special_rows_and_anchor_aliases_keep_real_source_identity(self):
        app, _ = app_fixture()
        now = datetime(2026, 10, 14, 18)
        app.fixed_boss_entries = [dict(boss_name="니다벨  닻", time_text="18:30:00", days=["수"],
                                       text_color="#123456", bg_color="#654321")]
        app._get_schedule_reference_datetime = lambda: now
        app._get_schedule_visible_cutoff_datetime = lambda: None
        app.schedule_break_rows_enabled_var = SimpleNamespace(get=lambda: False)
        app._collect_schedule_share_rows = lambda start, end, **kw: (
            [dict(kind="event", boss_text="길드 던전", scheduled_at=now),
             dict(kind="event", boss_text="길드 집합", scheduled_at=now),
             dict(kind="event", boss_text="닻", scheduled_at=now.replace(minute=30), is_fixed=True)], start, end)
        app._get_schedule_boss_definition_name = lambda value: ""
        app._get_schedule_boss_display_name = lambda item, **kw: "닻"
        app._get_canonical_boss_name = lambda value: "닻" if str(value).replace(" ", "").endswith("닻") else value
        app._normalize_fixed_boss_time_text = lambda value: value
        app._is_fixed_boss_entry_active_on_date = lambda *args, **kw: True
        app.schedule_control_events = [];app.schedule_active_entries = []
        app.current_season_no = 18;app.season_history_map = {};app._get_schedule_share_server_name = lambda: "오9"
        payload = collect_snapshot(app)
        rows = {row["name"]: row for row in payload["events"]}
        self.assertEqual(rows["길드 던전"]["catalog_id"], _catalog_id("boss", "길던"))
        self.assertEqual(rows["길드 집합"]["catalog_id"], _catalog_id("boss", "사용자이벤트"))
        self.assertEqual(rows["니다벨 닻"]["catalog_id"], _catalog_id("fixed", "니다벨닻"))
        self.assertEqual(rows["니다벨 닻"]["text_color"], "#123456")
        self.assertEqual(rows["니다벨 닻"]["bg_color"], "#654321")

    def snapshot(self):
        app, _ = app_fixture(); now = datetime.now(timezone.utc).isoformat()
        return dict(schema=1, server_name="오9", guild_name="딸기", season="18", revision=1,
                    window_start=now, window_end=now, captured_at=now, updated_at=now,
                    events=[], active=[], boss_catalog=_collect_catalog(app))

    def test_public_allowlist_and_legacy_snapshot_compatibility(self):
        data = self.snapshot();data["boss_catalog"][0]["token"] = "must-not-leak"
        clean = validate_snapshot(data)
        self.assertNotIn("token", clean["boss_catalog"][0])
        data.pop("boss_catalog")
        self.assertNotIn("boss_catalog", validate_snapshot(data))

    def test_invalid_catalog_cannot_enter_public_cache(self):
        for change in ({"id": "not-a-valid-id"}, {"order": True}, {"group": "x" * 101}):
            data = self.snapshot(); data["boss_catalog"][0].update(change)
            with self.assertRaises(ValueError): validate_snapshot(data)
        data = self.snapshot();data["boss_catalog"].append(deepcopy(data["boss_catalog"][0]))
        with self.assertRaises(ValueError): validate_snapshot(data)

    def test_catalog_updates_use_existing_status_poll_without_schedule_reload(self):
        with tempfile.TemporaryDirectory(prefix="boss-catalog-unit-") as folder:
            run_id = "a" * 32
            root = Path(folder);cache = ScheduleCache(root, run_id, Mock());data = self.snapshot()
            def publish():
                write_json(schedule_path(root, run_id), dict(data, run_id=run_id, protocol=PROTOCOL));cache.refresh()
            publish(); before = cache.status()
            self.assertNotIn("boss_catalog", cache.status(known_catalog_version=before["catalog_version"]))
            data["boss_catalog"][0]["label"] = "새 약칭";data["revision"] += 1;publish()
            after = cache.status(known_major_version=before["major_version"], known_catalog_version=before["catalog_version"])
            self.assertEqual(after["schedule_version"], before["schedule_version"])
            self.assertNotEqual(after["catalog_version"], before["catalog_version"])
            self.assertEqual(after["boss_catalog"][0]["label"], "새 약칭")
            self.assertNotIn("major_updates", after)

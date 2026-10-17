"""Offline identity and voice lookup checks; no GUI/bot process is started."""
import ast
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
from datetime import datetime
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from fixed_boss_aliases import (GROUPS, canonical_catalog_id, catalog_id,
                                legacy_catalog_ids, migrate_spectrum_numbers,
                                voice_name_candidates, fixed_entry_id)


def old_id(kind, name):
    return hashlib.sha256((kind + '|' + re.sub(r'\s+', '', name)).encode()).hexdigest()[:24]


def source_functions(path, names, namespace):
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(nodes) != len(names):
        raise AssertionError('missing or ambiguous production function')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
    return namespace


class FixedAliasTests(unittest.TestCase):
    def test_requested_groups_keep_identity_and_expose_every_old_id(self):
        for canonical, aliases in GROUPS:
            identity = catalog_id('fixed', canonical)
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertEqual(catalog_id('fixed', ' '.join(alias)), identity)
                    self.assertEqual(canonical_catalog_id(old_id('fixed', alias)), identity)
                    self.assertEqual(voice_name_candidates(alias)[0], alias)
                    self.assertTrue(set(aliases).issubset(voice_name_candidates(alias)))
                    self.assertIn(old_id('fixed', alias), [identity, *legacy_catalog_ids(identity)])

    def test_regions_and_registration_deadlines_are_distinct(self):
        names = ['니다 닻', '알브 닻', '무스펠 닻', '닻', '방어전 신청', '길드쟁탈전 신청마감', '쟁탈전']
        self.assertEqual(len({catalog_id('fixed', name) for name in names}), len(names))
        self.assertEqual(voice_name_candidates('방어전 신청'), ['방어전 신청', '방어전신청'])
        self.assertEqual(catalog_id('boss', '길드쟁탈전'), old_id('boss', '길드쟁탈전'))

    def test_spectrum_migration_is_idempotent_and_canonical_wins_conflicts(self):
        identity = catalog_id('fixed', '알브 닻')
        old = old_id('fixed', '알브하임 닻')
        untouched = 'a' * 24
        self.assertEqual(migrate_spectrum_numbers({old: 29, untouched: 7}), {identity: 29, untouched: 7})
        result = migrate_spectrum_numbers({old: 29, identity: 18, untouched: 7})
        self.assertEqual(result, {identity: 18, untouched: 7})
        self.assertEqual(migrate_spectrum_numbers(result), result)

    def test_bot_file_prefers_written_name_across_roots_then_uses_alias(self):
        with tempfile.TemporaryDirectory(prefix='fixed-alias-unit-') as folder:
            resource, app = (Path(folder) / name for name in ('resource', 'app'))
            for root in (resource, app):
                (root / 'voice/boss').mkdir(parents=True)
            fallback = resource / 'voice/boss/니다벨 닻.wav'
            original = app / 'voice/boss/니다 닻.mp3'
            fallback.write_bytes(b'fixture'); original.write_bytes(b'fixture')
            voice = source_functions('boss_timer_discord_bot.py', ['voice_file'],
                                     dict(RESOURCE_ROOT=resource, APP_ROOT=app))['voice_file']
            self.assertEqual(voice('boss', '니다 닻'), str(original))
            original.unlink()
            self.assertEqual(voice('boss', '니다 닻'), str(fallback))
            self.assertEqual(voice('boss', '알브 닻'), '')
            self.assertEqual(voice('boss', '니다벨 닻 신청'), '')
            self.assertEqual(voice('info', '니다 닻'), '')

    def test_gui_fixed_voice_tries_written_name_before_display_alias(self):
        namespace = source_functions('boss_timer_gui.py', ['_get_schedule_alarm_boss_voice_name_candidates'], dict(re=re))
        app = NS(_normalize_schedule_alarm_voice_stem=lambda s: re.sub(r'\s+', ' ', str(s).strip()),
                 _get_schedule_boss_display_name=lambda *a, **k: '닻',
                 _get_canonical_boss_name=lambda name: '닻', _get_display_boss_name=lambda name: name)
        names = namespace['_get_schedule_alarm_boss_voice_name_candidates'](
            app, item=dict(is_fixed=True, boss_name='알브하임 닻'))
        self.assertEqual(names[0], '알브하임 닻')
        self.assertIn('알브 닻', names)
        self.assertLess(names.index('알브 닻'), names.index('닻'))
        self.assertNotIn('니다 닻', names)

    def test_edit_preserves_identity_through_disk_and_admin_sync(self):
        names = ['_apply_fixed_boss_update', '_write_fixed_boss_definitions_file',
                 '_load_fixed_boss_definitions', '_build_github_fixed_boss_items',
                 '_normalize_github_fixed_boss_item']
        namespace = source_functions('boss_timer_gui.py', names,
            dict(re=re, os=os, shutil=shutil, tempfile=tempfile, datetime=datetime,
                 DEFAULT_FIXED_BOSS_TEXT_COLOR='#ffffff', DEFAULT_FIXED_BOSS_BG_COLOR='#123456'))
        entry = dict(boss_name='옛 이름', time_text='20:30:00', days=['수'], repeat_mode='weekly',
                     anchor_date='', enabled=True, text_color='#ffffff', bg_color='#123456')
        changed = dict(entry, boss_name='전혀 다른 이름', time_text='21:00:00', days=['목'])
        with tempfile.TemporaryDirectory(prefix='fixed-identity-unit-') as folder:
            path = Path(folder) / 'fixed.txt'
            app = NS(fixed_boss_entries=[entry], fixed_boss_selected_index=0,
                     fixed_boss_status_var=Mock(), fixed_boss_inline_status_var=Mock(),
                     schedule_boss_metrics_window_open=False, schedule_window=None,
                     _build_fixed_boss_entry_from_form=lambda: changed,
                     _get_canonical_boss_name=lambda name:name,
                     _normalize_fixed_boss_time_text=lambda s:s,
                     _normalize_fixed_boss_anchor_date_text=lambda s:s or '',
                     _normalize_hex_color=lambda s, default:s or default,
                     _normalize_optional_hex_color=lambda s:s,
                     _normalize_schedule_definition_line=lambda s:s.strip(),
                     _parse_schedule_definition_bool=lambda s, default:s != '0',
                     _get_schedule_fixed_bosses_storage_path=lambda:str(path), _ensure_init_dir=lambda:None)
            for name in ('_set_fixed_boss_add_button_active', '_handle_fixed_boss_alarm_definitions_changed',
                         '_sync_schedule_boss_metrics_with_definitions', '_save_settings', '_refresh_fixed_boss_list',
                         '_refresh_schedule_view', '_reset_fixed_boss_form', '_ensure_edge_tts_cache_for_names', '_append_debug_log'):
                setattr(app, name, Mock())
            write = namespace['_write_fixed_boss_definitions_file']
            app._write_fixed_boss_definitions_file=lambda entries:write(app, entries)
            app._save_fixed_boss_definitions=lambda:write(app, app.fixed_boss_entries)
            namespace['_apply_fixed_boss_update'](app)
            identity = fixed_entry_id(entry)
            self.assertEqual(fixed_entry_id(app.fixed_boss_entries[0]), identity)
            loaded = namespace['_load_fixed_boss_definitions'](app)
            self.assertEqual(loaded[0]['boss_name'], '전혀 다른 이름')
            self.assertEqual(loaded[0]['web_identity'], identity)
            app.fixed_boss_entries = loaded
            exported = namespace['_build_github_fixed_boss_items'](app)
            imported = namespace['_normalize_github_fixed_boss_item'](
                app, exported[0], preserved_by_identity={}, preserved_by_name={})
            self.assertEqual(imported['web_identity'], identity)
            self.assertEqual(imported['boss_name'], changed['boss_name'])
            self.assertTrue(write(app, [imported]))
            self.assertEqual(namespace['_load_fixed_boss_definitions'](app)[0], imported)
            from test_web_boss_catalog import app_fixture
            from web_schedule_snapshot import _collect_catalog, _fixed_catalog_id
            public, _ = app_fixture();public.fixed_boss_entries=[imported]
            row=next(row for row in _collect_catalog(public) if row['kind']=='fixed')
            self.assertEqual(row['id'], identity)
            self.assertEqual(_fixed_catalog_id(public, changed['boss_name']), identity)

    def test_legacy_file_without_identity_remains_readable(self):
        self.assertEqual(fixed_entry_id(dict(boss_name='알브하임 닻')), catalog_id('fixed', '알브 닻'))
        self.assertEqual(fixed_entry_id(dict(boss_name='알브 닻', web_identity='invalid')), catalog_id('fixed', '알브 닻'))

    def test_old_display_alias_resolution_and_anchor_regions_are_preserved(self):
        resolver=lambda name:{'성채':'지옥성채', '니다 닻':'닻', '알브 닻':'닻'}.get(name,name)
        self.assertEqual(fixed_entry_id(dict(boss_name='성채'),resolver),catalog_id('fixed','지옥성채'))
        self.assertEqual(fixed_entry_id(dict(boss_name='니다 닻'),resolver),catalog_id('fixed','니다벨 닻'))
        self.assertNotEqual(fixed_entry_id(dict(boss_name='알브 닻'),resolver),catalog_id('fixed','닻'))


if __name__ == '__main__':
    unittest.main()

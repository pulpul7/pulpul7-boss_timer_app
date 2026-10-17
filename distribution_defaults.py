"""Explicit, one-time distribution defaults. Never builds or copies credentials."""
from __future__ import annotations

import configparser
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
import re
import time
import uuid

from runtime_storage import atomic_write
from web_service_common import file_transaction
from distribution_catalog import OPTIONS, PROFILE_GROUPS, ROLLBACK_GROUPS
from distribution_settings_keys import GENERAL_KEYS

DECLARATION = 'init/distribution_defaults.json'
STATE = 'distribution_defaults/applied.json'
FILES = {
    'boss': ('init/schedule_boss_definitions.txt', 'init/schedule_area_definitions.txt'),
    'fixed': ('init/schedule_fixed_bosses.txt',),
    'spectrum': ('web_assets/boss_spectrum.json',),
}
FILES.update({key:(relative,) for key, _, _, relative, _ in OPTIONS})
CONFIG_KEYS = {
    'boss': {'schedule_color_data_enabled', 'schedule_share_use_boss_colors',
             'schedule_invasion_last_text_color', 'schedule_invasion_last_bg_color'},
    'fixed': {'fixed_boss_color_data_enabled', 'schedule_share_use_fixed_boss_colors',
              'fixed_boss_last_text_color', 'fixed_boss_last_bg_color'},
}
CONFIG_KEYS['boss_definition'] = CONFIG_KEYS['boss']
CONFIG_KEYS['settings'] = set(GENERAL_KEYS)
CONFIG_KEYS['metrics'] = {key for key in GENERAL_KEYS if key.startswith('schedule_boss_metric_')}
CONFIG_KEYS['breaks'] = {key for key in GENERAL_KEYS if key.startswith('schedule_break_')}


class DistributionDefaultsError(RuntimeError):
    """Startup must stop instead of loading partly applied distribution data."""


def revision_token(data):
    path = Path(data) / STATE
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ''


def save_if_current(data, token, callback):
    """An old GUI/cache cannot write settings after a newer declaration applied."""
    with file_transaction(Path(data) / STATE):
        if token is not None and revision_token(data) != token:
            return False
        return callback() is not False


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def config(raw):
    result = configparser.ConfigParser(interpolation=None)
    result.read_string(raw.decode('utf-8-sig'))
    if not result.has_section('settings'):
        result.add_section('settings')
    return result


def config_keys(groups):
    return set().union(*(CONFIG_KEYS.get(group, set()) for group in groups))


def config_bytes(previous, seed, groups):
    result = config(previous or b'')
    for key in config_keys(groups):
        if key in seed['settings']:
            result['settings'][key] = seed['settings'][key]
        else:
            result['settings'].pop(key, None)
    stream = StringIO();result.write(stream)
    return stream.getvalue().encode('utf-8')


def seed_hash(relative, raw, groups):
    # Build-time defaults may contain other UI keys. Pin only declared keys.
    if relative == 'init/default_settings.ini':
        values = config(raw)['settings']
        raw = json_bytes({key: values.get(key) for key in sorted(config_keys(groups))})
    return hashlib.sha256(raw).hexdigest()


def validate_seed(name, raw):
    if name.endswith('.txt'):
        rows = [line for line in raw.decode('utf-8-sig').splitlines() if line.strip() and not line.lstrip().startswith('#')]
        minimum = 5 if 'area' in name else 8
        if not rows or any(len(line.split('|')) < minimum for line in rows):
            raise ValueError('불완전한 배포 기본설정: ' + name)
    elif name.endswith('.ini'):
        config(raw)
    elif name.endswith('.json'):
        value = json.loads(raw)
        expected = list if name.endswith('schedule_break_rules.json') else dict
        if not isinstance(value, expected):
            raise ValueError('잘못된 배포 설정 형식: ' + name)
        if name.endswith('boss_spectrum.json'):
            from web_boss_spectrum import validate_numbers, validate_themes
            if value.get('schema') == 2:
                validate_numbers(value.get('numbers'))
            elif value.get('schema') == 1:
                validate_themes(value.get('themes'))
            else:
                raise ValueError('잘못된 스펙트럼 형식')
        elif name.endswith('size_defaults.json'):
            from web_font_library import valid_size_defaults
            if value.get('schema') != 1:
                raise ValueError('잘못된 글꼴 기본 크기 형식')
            valid_size_defaults(value.get('sizes'))
        elif name.endswith('default_notice_settings.json'):
            from notice_module.payload.notice_defaults import validate_preferences
            validate_preferences(value)
        elif name.endswith('default_notice_module.json'):
            if (set(value)!={'schema','use_bundled_once'} or type(value.get('schema')) is not int
                    or value['schema']!=1 or value.get('use_bundled_once') is not True):
                raise ValueError('알리미 코드 배포설정 형식을 확인하세요.')


def read_sources(root, groups):
    names = {name for group in groups for name in FILES[group]}
    if config_keys(groups):
        names.add('init/default_settings.ini')
    result = {}
    for name in sorted(names):
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('배포 기본설정 파일 경로·크기를 확인하세요: ' + name)
        raw = path.read_bytes()
        validate_seed(name, raw)
        result[name] = raw
    return result


def read_declaration(root):
    root = Path(root)
    path = root / DECLARATION
    if not path.exists():
        return None
    if path.stat().st_size > 65536:
        raise ValueError('배포 기본설정 선언이 너무 큽니다.')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict) or value.get('schema') != 1 or type(value.get('enabled')) is not bool:
        raise ValueError('배포 기본설정 선언 형식을 확인하세요.')
    if not value['enabled']:
        return None
    groups = value.get('groups')
    if (not isinstance(groups, list) or not groups or any(type(g) is not str or g not in FILES for g in groups)
            or len(set(groups)) != len(groups) or type(value.get('sequence')) is not int or value['sequence'] <= 0
            or not isinstance(value.get('revision'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value['revision'])):
        raise ValueError('배포 기본설정의 대상·차수를 확인하세요.')
    sources = read_sources(root, groups)
    hashes = {name: seed_hash(name, raw, groups) for name, raw in sources.items()}
    if value.get('hashes') != hashes:
        raise ValueError('선언 후 기본설정이 변경됐습니다. 덮어쓰기 선언을 다시 만든 뒤 빌드하세요.')
    return value, sources


def declare(root, groups):
    """Freeze an explicit selection; caller decides whether to save it."""
    groups = sorted(set(groups))
    if not groups or any(group not in FILES for group in groups):
        raise ValueError('선언할 설정 항목을 선택하세요.')
    sources = read_sources(Path(root), groups)
    return dict(schema=1, enabled=True, revision=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8],
                sequence=time.time_ns(), groups=groups,
                hashes={name: seed_hash(name, raw, groups) for name, raw in sources.items()})


def apply_declared_defaults(resource, data, *, season='', server=''):
    declared = read_declaration(resource)
    if declared is None:
        return dict(applied=False, reason='not_declared')
    declaration, sources = declared
    data = Path(data).resolve()
    with file_transaction(data / STATE), ExitStack() as locks:
        state_path = data / STATE
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else dict(schema=1, scopes={})
        if not isinstance(state, dict) or state.get('schema') != 1 or not isinstance(state.get('scopes'), dict):
            raise ValueError('배포 기본설정 적용 기록을 확인하세요.')
        scopes = ['global']
        if season or server:
            if not re.fullmatch(r'season_(?:\d+|unset)', season) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', server):
                raise ValueError('배포 기본설정을 적용할 서버·시즌을 확인하세요.')
            scopes.append('server_profiles/' + season + '/' + server)
        writes, previous, applied = {}, {}, []

        def before(path):
            path = Path(path)
            if not path.resolve().is_relative_to(data):
                raise ValueError('기본설정 대상이 AppData 밖을 가리킵니다.')
            if path not in previous:
                previous[path] = path.read_bytes() if path.exists() else None
            return previous[path]

        seed = config(sources.get('init/default_settings.ini', b''))
        for scope in scopes:
            receipts = state['scopes'].setdefault(scope, {})
            if not isinstance(receipts, dict):
                raise ValueError('잘못된 배포 적용 기록입니다.')
            groups = []
            for group in declaration['groups']:
                if scope != 'global' and group not in PROFILE_GROUPS:
                    continue
                receipt = receipts.get(group, receipts.get('boss', {}) if group in {'boss_definition','areas'} else {})
                if not isinstance(receipt, dict) or type(receipt.get('sequence', 0)) is not int:
                    raise ValueError('잘못된 배포 적용 차수입니다.')
                related = ('boss', 'boss_definition', 'areas') if group == 'boss' else (group, 'boss') if group in {'boss_definition','areas'} else (group,)
                latest_global = max(state['scopes']['global'].get(key, {}).get('sequence', 0) for key in related)
                if latest_global > declaration['sequence']:
                    continue
                if receipt.get('sequence', 0) < declaration['sequence']:
                    groups.append(group)
            if not groups:
                continue
            if scope == 'global' and 'notice_module' in groups:
                # Serialize with the real updater, preserving verified packages,
                # preferences and history. Only next-session selection is reset.
                from ai_module_updater import AiModuleUpdater
                updater=AiModuleUpdater(data/'update_ai','0.0.0')
                locks.enter_context(updater._locked())
                target=data/'update_ai/state.json'
                before(target)
                update_state=updater.snapshot()
                update_state.update(active='',pending='',previous='')
                update_state.pop('runtime_trial',None)
                update_state.pop('runtime_loaded',None)
                update_state.setdefault('history',[]).append(dict(
                    date=datetime.now(timezone.utc).isoformat(timespec='seconds'),version='',
                    status='배포 알리미 적용 예약',detail='이번 배포본의 내장 코드 사용 · 설치 파일/개인 설정/송출 이력 보존'))
                writes[target]=json_bytes(update_state)
            base = data if scope == 'global' else data / scope
            for group in groups:
                for relative in FILES[group]:
                    if scope == 'global':
                        target = data / ('web_service/boss_spectrum.json' if group == 'spectrum' else relative)
                        if group == 'font_sizes':
                            target = data / 'web_service/font_settings.json'
                            settings = json.loads(before(target) or b'{"schema":1,"default":null,"disabled":[],"custom":[]}')
                            if settings.get('schema') != 1:
                                raise ValueError('웹 글꼴 설정 형식을 확인하세요.')
                            settings['font_size_defaults'] = json.loads(sources[relative])['sizes']
                            writes[target] = json_bytes(settings)
                        else:
                            before(target);writes[target] = sources[relative]
                    elif group == 'voice':
                        continue  # Global voice preferences must not reset on a server switch.
                    elif group == 'notice':
                        from notice_module.payload.notice_management import NoticeStore
                        from notice_module.payload.notice_defaults import PREFERENCE_KEYS
                        store = NoticeStore(data / 'notice_data', server)
                        target = store.path
                        raw = before(target)
                        state_value = json.loads(raw) if raw is not None else store._load()
                        if state_value.get('server_id') != server:
                            raise ValueError('알리미 서버 설정이 일치하지 않습니다.')
                        preferences = json.loads(sources[relative])
                        for key in PREFERENCE_KEYS:
                            if key in preferences:
                                state_value[key] = preferences[key]
                            else:
                                state_value.pop(key, None)
                        state_value['generation'] = state_value.get('generation', 0) + 1
                        state_value['tts_templates_revision'] = state_value.get('tts_templates_revision', 0) + 1
                        store._refresh_relative_speech(state_value)
                        writes[target] = json_bytes(state_value)
                    else:
                        target = base / ('schedule_alarm_settings.json' if group == 'alarm' else relative)
                        before(target);writes[target] = sources[relative]
                if scope == 'global' and group in {'alarm','voice','music'}:
                    target = data / {'alarm':'schedule_alarm_settings.json','voice':'edge_tts.ini','music':'background_music_settings.json'}[group]
                    before(target);writes[target] = sources[FILES[group][0]]
                receipts[group] = dict(sequence=declaration['sequence'], revision=declaration['revision'])
                applied.append(scope + ':' + group)
            if scope == 'global' and config_keys(groups):
                for target in (data / 'boss_timer_settings.ini', data / 'init/default_settings.ini'):
                    writes[target] = config_bytes(before(target), seed, groups)
            if scope != 'global':
                baseline = base / 'settings_rollback/baseline'
                manifest_path = baseline / 'manifest.json'
                manifest_raw = before(manifest_path)
                if manifest_raw is not None:
                    manifest = json.loads(manifest_raw)
                    if not isinstance(manifest, dict) or not isinstance(manifest.get('groups'), dict):
                        raise ValueError('롤백 기준점 형식을 확인하세요.')
                    for group in groups:
                        rollback_group = ROLLBACK_GROUPS.get(group, group)
                        entries = manifest['groups'].get(rollback_group, [])
                        for relative in FILES[group]:
                            snapshot = {'alarm':'schedule_alarm_settings.json','voice':'global/edge_tts.ini','notice':'notice_settings.json'}.get(group, relative)
                            target = baseline / snapshot
                            before(target);writes[target] = sources[relative]
                            entries = [entry for entry in entries if entry.get('snapshot') != snapshot]
                            source = (data / 'edge_tts.ini' if group == 'voice' else
                                      data / 'notice_data' / (hashlib.sha256(server.encode('utf-8')).hexdigest()+'.json') if group == 'notice' else base / snapshot)
                            entries.append(dict(source=str(source), snapshot=snapshot, exists=True))
                        manifest['groups'][rollback_group] = entries
                    manifest['distribution_defaults_revision'] = declaration['revision']
                    writes[manifest_path] = json_bytes(manifest)
                    target = baseline / 'boss_timer_settings.ini'
                    writes[target] = config_bytes(before(target), seed, groups)
        if not writes:
            return dict(applied=False, reason='already_applied_or_older')
        before(state_path)
        writes[state_path] = json_bytes(state)
        backup = data / 'distribution_defaults/backups' / (declaration['revision'] + '_' + uuid.uuid4().hex[:8])
        inventory = {}
        for target, raw in previous.items():
            relative = target.relative_to(data).as_posix()
            inventory[relative] = raw is not None
            if raw is not None:
                atomic_write(backup / relative, raw)
        atomic_write(backup / 'backup_manifest.json', json_bytes(dict(revision=declaration['revision'], files=inventory)))
        if any((p.read_bytes() if p.exists() else None) != raw for p, raw in previous.items()):
            raise RuntimeError('백업 중 설정이 변경되어 덮어쓰기를 중단했습니다.')
        committed = []
        try:
            for target, raw in writes.items():
                atomic_write(target, raw);committed.append(target)
            if any(path.read_bytes() != raw for path, raw in writes.items()):
                raise RuntimeError('배포 기본설정 저장 검증에 실패했습니다.')
        except Exception:
            for target in reversed(committed):
                if previous[target] is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write(target, previous[target])
            raise
        return dict(applied=True, revision=declaration['revision'], targets=applied, backup=str(backup))

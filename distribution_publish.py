"""Prepare only explicitly checked public settings for a later manual build."""
import configparser
from datetime import datetime
import json
from pathlib import Path
import uuid

from distribution_catalog import OPTION_IDS, file_selection
from distribution_settings_keys import GENERAL_KEYS
from distribution_defaults import (DECLARATION, FILES, config, config_bytes, config_keys,
                                   declare, json_bytes, validate_seed)
from runtime_storage import atomic_write
from web_service_common import file_transaction


def selection_at(project):
    path = Path(project) / DECLARATION
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    return file_selection(data.get('groups', [])) if data.get('enabled') else set()


def public_payload(group, path, raw, *, runtime=False):
    if group == 'notice_module':
        # This is a deployment policy, never executable code from AppData.
        return json_bytes(dict(schema=1,use_bundled_once=True))
    if group == 'spectrum':
        from web_boss_spectrum import read_numbers
        from fixed_boss_aliases import migrate_spectrum_numbers
        return json_bytes(dict(schema=2, target='background', numbers=migrate_spectrum_numbers(read_numbers(path))))
    if group == 'font_sizes':
        from web_font_library import valid_size_defaults
        data = json.loads(raw)
        return json_bytes(dict(schema=1, sizes=valid_size_defaults(data.get('font_size_defaults', {}) if runtime else data.get('sizes', {}))))
    if group == 'voice':
        from io import StringIO
        source = configparser.ConfigParser(interpolation=None)
        source.read_string(raw.decode('utf-8-sig'))
        result = configparser.ConfigParser(interpolation=None)
        result['edge_tts'] = {key:source['edge_tts'][key] for key in ('enabled','voice','rate','volume','pitch') if key in source['edge_tts']}
        output=StringIO();result.write(output)
        return output.getvalue().encode('utf-8')
    if group == 'notice':
        from notice_module.payload.notice_defaults import PREFERENCE_KEYS, validate_preferences
        data=json.loads(raw)
        return json_bytes(validate_preferences(dict(schema=1, **{key:data[key] for key in PREFERENCE_KEYS if key in data})))
    if group == 'music':
        data=json.loads(raw)
        terms=data.get('search_terms')
        if not isinstance(terms,list) or any(not isinstance(term,str) for term in terms):
            raise ValueError('음악 검색어 설정을 확인하세요.')
        return json_bytes(dict(search_terms=terms))
    return raw


def publish_selection(project, selected, sources):
    project=Path(project).resolve();selected=set(selected)
    if selected - OPTION_IDS or not (project/'boss_timer_gui.spec').is_file():
        raise ValueError('boss_timer_gui.spec가 있는 빌드 원본 폴더와 설정 항목을 확인하세요.')
    with file_transaction(project/DECLARATION):
        writes, inputs = {}, {}
        def read(path):
            path=Path(path);raw=path.read_bytes();inputs[path]=raw;return raw
        if selected:
            for group in sorted(selected):
                if group == 'settings':
                    continue
                name=FILES[group][0]
                candidate=Path(sources[group]) if sources.get(group) else project/name
                runtime=candidate.is_file() and candidate.resolve() != (project/name).resolve()
                path=candidate if candidate.is_file() else project/name
                raw=public_payload(group,path,read(path),runtime=runtime)
                validate_seed(name,raw);writes[project/name]=raw
            if config_keys(selected):
                target=project/'init/default_settings.ini'
                source=Path(sources.get('settings',target))
                seed=config(read(source if source.is_file() else target))
                if 'settings' in selected and seed['settings'].get('background_path'):
                    value=seed['settings']['background_path']
                    try:seed['settings']['background_path']=Path(value).resolve().relative_to(project).as_posix()
                    except ValueError:pass
                previous=read(target) if target.exists() else None
                merged=config(config_bytes(previous,seed,selected))
                public=config(b'')
                public['settings']={key:merged['settings'][key] for key in GENERAL_KEYS if key in merged['settings']}
                from io import StringIO
                output=StringIO();public.write(output)
                writes[target]=output.getvalue().encode('utf-8')
        before={path:path.read_bytes() if path.exists() else None for path in [*writes,project/DECLARATION]}
        backup=project/'settings_seed_backups'/('distribution_ui_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
        for path,raw in before.items():
            if not path.resolve().is_relative_to(project):
                raise ValueError('배포 원본 폴더 밖에는 저장하지 않습니다.')
            if raw is not None:
                atomic_write(backup/path.relative_to(project),raw)
        atomic_write(backup/'backup_manifest.json',json_bytes({str(path.relative_to(project)):raw is not None for path,raw in before.items()}))
        if any(path.read_bytes()!=raw for path,raw in inputs.items()):
            raise RuntimeError('설정이 저장 중 변경되었습니다. 잠시 후 다시 저장하세요.')
        if any((path.read_bytes() if path.exists() else None)!=raw for path,raw in before.items()):
            raise RuntimeError('배포 원본이 다른 작업에서 변경되었습니다. 다시 저장하세요.')
        committed=[]
        try:
            for path,raw in writes.items():
                atomic_write(path,raw);committed.append(path)
            value=declare(project,selected) if selected else dict(schema=1,enabled=False)
            target=project/DECLARATION
            atomic_write(target,json_bytes(value));committed.append(target)
        except Exception:
            for path in reversed(committed):
                if before[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path,before[path])
            raise
        return dict(declaration=value, backup=str(backup), count=len(selected))

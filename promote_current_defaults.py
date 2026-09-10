"""Explicit, manual promotion of current voice/boss settings. Never builds.

Backs up the old baseline and project seeds before updating selected groups.
Schedules, Discord credentials, and break-time baseline are not changed.
"""
from __future__ import annotations
import configparser
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import uuid


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_bytes(data)
    os.replace(temporary,path)


def config_bytes(config):
    from io import StringIO
    output=StringIO()
    config.write(output)
    return output.getvalue().encode('utf-8')


def selected_setting(key):
    return (key.startswith(('schedule_alarm_','schedule_second_precision_','schedule_boss_metric_'))
            or key in {'fixed_boss_color_data_enabled','fixed_boss_last_text_color','fixed_boss_last_bg_color',
                       'schedule_share_use_fixed_boss_colors','schedule_color_data_enabled',
                       'schedule_share_use_boss_colors','schedule_invasion_last_text_color','schedule_invasion_last_bg_color'})


def main():
    root=Path(__file__).resolve().parent
    appdata=Path(os.environ['APPDATA'])/'BossTimer'
    active=json.loads((appdata/'active_server_profile.json').read_text(encoding='utf-8-sig'))
    season,server=str(active['season_key']),str(active['server_id'])
    if any(not value or value in {'.','..'} or '/' in value or '\\' in value for value in (season,server)):
        raise ValueError('Invalid active profile')
    profile=appdata/'server_profiles'/season/server
    baseline=profile/'settings_rollback'/'baseline'
    manifest=json.loads((baseline/'manifest.json').read_text(encoding='utf-8-sig'))
    alarm_path=profile/'schedule_alarm_settings.json'
    alarm=json.loads(alarm_path.read_text(encoding='utf-8-sig'))
    # Preserve the user's explicit countdown skip choice. Verify the three
    # selected sound files exist and have portable project-relative paths.
    for key in ('general','fixed','rapid_chain'):
        raw=str(alarm.get('chime_settings',{}).get(key) or '')
        if not raw:
            raise ValueError(f'Current chime is empty: {key}')
        sound=Path(raw) if Path(raw).is_absolute() else root/raw
        sound=sound.resolve()
        relative=sound.relative_to(root)
        if not sound.is_file() or relative.parts[0]!='wave':
            raise ValueError(f'Chime must exist in the project wave folder: {key}')
        alarm['chime_settings'][key]=relative.as_posix()
    alarm_bytes=(json.dumps(alarm,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    edge=appdata/'edge_tts.ini'
    selected={
        'alarm':[(alarm_path,'schedule_alarm_settings.json'),(edge,'global/edge_tts.ini')],
        'boss':[(profile/'init'/name,'init/'+name) for name in ('schedule_boss_definitions.txt','schedule_area_definitions.txt')],
        'fixed':[(profile/'init'/'schedule_fixed_bosses.txt','init/schedule_fixed_bosses.txt')],
        'metrics':[(profile/'init'/'schedule_boss_metrics.json','init/schedule_boss_metrics.json')],
    }
    # Read and validate every source before any baseline/seed is changed.
    writes={}
    seeds={root/'init/default_schedule_alarm_settings.json':alarm_bytes}
    for group,entries in selected.items():
        for source,relative in entries:
            data=source.read_bytes()
            writes[baseline/relative]=data
            if relative.startswith('init/'):
                seeds[root/relative]=data
    voice=configparser.ConfigParser()
    voice.read(edge,encoding='utf-8-sig')
    voice_seed=configparser.ConfigParser()
    voice_seed['edge_tts']={key:voice['edge_tts'][key] for key in ('enabled','voice','rate','volume','pitch') if key in voice['edge_tts']}
    seeds[root/'init/default_edge_tts.ini']=config_bytes(voice_seed)
    current=configparser.ConfigParser(); current.read(root/'boss_timer_settings.ini',encoding='utf-8-sig')
    saved=configparser.ConfigParser(); saved.read(baseline/'boss_timer_settings.ini',encoding='utf-8-sig')
    seed=configparser.ConfigParser(); seed.read(root/'init/default_settings.ini',encoding='utf-8-sig')
    for target in (saved,seed):
        if not target.has_section('settings'): target.add_section('settings')
        for key in set(target['settings'])|set(current['settings']):
            if selected_setting(key):
                if key in current['settings']: target['settings'][key]=current['settings'][key]
                else: target['settings'].pop(key,None)
    writes[baseline/'boss_timer_settings.ini']=config_bytes(saved)
    seeds[root/'init/default_settings.ini']=config_bytes(seed)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    previous=profile/'settings_rollback'/('before_baseline_refresh_'+stamp)
    seed_backup=root/'settings_seed_backups'/stamp
    shutil.copytree(baseline,previous)
    seed_backup.mkdir(parents=True,exist_ok=False)
    for target in seeds:
        if target.exists(): shutil.copy2(target,seed_backup/target.name)
    # Re-check active profile before committing a snapshot to its baseline.
    if json.loads((appdata/'active_server_profile.json').read_text(encoding='utf-8-sig'))!=active:
        raise RuntimeError('Active profile changed; baseline not updated')
    for target,data in {**writes,**seeds}.items(): atomic_write(target,data)
    groups=manifest.setdefault('groups',{})
    for group,entries in selected.items():
        groups[group]=[dict(source=str(source),snapshot=relative.replace('/','\\'),exists=True) for source,relative in entries]
    manifest.update(refreshed_at=datetime.now().isoformat(),refreshed_groups=list(selected),
                    config_snapshot='boss_timer_settings.ini')
    atomic_write(baseline/'manifest.json',(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    for target,data in {**writes,**seeds}.items():
        if target.read_bytes()!=data: raise RuntimeError(f'Verification failed: {target}')
    print(json.dumps(dict(profile=str(profile),previous_baseline=str(previous),previous_defaults=str(seed_backup),
                          chimes=alarm['chime_settings'],updated_files=len(writes)+len(seeds)),ensure_ascii=False))


if __name__=='__main__':
    main()

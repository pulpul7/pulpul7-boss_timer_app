"""Manual repair of confirmed all-empty chimes; preview by default, no builds.

Close BossTimer first. Run with --apply only after checking the preview.
Repairs chimes only; schedules, bot credentials and other settings are untouched.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from runtime_storage import atomic_write

CHIME_KEYS = ('general', 'fixed', 'rapid_chain')


def plan_repair(data_root, resource_root):
    data_root, resource_root = Path(data_root).resolve(), Path(resource_root).resolve()
    selection_path = data_root / 'active_server_profile.json'
    selection_bytes = selection_path.read_bytes()
    selection = json.loads(selection_bytes)
    season, server = str(selection['season_key']), str(selection['server_id'])
    import re
    if not re.fullmatch(r'season_\d+', season) or not re.fullmatch(r'[\w-]+', server, re.ASCII):
        raise ValueError('활성 시즌/서버 경로를 확인할 수 없습니다.')
    profile = data_root / 'server_profiles' / season / server
    defaults = json.loads((resource_root/'init/default_schedule_alarm_settings.json').read_text(encoding='utf-8-sig'))
    sounds = {key: defaults['chime_settings'][key] for key in CHIME_KEYS}
    for sound in sounds.values():
        path = (resource_root / sound).resolve()
        if not sound or not path.is_relative_to(resource_root / 'wave') or not path.is_file():
            raise ValueError('기본 차임벨 음성 파일을 확인할 수 없습니다.')
    targets = [profile/'schedule_alarm_settings.json',
               profile/'settings_rollback/baseline/schedule_alarm_settings.json',
               data_root/'init/default_schedule_alarm_settings.json',
               data_root/'schedule_alarm_settings.json']
    changes = []
    for path in targets:
        if not path.resolve().is_relative_to(data_root) or path.is_symlink():
            raise ValueError('사용자 데이터 밖의 경로입니다.')
        if not path.is_file():
            continue
        original = path.read_bytes()
        payload = json.loads(original)
        chimes = payload.get('chime_settings', {})
        if not isinstance(chimes, dict) or any(str(chimes.get(key) or '').strip() for key in CHIME_KEYS):
            continue  # Never replace even one existing user-selected sound.
        payload['chime_settings'] = dict(chimes, **sounds)
        changes.append((path, original, (json.dumps(payload, ensure_ascii=False, indent=2)+'\n').encode('utf-8')))
    return selection_bytes, profile, changes


def apply_repair(data_root, resource_root):
    data_root = Path(data_root).resolve()
    selection, profile, changes = plan_repair(data_root, resource_root)
    if not changes:
        return None, []
    backup = profile/'settings_rollback'/('before_chime_repair_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    for path, original, _ in changes:
        atomic_write(backup/path.relative_to(data_root), original)
    if (data_root/'active_server_profile.json').read_bytes() != selection:
        raise RuntimeError('활성 시즌이 변경되어 복구를 중단했습니다.')
    for path, original, _ in changes:
        if path.read_bytes() != original:
            raise RuntimeError('설정이 변경되어 복구를 중단했습니다. 프로그램을 종료하세요.')
    for path, _, replacement in changes:
        atomic_write(path, replacement)
        if path.read_bytes() != replacement:
            raise RuntimeError('차임벨 저장 검증에 실패했습니다.')
    return backup, [str(path.relative_to(data_root)) for path, _, _ in changes]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    data = Path(os.environ['APPDATA'])/'BossTimer'
    if args.apply:
        backup, changed = apply_repair(data, root)
        print(json.dumps(dict(backup=str(backup) if backup else None, repaired=changed), ensure_ascii=False))
    else:
        _, profile, changes = plan_repair(data, root)
        print(json.dumps(dict(profile=str(profile), targets=[str(p.relative_to(data)) for p, _, _ in changes]), ensure_ascii=False))

"""Developer-owned web boss color numbers, isolated from schedule data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

from web_font_library import font_developer_allowed
from web_service_common import file_transaction, read_json, write_json
from fixed_boss_aliases import migrate_spectrum_numbers

THEMES = {'sakura', 'spring', 'winter', 'rain', 'sky', 'wood', 'green', 'blue', 'magenta', 'ivory', 'canary', 'forsythia', 'purple', 'gray'}
MAX_BOSSES = 4000
MAX_COLOR = 30
MAX_BYTES = 1024 * 1024


def validate_themes(value):
    if not isinstance(value, dict) or set(value) - (THEMES | {'default'}):
        raise ValueError('invalid boss spectrum themes')
    result = {}
    for theme, mapping in value.items():
        if not isinstance(mapping, dict) or len(mapping) > MAX_BOSSES:
            raise ValueError('invalid boss spectrum mapping')
        if any(not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{24}', key)
               or type(number) is not int or not 1 <= number <= MAX_COLOR for key, number in mapping.items()):
            raise ValueError('invalid boss spectrum number')
        result[theme] = dict(mapping)
    return result


def validate_numbers(value):
    return validate_themes({'spring': value})['spring']


def read_numbers(path):
    path = Path(path)
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('boss spectrum file too large')
        data = read_json(path)
    except FileNotFoundError:
        return {}
    if data.get('schema') == 2:
        return validate_numbers(data.get('numbers'))
    if data.get('schema') != 1:
        raise ValueError('invalid boss spectrum schema')
    # Preserve previous light-theme choices as one shared number per boss.
    # Later entries win if an old file assigned different numbers. Dark is ignored.
    numbers = {}
    for theme, mapping in validate_themes(data.get('themes')).items():
        if theme in THEMES:
            numbers.update(mapping)
    return validate_numbers(numbers)


def bundle_path():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'web_assets' / 'boss_spectrum.json'


class BossSpectrumStore:
    def __init__(self, root, bundled=None):
        self.path = Path(root) / 'boss_spectrum.json'
        self.bundled = Path(bundled) if bundled is not None else bundle_path()

    def numbers(self):
        # Runtime data fully overrides the bundle, including explicit removals.
        return migrate_spectrum_numbers(read_numbers(self.path if self.path.exists() else self.bundled))

    def public(self, allowed=False):
        numbers = self.numbers()
        version = hashlib.sha256(json.dumps(numbers, sort_keys=True).encode()).hexdigest()[:16]
        return dict(schema=2, target='background', numbers=numbers, version=version, can_save=bool(allowed))

    def save(self, theme, bosses, number, catalog):
        from program_update_guard import active
        if active(self.path.parent.parent):
            raise ValueError('전체 업데이트 중에는 배포 색상 저장을 잠시 중지합니다.')
        if (not isinstance(theme, str) or theme not in THEMES or type(number) is not int
                or not 0 <= number <= MAX_COLOR or not isinstance(bosses, list)
                or not 1 <= len(bosses) <= MAX_BOSSES):
            raise ValueError('테마·스펙트럼 번호와 선택한 보스를 확인해주세요.')
        if any(not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{24}', key) for key in bosses):
            raise ValueError('보스 목록을 다시 확인해주세요.')
        known = {row.get('id') for row in catalog if isinstance(row, dict)}
        if set(bosses) - known:
            raise ValueError('보스 목록이 변경되었습니다. 새로고침 후 다시 선택해주세요.')
        with file_transaction(self.path):
            mapping = self.numbers()
            for key in bosses:
                if number:
                    mapping[key] = number
                else:
                    mapping.pop(key, None)
            validate_numbers(mapping)
            write_json(self.path, dict(schema=2, target='background', numbers=mapping))
        return self.public(True)


def public_spectrum(access, context, ip):
    return BossSpectrumStore(access.root).public(font_developer_allowed(access, context, ip))


def save_spectrum(access, context, ip, payload, catalog):
    if not font_developer_allowed(access, context, ip):
        raise PermissionError('개발자·마스터 관리자만 보스 스펙트럼을 저장할 수 있습니다.')
    result = BossSpectrumStore(access.root).save(payload.get('theme'), payload.get('bosses'),
                                                payload.get('number'), catalog)
    return dict(result, ok=True, message='보스 박스 배경색을 저장했습니다. 다음 배포 빌드에 포함됩니다.')

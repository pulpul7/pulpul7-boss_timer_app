"""Portable preferences only: never export notices, receipts or server identity."""
from copy import deepcopy
import json
from pathlib import Path

PREFERENCE_KEYS = ("settings", "tts_templates", "tts_template_enabled",
                   "tts_date_syntax", "participation_template_merged")


def validate_preferences(payload):
    from .notice_management import validate_settings
    from .notice_templates import TEMPLATES, validate_template
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ValueError("알리미 기본설정 형식이 잘못되었습니다.")
    if set(payload) - {*PREFERENCE_KEYS, "schema"}:
        raise ValueError("알리미 기본설정에 설정 이외의 정보가 있습니다.")
    validate_settings(payload["settings"])
    for key, text in payload.get("tts_templates", {}).items():
        if key not in TEMPLATES:
            raise ValueError(f"지원하지 않는 안내 유형: {key}")
        validate_template(key, text)
    if any(key not in TEMPLATES or type(value) is not bool
           for key, value in payload.get("tts_template_enabled", {}).items()):
        raise ValueError("유형별 사용 설정이 잘못되었습니다.")
    return deepcopy(payload)


def export_preferences(state):
    return validate_preferences(dict(schema=1, **{key: deepcopy(state[key]) for key in PREFERENCE_KEYS if key in state}))


def bundled_preferences():
    path = Path(__file__).with_name("notice_defaults.json")
    return validate_preferences(json.loads(path.read_text(encoding="utf-8-sig"))) if path.is_file() else None

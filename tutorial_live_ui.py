"""Locations and explicitly allowed navigation in the existing BossTimer UI.

No imitation controls, sample schedules, settings writes or authority operations.
"""
from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk


@dataclass(frozen=True)
class View:
    attribute: str = ""
    title: str = ""
    opener: str = ""
    parent: str = "main"
    modal: bool = False


VIEWS = {
    "main": View(),
    "input": View("schedule_input_window", "스케쥴 입력", "_ensure_schedule_input_window"),
    "capture": View("schedule_input_ocr_addon_window", "", "_ensure_schedule_input_ocr_addon_window", "input"),
    "capture_settings": View("schedule_input_ocr_addon_settings_window", "촬영 설정", "_open_schedule_input_ocr_addon_restore_delay_dialog", "capture", True),
    "boss": View("schedule_boss_config_window", "보스 설정", "_ensure_schedule_boss_config_window"),
    "fixed": View("fixed_boss_window", "고정보스 등록", "_ensure_fixed_boss_window"),
    "alarm": View("schedule_alarm_window", "알람 설정", "_ensure_schedule_alarm_window"),
    "voice": View("schedule_alarm_voice_rule_window", "음성 설정", "_open_schedule_alarm_voice_rule_window", "alarm"),
    "edge": View("", "edge-tts 음성 설정", "_open_edge_tts_settings_window", "alarm"),
    "chime": View("schedule_alarm_chime_window", "차임벨 설정", "_open_schedule_alarm_chime_settings_window", "alarm"),
    "discord": View("discord_bot_settings_window", "디스코드 봇 설정", "open_discord_bot_settings_window"),
    "share": View("", "스케쥴 복사", "_show_schedule_share_period_dialog", modal=True),
    "history": View("", "스케쥴 불러오기", "_open_schedule_delete_history_dialog", modal=True),
    "season": View("", "시즌 시작 / 이어하기", "_show_season_setup_dialog", modal=True),
    "rollback": View("", "설정 롤백", "_open_settings_rollback_dialog", "season", True),
    "notice": View("", "알리미 관리 ·", "open_notice_management"),
    "notice_templates": View("", "안내 문구 기본 설정 ·", parent="notice", modal=True),
    "notice_edit": View("", "TTS 문장 보기 / 편집", parent="notice"),
    "break": View("schedule_break_window", "휴식시간 설정", "_ensure_schedule_break_window"),
    "metrics": View("schedule_boss_metrics_window", "소요시간/점수 설정", "_ensure_schedule_boss_metrics_window"),
    "log": View("discord_schedule_monitor_window", "", "_open_discord_schedule_monitor_window"),
    "update": View("", "업데이트 확인", "open_ai_update_center"),
    "server": View("", "서버 추가/삭제", "_open_github_server_manage_dialog", modal=True),
    "stats": View("log_stats_window", "통계", "_ensure_log_stats_window"),
    "record": View("record_book_window", "보스 기록표", "_ensure_record_book_window"),
    "log_records": View("log_panel", "기록 로그", "_ensure_log_panel"),
    "delete": View("", "스케쥴 전체삭제", "_open_schedule_delete_dialog", modal=True),
    "regular": View("", "정기점검", "_open_regular_maintenance_dialog", modal=True),
    "temporary": View("", "임시점검 입력", "_open_temporary_maintenance_dialog", modal=True),
    "music": View("", "배경음악 검색어", "_open_background_music_search_dialog", modal=True),
}

# Only window navigation is allowed. Upload, synchronization, TXT, row edits,
# saves, resets, music playback and Discord connection are deliberately absent.
MAIN_OPENERS = {
    "스케쥴 입력": "input", "추가": "input", "보스 설정": "boss",
    "고정 보스": "fixed", "알람 설정": "alarm", "설정": "discord",
    "스케쥴 복사": "share", "복구": "history", "새 시즌 시작": "season",
    "알리미 관리": "notice", "소요시간/점수": "metrics", "휴식시간 설정": "break",
    "보탐 로그": "log", "업데이트 확인": "update", "통계": "stats",
    "서버 추가/삭제": "server", "아군/적군 막타": "record",
    "삭제": "delete", "정기점검": "regular", "임시점검": "temporary",
    "음악 설정": "music",
}

ATTRIBUTES = {
    ("input", "OCR_1"): "schedule_input_ocr2_button",
    ("input", "OCR_2"): "schedule_input_image_button",
    ("input", "스샷찍기"): "schedule_input_ocr_addon_button",
    ("input", "적용"): "schedule_input_apply_button",
    ("input", "입력 취소"): "schedule_input_undo_button",
    ("input", "수정 취소"): "schedule_input_edit_undo_button",
    ("input", "이전 스샷 복구"): "schedule_input_restore_button",
    # The visible OCR_1 button uses the legacy "ocr2" mode and renders on
    # the left. schedule_input_ocr1_text is the right-hand OCR_2 result pane.
    ("input", "result"): "schedule_input_text",
    ("input", "capture"): "schedule_input_ocr_queue_canvas",
    ("input", "24시간이내"): "schedule_input_within_day_check",
    ("input", "초확정"): "schedule_input_second_confirmed_check",
    ("input", "시간순 정렬"): "schedule_input_sort_check",
    ("capture", "스샷찍기 (F2)"): "schedule_input_ocr_addon_capture_button",
    ("capture", "초단위 찍기"): "schedule_input_precision_button",
    ("capture", "설정"): "schedule_input_ocr_addon_settings_button",
    ("boss", "보스명"): "schedule_boss_name_entry",
    ("boss", "젠주기"): "schedule_boss_respawn_entry",
    ("fixed", "시간"): "fixed_boss_time_entry",
    ("fixed", "days"): "fixed_boss_window",
    ("voice", "files"): "schedule_alarm_voice_rule_tree",
    ("voice", "스케쥴 복원"): "schedule_alarm_voice_test_restore_button",
}

LABELS = {
    ("discord", "Bot Token"): ("봇 토큰",),
    ("capture", "설정"): ("⚙", "설정"),
    ("capture_settings", "초당 분석 횟수"): ("연속촬영 · 1초당 감지 횟수",),
    ("share", "이미지 복사"): ("복사", "확인", "이미지 복사"),
    ("share", "시작 / 종료"): ("시작", "시작 시간", "종료", "종료 시간"),
    ("share", "보스색"): ("보스색", "보스색 사용"),
    ("boss", "새 보스"): ("새 보스", "보스명"),
}


def available(widget):
    try:
        return widget is not None and bool(widget.winfo_exists())
    except tk.TclError:
        return False


def walk(widget):
    yield widget
    for child in tuple(widget.children.values()):
        yield from walk(child)


def route(view, target):
    """Concept chapters point to actual controls, never synthetic diagrams."""
    if view == "table":
        return "main", {
            "rows": "@schedule_tree", "컷": "@schedule_tree",
            "취소": "@schedule_tree", "컷시간": "@schedule_tree",
            "Delete": "행삭제", "← / →": "@schedule_tree",
            "오른쪽 클릭": "@schedule_tree",
        }.get(target, target)
    if view == "precision":
        if target == "초당 분석 횟수":
            return "capture_settings", target
        if target == "confirmed":
            return "input", "result"
        return "capture", "초단위 찍기" if target == "watch" else target
    if view == "automatic":
        return "main", {
            "chain": "@schedule_tree", "fixed_auto": "@schedule_tree",
            "maintenance": "정기점검", "notices": "알리미 관리",
            "priority": "알리미 관리", "appdata": "새 시즌 시작", "updates": "업데이트 확인",
        }[target]
    if view == "recovery":
        if target in {"입력 취소", "수정 취소", "이전 스샷 복구"}:
            return "input", target
        return {
            "history": ("history", "@rows"), "행복구": ("main", "행복구"),
            "보탐 로그": ("log", "@window"), "설정 복구": ("rollback", "@window"),
            "시즌 이어가기": ("season", "이어하기"), "업데이트 확인": ("update", "@window"),
            "음성 테스트 스케쥴 복원": ("voice", "스케쥴 복원"),
            "로그 폐기 복원": ("log_records", "폐기 복원"),
            "자동 문장으로 복원": ("notice_edit", target),
            "선택 문장 기본값 복원": ("notice_templates", target),
        }[target]
    if view == "discord" and target not in {"@window", "Bot Token", "Application ID", "서버 ID", "음성채널 ID", "안내채팅 ID"}:
        return "main", "디스코드봇 실행"
    if view == "voice":
        if target in {"edge-tts 설정", "모듈 설치"}:
            return "edge", "@window" if target == "edge-tts 설정" else target
        if target == "초읽기 사용":
            return "main", target
        if target == "차임벨 설정":
            return "chime", "@window"
        if target == "output":
            return "main", "알람 설정"
    if view == "notice":
        if target in {"자동 문장으로 복원"}:
            return "notice_edit", target
        if target in {"선택 문장 기본값 복원"}:
            return "notice_templates", target
        if target in {"notice_rows", "문구 수정", "미리듣기"}:
            return "notice", "@rows"
    if view == "share" and target == "paste":
        return "main", "스케쥴 복사"
    return view, target


def find_window(app, view, windows):
    spec = VIEWS[view]
    if view == "main":
        return app.schedule_window
    widget = getattr(app, spec.attribute, None) if spec.attribute else None
    if available(widget):
        return widget
    if spec.title:
        matches = [w for w in windows if available(w) and w.title().startswith(spec.title)]
        if matches:
            return matches[-1]
    return None


def find_target(app, owner, view, key):
    if key == "@window":
        return owner
    attr = ATTRIBUTES.get((view, key))
    widget = getattr(app, attr, None) if attr else None
    if available(widget) and widget.winfo_toplevel() is owner:
        return widget
    candidates = [w for w in walk(owner) if w.winfo_toplevel() is owner and w.winfo_viewable()]
    if key == "@rows":
        return next((w for w in candidates if isinstance(w, (ttk.Treeview, tk.Listbox))), None)
    names = LABELS.get((view, key), (key,))
    for name in names:
        for w in candidates:
            try:
                text = str(w.cget("text")).strip()
            except tk.TclError:
                continue
            if text.rstrip(":：") == name or text.replace(" ", "") == name.replace(" ", ""):
                if isinstance(w, (tk.Label, ttk.Label)):
                    # A label has no value. Highlight its actual adjacent field.
                    fields = [c for c in candidates if c.master is w.master and isinstance(c, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Spinbox))
                              and abs(c.winfo_rooty() - w.winfo_rooty()) < 20 and c.winfo_rootx() >= w.winfo_rootx()]
                    if fields:
                        return min(fields, key=lambda c: abs(c.winfo_rootx() - w.winfo_rootx()))
                return w
    return None

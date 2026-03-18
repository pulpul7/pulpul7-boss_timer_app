import configparser
import ctypes
import math
import os
import re
import sys
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog
from tkinter import font as tkfont


WINDOW_WIDTH = 470
WINDOW_HEIGHT = 450
ALERT_AREA_X = 29
ALERT_AREA_Y = 294
ALERT_TAG = "alert_overlay"
GRAPH_AREA_X = 29
GRAPH_AREA_Y = 330
GRAPH_TAG = "graph_overlay"
APP_VERSION = "v 2.0.0.Beta"
LAST_UPDATED = "2026-03-17"
AUTHOR_NAME = "나츠"
DEFAULT_BG_KEY = "default"
ALT_BG_KEY = "wallpaper"
JANG_WONYOUNG_BG_KEY = "jang_wonyoung"
BUILTIN_BACKGROUNDS = {
    DEFAULT_BG_KEY: ("assets", "기본배경.png"),
    ALT_BG_KEY: ("assets", "벽지.png"),
    JANG_WONYOUNG_BG_KEY: ("assets", "장원영.png"),
}
BUTTON_ICON_FILES = {
    "play": {
        "normal": ("icons", "재생버튼.png"),
        "pressed": ("icons", "재생버튼누름.png"),
    },
    "pause": {
        "normal": ("icons", "정지버튼.png"),
        "pressed": ("icons", "정지버튼누름.png"),
    },
    "reset": {
        "normal": ("icons", "초기화버튼.png"),
        "pressed": ("icons", "초기화버튼누름.png"),
    },
    "settings": {
        "normal": ("icons", "환경설정.png"),
        "pressed": ("icons", "환경설정누름.png"),
    },
}
PROGRESS_BAR_FILES = {
    "empty": ("icons", "빈막대.png"),
    "full": ("icons", "가득찬막대.png"),
}
BUTTON_ICON_MAX_SIZE = {
    "play": 54,
    "pause": 54,
    "reset": 54,
    "settings": 35,
}
PROGRESS_BAR_CROP = (28, 320, 1508, 438)
PROGRESS_BAR_SCALE = 4
PREFERRED_FONT_FAMILIES = [
    "Arial Black",
    "Malgun Gothic",
    "Segoe UI",
    "Arial",
    "Tahoma",
]
DEFAULT_TK_SCALING = 96 / 72
ELAPSED_BRUSH_TAG = "elapsed_brush"
TOTAL_LABEL_TAG = "total_time_label"
TOTAL_LABEL_HITBOX_TAG = "total_time_label_hitbox"
RECORD_LABEL_TAG = "record_time_label"
RECORD_LABEL_HITBOX_TAG = "record_time_label_hitbox"
ELAPSED_BRUSH_COLORS = {
    "배경 없음": "",
    "노랑": "#facc15",
    "주황": "#fb923c",
    "핑크": "#f9a8d4",
    "하늘": "#7dd3fc",
    "연두": "#bef264",
    "검은색": "#111827",
    "연빨강": "#fca5a5",
    "연보라": "#c4b5fd",
    "아이보리": "#f8f1df",
    "황토색": "#c2410c",
}
TOTAL_LABEL_BRUSH_NORMAL = "#e84141"
TOTAL_LABEL_BRUSH_HOVER = "#f59e0b"
TOTAL_LABEL_TEXT_NORMAL = "#ffffff"
TOTAL_LABEL_TEXT_HOVER = "#111827"
LOG_PANEL_WIDTH = 360
LOG_PANEL_HEIGHT = 450
ANALYSIS_WINDOW_WIDTH = 560
ANALYSIS_WINDOW_HEIGHT = 500
LOG_MAX_ENTRIES = 50
LOG_ENTRY_SEPARATOR = "\n\n" + ("=" * 37) + "\n\n"
LOG_VALIDATION_TOLERANCE_SECONDS = 1.0


def get_resource_root() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(get_app_root(), "boss_timer_settings.ini")
LOGS_DIR = os.path.join(get_app_root(), "logs")


def get_builtin_background_path(background_key: str) -> str:
    parts = BUILTIN_BACKGROUNDS.get(background_key, BUILTIN_BACKGROUNDS[DEFAULT_BG_KEY])
    return os.path.join(get_resource_root(), *parts)


def get_background_key_from_legacy_path(path: str) -> str | None:
    normalized_name = os.path.basename(path)
    for background_key, parts in BUILTIN_BACKGROUNDS.items():
        if normalized_name == parts[-1]:
            return background_key
    return None


def get_button_icon_path(icon_key: str, state: str = "normal") -> str:
    parts = BUTTON_ICON_FILES[icon_key][state]
    return os.path.join(get_resource_root(), *parts)


def configure_windows_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass


def activate_korean_keyboard() -> None:
    if sys.platform != "win32":
        return
    try:
        korean_layout = ctypes.windll.user32.LoadKeyboardLayoutW("00000412", 1)
        ctypes.windll.user32.ActivateKeyboardLayout(korean_layout, 0)
    except Exception:
        pass


def activate_korean_keyboard_for_widget(widget: tk.Widget | None) -> None:
    activate_korean_keyboard()
    if sys.platform != "win32" or widget is None:
        return
    try:
        korean_layout = ctypes.windll.user32.LoadKeyboardLayoutW("00000412", 1)
        hwnd = widget.winfo_id()
        ctypes.windll.user32.SendMessageW(hwnd, 0x0050, 1, korean_layout)
        ctypes.windll.user32.PostMessageW(hwnd, 0x0050, 1, korean_layout)
    except Exception:
        pass


def schedule_korean_keyboard_activation(widget: tk.Widget | None, delays: tuple[int, ...] = (10, 80, 180, 320)) -> None:
    if widget is None:
        return
    for delay in delays:
        try:
            widget.after(delay, lambda current_widget=widget: activate_korean_keyboard_for_widget(current_widget))
        except Exception:
            return
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def configure_tk_scaling(root: tk.Tk) -> None:
    try:
        root.tk.call("tk", "scaling", DEFAULT_TK_SCALING)
    except tk.TclError:
        pass


def format_seconds(seconds: float, show_centiseconds: bool = False) -> str:
    safe_seconds = max(0.0, seconds)
    whole_seconds = int(safe_seconds)
    minutes = whole_seconds // 60
    remaining_seconds = whole_seconds % 60
    if show_centiseconds:
        centiseconds = int((safe_seconds - whole_seconds) * 100)
        return f"{minutes:02d}:{remaining_seconds:02d}:{centiseconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


class BossTimerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("보스전 타이머")
        self.root.resizable(False, False)

        self.available_font_families = sorted(set(tkfont.families(self.root)))
        self.current_font_family = self._get_default_font_family()
        self.background_path = DEFAULT_BG_KEY
        self.background_alignment = "nw"
        self.show_alert_overlay = True
        self.show_alert_percent = True
        self.show_hodulgap_banner = True
        self.show_elapsed_brush = True
        self.elapsed_brush_color_name = "노랑"
        self.analysis_count_default = "20개"
        self.initial_elapsed_seconds = 0.0
        self.main_window_x = 100
        self.main_window_y = 100
        self.settings_window_x = 140
        self.settings_window_y = 140
        self._load_settings()
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{self.main_window_x}+{self.main_window_y}")

        self.font_family_var = tk.StringVar(value=self.current_font_family)
        self.settings_path_var = tk.StringVar(value=self.background_path)
        self.background_alignment_var = tk.StringVar(value=self.background_alignment)
        self.show_alert_overlay_var = tk.BooleanVar(value=self.show_alert_overlay)
        self.show_alert_percent_var = tk.BooleanVar(value=self.show_alert_percent)
        self.show_hodulgap_banner_var = tk.BooleanVar(value=self.show_hodulgap_banner)
        self.show_elapsed_brush_var = tk.BooleanVar(value=self.show_elapsed_brush)
        self.elapsed_brush_color_var = tk.StringVar(value=self.elapsed_brush_color_name)

        self.running = False
        self.base_elapsed_seconds = 0.0
        self.start_perf_time = 0.0
        self.update_after_id = None

        self.reached_70_calc_seconds = None
        self.reached_70_display_seconds = None
        self.current_percent = None
        self.pending_log_record = None
        self.discarded_log_records: list[dict] = []
        self.log_panel_open = False
        self.log_panel = None
        self.log_panel_toggle_button = None
        self.log_panel_x = None
        self.log_panel_y = None
        self.analysis_window_open = False
        self.analysis_window = None
        self.analysis_window_x = None
        self.analysis_window_y = None

        self.blink_90_active = False
        self.blink_90_end_time = 0.0
        self.blink_kill_active = False
        self.blink_kill_end_time = 0.0
        self.pause_blink_after_id = None
        self.pause_blink_on = False

        self.background_image = None
        self.button_images: dict[str, tk.PhotoImage] = {}
        self.canvas_icon_positions: dict[int, tuple[int, int]] = {}
        self.canvas_icon_keys: dict[int, str] = {}
        self.canvas_icon_hitboxes: dict[int, int] = {}
        self.canvas_icon_commands: dict[int, callable] = {}
        self.settings_notice_after_id = None
        self.settings_notice_end_time = 0.0
        self.tooltip_window = None

        self.title_font = tkfont.Font(family=self.current_font_family, size=40, weight="bold")
        self.header_font = tkfont.Font(family=self.current_font_family, size=13, weight="bold")
        self.alert_font = tkfont.Font(family=self.current_font_family, size=11, weight="bold")
        self.label_font = tkfont.Font(family=self.current_font_family, size=12, weight="bold")
        self.value_font = tkfont.Font(family=self.current_font_family, size=15, weight="bold")
        self.remain_kill_font = tkfont.Font(family=self.current_font_family, size=26, weight="bold")
        self.button_font = tkfont.Font(family=self.current_font_family, size=10, weight="bold")
        self.banner_font = tkfont.Font(family=self.current_font_family, size=13, weight="bold")
        self.percent_font = tkfont.Font(family=self.current_font_family, size=9, weight="bold")
        self.burst_font = tkfont.Font(family=self.current_font_family, size=28, weight="bold")
        self.expected_value_font = tkfont.Font(family=self.current_font_family, size=24, weight="bold")
        self.signature_font = tkfont.Font(family=self.current_font_family, size=9, weight="bold")
        self.icon_font = tkfont.Font(family=self.current_font_family, size=12, weight="bold")

        self.elapsed_var = tk.StringVar(value=format_seconds(self.initial_elapsed_seconds, show_centiseconds=True))
        self.reached_70_var = tk.StringVar(value="00:00:00")
        self.remain_90_var = tk.StringVar(value="00:00:00")
        self.remain_kill_var = tk.StringVar(value="00:00:00")
        self.overrun_var = tk.StringVar(value="00:00:00")
        self.log_boss_name_var = tk.StringVar()
        self.log_status_var = tk.StringVar(value="로그 패널을 열어 기록 후보를 확인하세요.")
        self.log_view_mode_var = tk.StringVar(value="log")
        self.log_record_subview_var = tk.StringVar(value="candidate")
        self.analysis_count_var = tk.StringVar(value=self.analysis_count_default)

        self._build_ui()
        self._apply_background(self.background_path, update_setting_var=False)
        self._draw_progress_graph(None)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _is_builtin_background(self, source: str) -> bool:
        return source in BUILTIN_BACKGROUNDS

    def _get_default_font_family(self) -> str:
        for family in PREFERRED_FONT_FAMILIES:
            if family in self.available_font_families:
                return family
        return self.available_font_families[0]

    def _normalize_background_source(self, source: str | None) -> str:
        normalized_source = (source or "").strip()
        if not normalized_source:
            return DEFAULT_BG_KEY
        if self._is_builtin_background(normalized_source):
            return normalized_source
        legacy_key = get_background_key_from_legacy_path(normalized_source)
        if legacy_key is not None:
            return legacy_key
        return normalized_source

    def _resolve_background_path(self, source: str | None) -> tuple[str, str]:
        normalized_source = self._normalize_background_source(source)
        if self._is_builtin_background(normalized_source):
            return normalized_source, get_builtin_background_path(normalized_source)
        return normalized_source, normalized_source

    def _get_background_dialog_dir(self) -> str:
        assets_dir = os.path.join(get_app_root(), "assets")
        if os.path.isdir(assets_dir):
            return assets_dir
        return get_app_root()

    def _load_settings(self) -> None:
        config = configparser.ConfigParser()
        if not os.path.exists(CONFIG_PATH):
            self._save_settings()
            return
        config.read(CONFIG_PATH, encoding="utf-8")
        if "settings" not in config:
            return
        settings = config["settings"]
        saved_bg = settings.get("background_path", DEFAULT_BG_KEY)
        saved_font = settings.get("font_family", self._get_default_font_family())
        saved_alignment = settings.get("background_alignment", "center")
        saved_alert_overlay = settings.getboolean("show_alert_overlay", fallback=True)
        saved_alert_percent = settings.getboolean("show_alert_percent", fallback=True)
        saved_hodulgap_banner = settings.getboolean("show_hodulgap_banner", fallback=True)
        saved_elapsed_brush = settings.getboolean("show_elapsed_brush", fallback=True)
        saved_elapsed_brush_color = settings.get("elapsed_brush_color", "노랑")
        saved_analysis_count = settings.get("analysis_count", "20개")
        self.main_window_x = self._parse_int(settings.get("main_window_x"), self.main_window_x)
        self.main_window_y = self._parse_int(settings.get("main_window_y"), self.main_window_y)
        self.settings_window_x = self._parse_int(settings.get("settings_window_x"), self.settings_window_x)
        self.settings_window_y = self._parse_int(settings.get("settings_window_y"), self.settings_window_y)
        normalized_bg = self._normalize_background_source(saved_bg)
        _, resolved_bg_path = self._resolve_background_path(normalized_bg)
        if self._is_builtin_background(normalized_bg) or os.path.exists(resolved_bg_path):
            self.background_path = normalized_bg
        else:
            self.background_path = DEFAULT_BG_KEY
        if saved_font in self.available_font_families:
            self.current_font_family = saved_font
        if saved_alignment in {"center", "nw"}:
            self.background_alignment = saved_alignment
        self.show_alert_overlay = saved_alert_overlay
        self.show_alert_percent = saved_alert_percent
        self.show_hodulgap_banner = saved_hodulgap_banner
        if saved_elapsed_brush_color in ELAPSED_BRUSH_COLORS:
            self.elapsed_brush_color_name = saved_elapsed_brush_color
        self.show_elapsed_brush = saved_elapsed_brush and self.elapsed_brush_color_name != "배경 없음"
        if saved_analysis_count in {"5개", "10개", "20개", "30개", "50개"}:
            self.analysis_count_default = saved_analysis_count
        self.initial_elapsed_seconds = 0.0

    def _parse_int(self, value: str | None, fallback: int) -> int:
        try:
            return int(value) if value is not None else fallback
        except (TypeError, ValueError):
            return fallback

    def _bind_hover_button(self, button: tk.Widget, normal_bg: str, hover_bg: str, normal_fg: str | None = None, hover_fg: str | None = None) -> None:
        def on_enter(_event=None) -> None:
            button.config(bg=hover_bg, activebackground=hover_bg)
            if hover_fg is not None:
                button.config(fg=hover_fg, activeforeground=hover_fg)

        def on_leave(_event=None) -> None:
            button.config(bg=normal_bg, activebackground=normal_bg)
            if normal_fg is not None:
                button.config(fg=normal_fg, activeforeground=normal_fg)

        button.bind("<Enter>", on_enter)
        button.bind("<Leave>", on_leave)

    def _show_tooltip(self, text: str, *, widget: tk.Widget | None = None, canvas_x: int | None = None, canvas_y: int | None = None) -> None:
        self._hide_tooltip()
        if widget is not None:
            x = widget.winfo_rootx() + widget.winfo_width() + 8
            y = widget.winfo_rooty() + 6
        elif canvas_x is not None and canvas_y is not None:
            x = self.root.winfo_rootx() + canvas_x + 12
            y = self.root.winfo_rooty() + canvas_y + 12
        else:
            return
        self.tooltip_window = tk.Toplevel(self.root)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.attributes("-topmost", True)
        self.tooltip_window.geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tooltip_window,
            text=text,
            font=(self.current_font_family, 9, "bold"),
            bg="#ffffff",
            fg="#374151",
            relief="solid",
            bd=1,
            padx=6,
            pady=2,
        )
        label.pack()

    def _hide_tooltip(self) -> None:
        if self.tooltip_window is not None:
            try:
                self.tooltip_window.destroy()
            except tk.TclError:
                pass
            self.tooltip_window = None

    def _get_logs_dir(self) -> str:
        os.makedirs(LOGS_DIR, exist_ok=True)
        return LOGS_DIR

    def _sanitize_boss_name(self, raw_name: str) -> str:
        name = (raw_name or "").strip()
        if not name:
            return ""
        invalid_chars = '<>:"/\\|?*'
        sanitized = "".join("_" if char in invalid_chars else char for char in name)
        return sanitized.strip(". ")

    def _get_boss_log_path(self, boss_name: str) -> str:
        safe_name = self._sanitize_boss_name(boss_name) or "미지정보스"
        return os.path.join(self._get_logs_dir(), f"{safe_name}.txt")

    def _parse_log_time_value(self, raw_value: str) -> float | None:
        value = (raw_value or "").strip()
        if not value:
            return None
        parts = value.split(":")
        try:
            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = int(parts[1])
                if seconds >= 60:
                    return None
                return max(0.0, minutes * 60 + seconds)
            if len(parts) == 3:
                minutes = int(parts[0])
                seconds = int(parts[1])
                centiseconds = int(parts[2])
                if seconds >= 60 or centiseconds >= 100:
                    return None
                return max(0.0, minutes * 60 + seconds + centiseconds / 100)
        except ValueError:
            return None
        return None

    def _format_log_record_block(self, record: dict) -> str:
        lines = [
            f"[{record['recorded_at']}]",
            f"보스이름: {record['boss_name']}",
            f"컷 시간: {record['actual_cut_time']}",
            f"컷 예상: {record['expected_time']}",
            f"초과시간: {record['overrun_time']}",
            f"광타임: {record['gwang_time']}",
            f"검증상태: {record['validation_state']}",
        ]
        if record.get("validation_note"):
            lines.append(f"비고: {record['validation_note']}")
        return "\n".join(lines)

    def _read_log_blocks(self, boss_name: str) -> list[str]:
        log_path = self._get_boss_log_path(boss_name)
        if not os.path.exists(log_path):
            return []
        with open(log_path, "r", encoding="utf-8") as file:
            content = file.read().strip()
        if not content:
            return []
        parts = re.split(r"\n\s*\n=+\n\s*\n", content)
        return [block.strip() for block in parts if block.strip()]

    def _write_log_blocks(self, boss_name: str, blocks: list[str]) -> None:
        log_path = self._get_boss_log_path(boss_name)
        trimmed_blocks = [block.strip() for block in blocks if block.strip()][-LOG_MAX_ENTRIES:]
        with open(log_path, "w", encoding="utf-8") as file:
            file.write(LOG_ENTRY_SEPARATOR.join(trimmed_blocks))

    def _append_log_record(self, record: dict) -> None:
        blocks = self._read_log_blocks(record["boss_name"])
        blocks.insert(0, self._format_log_record_block(record))
        self._write_log_blocks(record["boss_name"], blocks)

    def _format_history_blocks_for_display(self, blocks: list[str]) -> str:
        if not blocks:
            return "저장된 기록이 없습니다."
        display_blocks = []
        for index, block in enumerate(blocks, start=1):
            display_blocks.append(f"< {index} >\n{block.strip()}")
        return ("\n" + ("=" * 37) + "\n").join(display_blocks)

    def _parse_log_block(self, block: str) -> dict:
        parsed = {
            "recorded_at": "",
            "boss_name": "",
            "validation_state": "",
            "validation_note": "",
            "actual_cut_time": "",
            "expected_time": "",
            "gwang_time": "",
            "overrun_time": "",
            "actual_cut_seconds": None,
            "expected_total_seconds": None,
            "overrun_seconds": None,
        }
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and lines[0].startswith("[") and lines[0].endswith("]"):
            parsed["recorded_at"] = lines[0][1:-1]
        time_entries = []
        for line in lines[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            value = value.strip()
            normalized_key = key.strip()
            if "보스" in normalized_key or "蹂댁뒪" in normalized_key:
                parsed["boss_name"] = value
            elif "검증" in normalized_key or "寃利" in normalized_key:
                if "완료" in value or "?꾨즺" in value or "꾨즺" in value:
                    parsed["validation_state"] = "검증 완료"
                elif "불일치" in value or "遺덉씪移" in value:
                    parsed["validation_state"] = "검증 불일치"
                elif "미확정" in value:
                    parsed["validation_state"] = "광 시간 미확정"
                else:
                    parsed["validation_state"] = value
            elif "비고" in normalized_key or "鍮꾧퀬" in normalized_key:
                parsed["validation_note"] = value
            elif "예상" in normalized_key or "?덉긽" in normalized_key:
                parsed["expected_time"] = value
                parsed["expected_total_seconds"] = self._parse_log_time_value(value)
            elif "덉긽" in normalized_key:
                parsed["expected_time"] = value
                parsed["expected_total_seconds"] = self._parse_log_time_value(value)
            elif "컷" in normalized_key or "而" in normalized_key:
                parsed["actual_cut_time"] = value
                parsed["actual_cut_seconds"] = self._parse_log_time_value(value)
            elif "광" in normalized_key or "愿" in normalized_key:
                parsed["gwang_time"] = value
            elif "초과" in normalized_key or "珥덇낵" in normalized_key:
                parsed["overrun_time"] = value
                parsed["overrun_seconds"] = self._parse_log_time_value(value)
            else:
                parsed_time = self._parse_log_time_value(value)
                if parsed_time is not None:
                    time_entries.append((normalized_key, value, parsed_time))
        if not parsed["gwang_time"] or not parsed["expected_time"] or not parsed["actual_cut_time"] or not parsed["overrun_time"]:
            fallback_times = [entry[1:] for entry in time_entries]
            if len(fallback_times) >= 4:
                if not parsed["gwang_time"]:
                    parsed["gwang_time"] = fallback_times[0][0]
                if not parsed["expected_time"]:
                    parsed["expected_time"] = fallback_times[1][0]
                    parsed["expected_total_seconds"] = fallback_times[1][1]
                if not parsed["actual_cut_time"]:
                    parsed["actual_cut_time"] = fallback_times[2][0]
                    parsed["actual_cut_seconds"] = fallback_times[2][1]
                if not parsed["overrun_time"]:
                    parsed["overrun_time"] = fallback_times[3][0]
                    parsed["overrun_seconds"] = fallback_times[3][1]
        if not parsed["validation_state"] and parsed["actual_cut_seconds"] is not None and parsed["expected_total_seconds"] is not None:
            parsed["validation_state"] = "검증 완료(기존 기록)"
        return parsed

    def _get_analysis_limit(self) -> int:
        selected = self.analysis_count_var.get()
        if selected == "전체":
            return LOG_MAX_ENTRIES
        digits = "".join(char for char in selected if char.isdigit())
        return int(digits) if digits else 10

    def _build_trusted_log_record(self) -> tuple[dict | None, str | None]:
        actual_cut_time = self.elapsed_var.get()
        actual_cut_seconds = self._parse_log_time_value(actual_cut_time)
        if actual_cut_seconds is None:
            actual_cut_seconds = self._now_elapsed()
        if actual_cut_seconds <= 0:
            return None, "총 시간이 0초라서 기록 후보를 만들 수 없습니다."
        validation_state = "검증 완료"
        validation_note = ""
        gwang_time = "미확정"
        expected_time = "계산 불가"
        overrun_time = "계산 불가"
        expected_total_seconds = None
        overrun_seconds = None
        if self.reached_70_display_seconds is None:
            validation_state = "광 시간 미확정"
            validation_note = "광 시간을 체크하지 않은 상태에서 보스 컷 후보를 만들었습니다."
        else:
            gwang_time = self.reached_70_var.get()
            remain_kill_time = self.remain_kill_var.get()
            overrun_time = self.overrun_var.get()
            remain_kill_seconds = self._parse_log_time_value(remain_kill_time)
            overrun_seconds = self._parse_log_time_value(overrun_time)
            if remain_kill_seconds is None:
                remain_kill_seconds = 0.0
            if overrun_seconds is None:
                overrun_seconds = 0.0
            if overrun_seconds > 0:
                expected_total_seconds = max(0.0, actual_cut_seconds - overrun_seconds)
            else:
                expected_total_seconds = actual_cut_seconds + remain_kill_seconds
            computed_expected_seconds = self._get_cut_expected_total_seconds()
            computed_overrun_seconds = None
            if computed_expected_seconds is not None and computed_expected_seconds > 0:
                computed_overrun_seconds = max(0.0, actual_cut_seconds - computed_expected_seconds)
            if expected_total_seconds <= 0:
                validation_state = "예상시간 계산 불가"
                validation_note = "현재 화면 값으로 예상시간을 계산할 수 없습니다."
            elif actual_cut_seconds < self.reached_70_display_seconds + 1.0:
                validation_state = "계산 조건 불충족"
                validation_note = "총 시간이 광 시간보다 최소 1초 이상 커야 예상시간 비교가 가능합니다."
            else:
                expected_time = format_seconds(expected_total_seconds, show_centiseconds=True)
                if overrun_seconds > 0:
                    overrun_time = self.overrun_var.get()
                else:
                    overrun_time = format_seconds(max(0.0, actual_cut_seconds - expected_total_seconds), show_centiseconds=True)
                if computed_expected_seconds is None:
                    validation_state = "내부 검증 불가"
                    validation_note = "내부 계산 기준 예상시간을 만들 수 없어 화면값만 사용했습니다."
                else:
                    expected_diff = abs(expected_total_seconds - computed_expected_seconds)
                    overrun_diff = abs((overrun_seconds or 0.0) - (computed_overrun_seconds or 0.0))
                    expected_diff_display = format_seconds(expected_diff, show_centiseconds=True)
                    overrun_diff_display = format_seconds(overrun_diff, show_centiseconds=True)
                    if expected_diff >= LOG_VALIDATION_TOLERANCE_SECONDS or overrun_diff >= LOG_VALIDATION_TOLERANCE_SECONDS:
                        validation_state = "검증 불일치"
                        validation_note = (
                            "화면표시와 내부계산이 어긋납니다. "
                            f"예상시간 오차 {expected_diff_display}, 초과시간 오차 {overrun_diff_display}"
                        )
                    else:
                        validation_note = ""
        record = {
            "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "boss_name": self._sanitize_boss_name(self.log_boss_name_var.get()) or "미지정보스",
            "validation_state": validation_state,
            "validation_note": validation_note,
            "gwang_time": gwang_time,
            "expected_time": expected_time,
            "actual_cut_time": actual_cut_time,
            "overrun_time": overrun_time,
            "gwang_seconds": float(self.reached_70_display_seconds) if self.reached_70_display_seconds is not None else None,
            "expected_total_seconds": float(expected_total_seconds) if expected_total_seconds is not None else None,
            "actual_cut_seconds": float(actual_cut_seconds),
            "overrun_seconds": float(overrun_seconds) if overrun_seconds is not None else None,
        }
        return record, None

    def _save_settings(self) -> None:
        config = configparser.ConfigParser()
        analysis_count_value = self.analysis_count_default
        if hasattr(self, "analysis_count_var") and self.analysis_count_var is not None:
            analysis_count_value = self.analysis_count_var.get()
        config["settings"] = {
            "background_path": self._normalize_background_source(self.background_path),
            "font_family": self.current_font_family,
            "background_alignment": self.background_alignment,
            "show_alert_overlay": str(self.show_alert_overlay),
            "show_alert_percent": str(self.show_alert_percent),
            "show_hodulgap_banner": str(self.show_hodulgap_banner),
            "show_elapsed_brush": str(self.show_elapsed_brush),
            "elapsed_brush_color": self.elapsed_brush_color_name,
            "analysis_count": analysis_count_value,
            "main_window_x": str(self.main_window_x),
            "main_window_y": str(self.main_window_y),
            "settings_window_x": str(self.settings_window_x),
            "settings_window_y": str(self.settings_window_y),
            "author": AUTHOR_NAME,
            "version": APP_VERSION,
            "last_updated": LAST_UPDATED,
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as file:
            config.write(file)

    def _build_ui(self) -> None:
        self.root.resizable(False, False)

        self.bg_canvas = tk.Canvas(self.root, width=WINDOW_WIDTH, height=WINDOW_HEIGHT, bd=0, highlightthickness=0)
        self.bg_canvas.pack(fill="both", expand=True)

        self.background_item = self.bg_canvas.create_image(0, 0, anchor="nw")

        self.root.bind("<Configure>", self._on_root_resize)

        self.settings_button = self._create_canvas_icon_button("settings", 440, 26, self.open_settings_window)
        self.log_panel_toggle_button = tk.Button(
            self.root,
            text="로그",
            font=tkfont.Font(family=self.current_font_family, size=9, weight="bold"),
            bg="#1d4ed8",
            fg="#ffffff",
            activebackground="#1e40af",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=2,
            pady=4,
            command=self.toggle_log_panel,
            cursor="hand2",
        )
        self._bind_hover_button(self.log_panel_toggle_button, "#1d4ed8", "#1e40af", "#ffffff", "#ffffff")
        self.bg_canvas.create_window(WINDOW_WIDTH - 2, 226, anchor="e", window=self.log_panel_toggle_button, width=34, height=88)
        self.boss_cut_button = tk.Button(
            self.root,
            text="보스\n컷",
            font=tkfont.Font(family=self.current_font_family, size=9, weight="bold"),
            bg="#0f766e",
            fg="#ffffff",
            activebackground="#115e59",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=2,
            pady=4,
            command=self.capture_boss_cut_candidate,
            cursor="hand2",
        )
        self._bind_hover_button(self.boss_cut_button, "#0f766e", "#115e59", "#ffffff", "#ffffff")
        self.bg_canvas.create_window(WINDOW_WIDTH - 2, 318, anchor="e", window=self.boss_cut_button, width=34, height=78)

        self.elapsed_color = "#cbd5e1"
        self._draw_elapsed_brush()
        self.elapsed_shadow_item = self.bg_canvas.create_text(
            238,
            71,
            text=self.elapsed_var.get(),
            fill="#0f172a",
            font=self.title_font,
        )
        self.elapsed_text_item = self.bg_canvas.create_text(
            236,
            69,
            text=self.elapsed_var.get(),
            fill=self.elapsed_color,
            font=self.title_font,
        )

        self.start_button = self._create_canvas_icon_button("play", 228, 124, self.toggle_timer)
        self.reset_button = self._create_canvas_icon_button("reset", 292, 124, self.reset_timer)

        self.record_button = tk.Button(
            self.root,
            text="광",
            font=tkfont.Font(family=self.current_font_family, size=11, weight="bold"),
            bg="#fb923c",
            fg="#ffffff",
            activebackground="#ea580c",
            activeforeground="#ffffff",
            relief="raised",
            bd=1,
            highlightthickness=0,
            padx=4,
            pady=6,
            width=2,
            command=self.record_70_percent_time,
            cursor="hand2",
        )
        self.bg_canvas.create_window(171, 124, anchor="center", window=self.record_button, width=40, height=40)
        self.record_time_label = tk.Label(
            self.root,
            textvariable=self.reached_70_var,
            font=self.label_font,
            bg="#0f172a",
            fg="#f8fafc",
            relief="sunken",
            bd=2,
            padx=8,
            pady=2,
            anchor="center",
        )
        self._draw_record_label()
        self.bg_canvas.create_window(176, 212, anchor="center", window=self.record_time_label, width=116)
        self.overrun_label_brush_item = self.bg_canvas.create_polygon(
            18, 155,
            32.72, 150,
            51.12, 156,
            75.04, 151,
            110, 158,
            104, 176,
            78.72, 174,
            52.96, 176,
            26, 173,
            fill="#8f2b2b",
            outline="",
            smooth=True,
            splinesteps=12,
        )
        self.overrun_label_text_item = self.bg_canvas.create_text(34, 166, anchor="w", text="초과시간", font=self.label_font, fill="#ffffff")
        self.overrun_time_item = self.bg_canvas.create_text(176, 166, text=self.overrun_var.get(), font=self.label_font, fill="#f8fafc")
        self._set_overrun_visibility(False)

        self._create_timer_row(268, "컷 남은", self.remain_kill_var, "#f8f1df", "#dc2626", store_as="remain_kill_box", label_fg="#ffffff", brush_color="#8f2b2b")
        self._create_timer_row(286, "90%", self.remain_90_var, "#eef2ff", "#ea580c", store_as="remain_90_box", visible=False)
        self.remain_kill_box.config(font=self.remain_kill_font)
        self.remain_kill_box.config(width=8, padx=1)

        self._draw_total_time_label()
        self._draw_brush_stroke(self.bg_canvas, 0, WINDOW_HEIGHT - 28, 70, 22, "#d8bea1")
        self.bg_canvas.create_text(10, WINDOW_HEIGHT - 8, anchor="sw", text="밤비", font=self.signature_font, fill="#5b3716")
        self.bg_canvas.create_text(WINDOW_WIDTH - 24, WINDOW_HEIGHT - 8, anchor="se", text=APP_VERSION, font=self.percent_font, fill="#ffffff")

    def _on_root_resize(self, event: tk.Event) -> None:
        width = max(300, event.width)
        height = max(260, event.height)
        self.bg_canvas.config(width=width, height=height)
        if self.background_alignment == "nw":
            self.bg_canvas.coords(self.background_item, 0, 0)
        else:
            self.bg_canvas.coords(self.background_item, width // 2, height // 2)

    def _draw_brush_stroke(self, canvas: tk.Canvas, x: int, y: int, width: int, height: int, color: str, tags=None) -> None:
        points = [
            x, y + 5,
            x + width * 0.16, y,
            x + width * 0.36, y + 6,
            x + width * 0.62, y + 1,
            x + width, y + 8,
            x + width - 6, y + height,
            x + width * 0.66, y + height - 2,
            x + width * 0.38, y + height,
            x + 8, y + height - 3,
        ]
        canvas.create_polygon(points, fill=color, outline="", smooth=True, splinesteps=12, tags=tags)

    def _draw_header_brush(self, canvas: tk.Canvas, top_y: int, color: str, tags=None) -> None:
        points = [
            18, top_y + 5,
            32.72, top_y,
            51.12, top_y + 6,
            75.04, top_y + 1,
            110, top_y + 8,
            104, top_y + 26,
            78.72, top_y + 24,
            52.96, top_y + 26,
            26, top_y + 23,
        ]
        canvas.create_polygon(points, fill=color, outline="", smooth=True, splinesteps=12, tags=tags)

    def _draw_elapsed_brush(self) -> None:
        self.bg_canvas.delete(ELAPSED_BRUSH_TAG)
        if not self.show_elapsed_brush:
            return
        brush_color = ELAPSED_BRUSH_COLORS.get(self.elapsed_brush_color_name, ELAPSED_BRUSH_COLORS["노랑"])
        self._draw_brush_stroke(self.bg_canvas, 84, 51, 302, 40, brush_color, tags=ELAPSED_BRUSH_TAG)
        self.bg_canvas.tag_raise(ELAPSED_BRUSH_TAG, self.background_item)

    def _draw_total_time_label(self, brush_color: str = TOTAL_LABEL_BRUSH_NORMAL, text_color: str = TOTAL_LABEL_TEXT_NORMAL) -> None:
        self.bg_canvas.delete(TOTAL_LABEL_TAG)
        self.bg_canvas.delete(TOTAL_LABEL_HITBOX_TAG)
        self._draw_brush_stroke(self.bg_canvas, 18, 40, 92, 26, brush_color, tags=TOTAL_LABEL_TAG)
        self.bg_canvas.create_text(33, 56, anchor="w", text="珥??쒓컙", font=self.header_font, fill=text_color, tags=TOTAL_LABEL_TAG)
        self.bg_canvas.create_rectangle(18, 40, 110, 66, fill="", outline="", tags=TOTAL_LABEL_HITBOX_TAG)
        for bind_tag in (TOTAL_LABEL_TAG, TOTAL_LABEL_HITBOX_TAG):
            self.bg_canvas.tag_bind(bind_tag, "<Enter>", self._on_total_label_enter)
            self.bg_canvas.tag_bind(bind_tag, "<Leave>", self._on_total_label_leave)
            self.bg_canvas.tag_bind(bind_tag, "<Button-1>", self._on_total_label_click)
        self.bg_canvas.tag_raise(TOTAL_LABEL_HITBOX_TAG)

    def _on_total_label_enter(self, _event=None) -> None:
        self.bg_canvas.config(cursor="hand2")
        self._draw_total_time_label(TOTAL_LABEL_BRUSH_HOVER, TOTAL_LABEL_TEXT_HOVER)

    def _on_total_label_leave(self, _event=None) -> None:
        self.bg_canvas.config(cursor="")
        self._draw_total_time_label(TOTAL_LABEL_BRUSH_NORMAL, TOTAL_LABEL_TEXT_NORMAL)

    def _parse_elapsed_input(self, raw_value: str) -> float | None:
        value = (raw_value or "").strip()
        if not value:
            return None
        parts = value.split(":")
        try:
            if len(parts) == 1:
                return max(0.0, float(parts[0]))
            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = float(parts[1])
                return max(0.0, minutes * 60 + seconds)
            if len(parts) == 3:
                minutes = int(parts[0])
                seconds = int(parts[1])
                centiseconds = int(parts[2])
                return max(0.0, minutes * 60 + seconds + centiseconds / 100)
        except ValueError:
            return None
        return None

    def _create_timer_row(self, y: int, label_text: str, value_var: tk.StringVar, box_bg: str, box_fg: str, store_as: str | None = None, visible: bool = True, label_fg: str = "#f8fafc", brush_color: str = "#c65d1e") -> None:
        if not visible:
            hidden_box = tk.Label(self.root, textvariable=value_var)
            if store_as:
                setattr(self, store_as, hidden_box)
            return
        self._draw_header_brush(self.bg_canvas, y - 16, brush_color)
        self.bg_canvas.create_text(34, y, anchor="w", text=label_text, font=self.label_font, fill=label_fg)
        box_pad_y = 3 if store_as == "remain_kill_box" else 4
        box_x = 214 if store_as == "remain_kill_box" else 206
        value_box = tk.Label(self.root, textvariable=value_var, font=self.value_font, width=9, bg=box_bg, fg=box_fg, relief="sunken", bd=2, padx=5, pady=box_pad_y, anchor="center")
        self.bg_canvas.create_window(box_x, y, anchor="center", window=value_box)
        if store_as:
            setattr(self, store_as, value_box)

    def _make_action_button(self, text: str, bg: str, active_bg: str, command) -> tk.Button:
        return tk.Button(self.root, text=text, width=7, font=self.button_font, bg=bg, fg="white", activebackground=active_bg, activeforeground="white", relief="flat", bd=0, highlightthickness=0, padx=8, pady=5, command=command, cursor="hand2")

    def _ensure_button_image(self, icon_key: str, state: str = "normal") -> tk.PhotoImage:
        cache_key = f"{icon_key}:{state}"
        if cache_key in self.button_images:
            return self.button_images[cache_key]
        image = tk.PhotoImage(file=get_button_icon_path(icon_key, state))
        max_size = BUTTON_ICON_MAX_SIZE.get(icon_key, 54)
        scale = max(1, math.ceil(max(image.width(), image.height()) / max_size))
        if scale > 1:
            image = image.subsample(scale, scale)
        self.button_images[cache_key] = image
        return image

    def _create_canvas_icon_button(self, icon_key: str, x: int, y: int, command):
        image = self._ensure_button_image(icon_key, "normal")
        tag = f"icon_button_{icon_key}"
        item_id = self.bg_canvas.create_image(x, y, image=image, anchor="center", tags=(tag,))
        hitbox_id = self.bg_canvas.create_rectangle(x - 24, y - 24, x + 24, y + 24, fill="", outline="", tags=(f"{tag}_hitbox",))
        self.bg_canvas.tag_raise(hitbox_id)
        self.canvas_icon_positions[item_id] = (x, y)
        self.canvas_icon_keys[item_id] = icon_key
        self.canvas_icon_hitboxes[item_id] = hitbox_id
        self.canvas_icon_commands[item_id] = command
        self.bg_canvas.tag_bind(hitbox_id, "<Enter>", lambda event, current_id=item_id: self._hover_canvas_icon_button(current_id))
        self.bg_canvas.tag_bind(hitbox_id, "<ButtonPress-1>", lambda event, current_id=item_id: self._press_canvas_icon_button(current_id))
        self.bg_canvas.tag_bind(hitbox_id, "<ButtonRelease-1>", lambda event, current_id=item_id, action=command: self._release_canvas_icon_button(current_id, action, event))
        self.bg_canvas.tag_bind(hitbox_id, "<Leave>", lambda event, current_id=item_id: self._reset_canvas_icon_button(current_id))
        return item_id

    def _set_canvas_icon_button_image(self, item_id: int, icon_key: str) -> None:
        self.canvas_icon_keys[item_id] = icon_key
        self.bg_canvas.itemconfig(item_id, image=self._ensure_button_image(icon_key, "normal"))

    def _hover_canvas_icon_button(self, item_id: int) -> None:
        self.bg_canvas.config(cursor="hand2")
        icon_key = self.canvas_icon_keys.get(item_id)
        if icon_key is not None:
            self.bg_canvas.itemconfig(item_id, image=self._ensure_button_image(icon_key, "pressed"))
            hint_map = {
                "settings": "환경설정",
                "play": "시작",
                "pause": "일시정지",
                "reset": "초기화",
            }
            hint_text = hint_map.get(icon_key)
            if hint_text is not None:
                x, y = self.canvas_icon_positions.get(item_id, (0, 0))
                self._show_tooltip(hint_text, canvas_x=x, canvas_y=y + 18)
            label_map = {
                "settings": "환경설정",
                "play": "시작",
                "pause": "일시정지",
                "reset": "초기화",
            }
            hint_text = label_map.get(icon_key)
            if hint_text is not None:
                x, y = self.canvas_icon_positions.get(item_id, (0, 0))
                self._show_tooltip(hint_text, canvas_x=x, canvas_y=y + 18)

    def _press_canvas_icon_button(self, item_id: int) -> None:
        icon_key = self.canvas_icon_keys.get(item_id)
        if icon_key is not None:
            self.bg_canvas.itemconfig(item_id, image=self._ensure_button_image(icon_key, "pressed"))

    def _release_canvas_icon_button(self, item_id: int, command, event) -> None:
        self._reset_canvas_icon_button(item_id)
        if self._is_canvas_release_inside_item(item_id, event):
            command()

    def _reset_canvas_icon_button(self, item_id: int) -> None:
        self.bg_canvas.config(cursor="")
        self._hide_tooltip()
        icon_key = self.canvas_icon_keys.get(item_id)
        if icon_key is not None:
            self.bg_canvas.itemconfig(item_id, image=self._ensure_button_image(icon_key, "normal"))

    def _make_pressable_button(self, text: str, bg: str, active_bg: str, command, padx: int = 18, pady: int = 8) -> tk.Button:
        button = tk.Button(self.root, text=text, font=tkfont.Font(family=self.current_font_family, size=12, weight="bold"), bg=bg, fg="white", activebackground=active_bg, activeforeground="white", relief="raised", bd=2, highlightthickness=0, padx=18, pady=8, command=command, cursor="hand2")
        button.config(padx=padx, pady=pady)
        button.bind("<ButtonPress-1>", lambda event: button.config(relief="sunken"))
        button.bind("<ButtonRelease-1>", lambda event: button.config(relief="raised"))
        return button

    def _is_canvas_release_inside_item(self, item_id: int, event) -> bool:
        bbox = self.bg_canvas.bbox(item_id)
        if bbox is None:
            return False
        left, top, right, bottom = bbox
        return left <= event.x <= right and top <= event.y <= bottom

    def _configure_record_button_style(self, text_fg: str, highlight_thickness: int, highlight_color: str) -> None:
        self.record_button.config(
            fg=text_fg,
            activeforeground=text_fg,
            highlightthickness=highlight_thickness,
            highlightbackground=highlight_color,
            highlightcolor=highlight_color,
        )

    def apply_alert_overlay_setting(self) -> None:
        self.show_alert_overlay = self.show_alert_overlay_var.get()
        self._draw_alert_banner()

    def apply_alert_percent_setting(self) -> None:
        self.show_alert_percent = self.show_alert_percent_var.get()
        if self.reached_70_display_seconds is None:
            self.bg_canvas.delete(ALERT_TAG)
            return
        self._draw_alert_banner()

    def apply_hodulgap_banner_setting(self) -> None:
        self.show_hodulgap_banner = self.show_hodulgap_banner_var.get()
        self._draw_progress_graph(self.current_percent)

    def apply_elapsed_brush_setting(self, *_args) -> None:
        selected_color = self.elapsed_brush_color_var.get()
        if selected_color in ELAPSED_BRUSH_COLORS:
            self.elapsed_brush_color_name = selected_color
        self.show_elapsed_brush = self.elapsed_brush_color_name != "배경 없음"
        self._draw_elapsed_brush()
        self.bg_canvas.tag_raise(self.elapsed_shadow_item)
        self.bg_canvas.tag_raise(self.elapsed_text_item)
        self._save_settings()

    def _apply_font_family(self, family: str) -> None:
        self.current_font_family = family
        for font_obj in [self.title_font, self.header_font, self.alert_font, self.label_font, self.value_font, self.remain_kill_font, self.button_font, self.banner_font, self.percent_font, self.burst_font, self.expected_value_font, self.signature_font, self.icon_font]:
            font_obj.config(family=family)
        self.record_button.config(font=tkfont.Font(family=family, size=11, weight="bold"))
        if hasattr(self, "log_panel_toggle_button") and self.log_panel_toggle_button is not None:
            self.log_panel_toggle_button.config(font=tkfont.Font(family=family, size=9, weight="bold"))
        if hasattr(self, "boss_cut_button") and self.boss_cut_button is not None:
            self.boss_cut_button.config(font=tkfont.Font(family=family, size=9, weight="bold"))
        self._draw_record_label()
        self._draw_progress_graph(self.current_percent)
        self._draw_alert_banner()

    def _now_elapsed(self) -> float:
        return self.base_elapsed_seconds + (time.perf_counter() - self.start_perf_time) if self.running else self.base_elapsed_seconds

    def _sync_start_button_icon(self) -> None:
        if hasattr(self, "start_button"):
            self._set_canvas_icon_button_image(self.start_button, "pause" if self.running else "play")

    def toggle_timer(self) -> None:
        if self.running:
            self.stop_timer()
        else:
            self.start_timer()

    def start_timer(self) -> None:
        if self.running:
            return
        self.running = True
        self.start_perf_time = time.perf_counter()
        self._set_elapsed_color("#16a34a")
        self._sync_start_button_icon()
        self._schedule_update()

    def stop_timer(self) -> None:
        if self.running:
            self.base_elapsed_seconds = self._now_elapsed()
        self.running = False
        self._set_elapsed_color("#cbd5e1")
        self._sync_start_button_icon()
        self._cancel_update()
        self._start_pause_blink()
        self._refresh_ui()

    def reset_timer(self) -> None:
        self.stop_timer()
        self.base_elapsed_seconds = self.initial_elapsed_seconds
        self.reached_70_calc_seconds = None
        self.reached_70_display_seconds = None
        self.current_percent = None
        self._stop_pause_blink()
        self._update_elapsed_display("00:00:00")
        self._set_elapsed_color("#cbd5e1")
        self.reached_70_var.set("00:00:00")
        self.remain_90_var.set("00:00:00")
        self.remain_kill_var.set("00:00:00")
        self._set_overrun_display("00:00:00")
        self._set_overrun_visibility(False)
        self._reset_effects()
        self._apply_default_boxes()
        self._draw_progress_graph(None)
        self._configure_record_button_style("#ffffff", 1, "#fecdd3")
        self._sync_start_button_icon()
        self._update_boss_cut_button_effect(time.perf_counter())

    def record_70_percent_time(self) -> None:
        current_elapsed = self._now_elapsed()
        if current_elapsed <= 0:
            return
        self.reached_70_calc_seconds = int(current_elapsed)
        self.reached_70_display_seconds = current_elapsed
        self.reached_70_var.set(format_seconds(current_elapsed, show_centiseconds=True))
        self._configure_record_button_style("#ffffff", 2, "#7f1d1d")
        self._reset_effects()
        self._update_prediction_labels(current_elapsed)

    def _schedule_update(self) -> None:
        self._cancel_update()
        self._stop_pause_blink()
        self.update_after_id = self.root.after(10, self._update_loop)

    def _cancel_update(self) -> None:
        if self.update_after_id is not None:
            self.root.after_cancel(self.update_after_id)
            self.update_after_id = None

    def _set_elapsed_color(self, color: str) -> None:
        self.elapsed_color = color
        self.bg_canvas.itemconfig(self.elapsed_text_item, fill=color)

    def _update_elapsed_display(self, text: str) -> None:
        self.elapsed_var.set(text)
        self.bg_canvas.itemconfig(self.elapsed_shadow_item, text=text)
        self.bg_canvas.itemconfig(self.elapsed_text_item, text=text)

    def _set_overrun_display(self, text: str) -> None:
        self.overrun_var.set(text)
        if hasattr(self, "overrun_time_item"):
            self.bg_canvas.itemconfig(self.overrun_time_item, text=text, font=self.label_font)

    def _set_overrun_visibility(self, visible: bool) -> None:
        state = "normal" if visible else "hidden"
        for item_name in ("overrun_label_brush_item", "overrun_label_text_item", "overrun_time_item"):
            if hasattr(self, item_name):
                self.bg_canvas.itemconfig(getattr(self, item_name), state=state)

    def _update_boss_cut_button_effect(self, now: float) -> None:
        if not hasattr(self, "boss_cut_button") or self.boss_cut_button is None:
            return
        overrun_seconds = self._parse_log_time_value(self.overrun_var.get()) or 0.0
        if overrun_seconds > 0.0:
            is_flash_on = int(now * 4) % 2 == 0
            bg_color = "#facc15" if is_flash_on else "#ef4444"
            active_bg = "#eab308" if is_flash_on else "#dc2626"
            self.boss_cut_button.config(bg=bg_color, activebackground=active_bg, fg="#111827", activeforeground="#111827")
            return
        self.boss_cut_button.config(bg="#0f766e", activebackground="#115e59", fg="#ffffff", activeforeground="#ffffff")

    def _apply_initial_elapsed_seconds(self, elapsed_seconds: float) -> None:
        self.base_elapsed_seconds = max(0.0, elapsed_seconds)
        if self.running:
            self.start_perf_time = time.perf_counter()
        self._update_elapsed_display(format_seconds(self._now_elapsed(), show_centiseconds=True))
        self.reached_70_calc_seconds = None
        self.reached_70_display_seconds = None
        self.current_percent = None
        self.reached_70_var.set("00:00:00")
        self.remain_90_var.set("00:00:00")
        self.remain_kill_var.set("00:00:00")
        self._set_overrun_display("00:00:00")
        self._set_overrun_visibility(False)
        self._reset_effects()
        self._apply_default_boxes()
        self._draw_progress_graph(None)
        self._draw_alert_banner()
        self._update_boss_cut_button_effect(time.perf_counter())
        

    def _on_total_label_click(self, _event=None) -> None:
        try:
            entered_value = simpledialog.askstring(
                "珥??쒓컙 ?ㅼ젙",
                "硫붿씤 ??대㉧ 珥덇린媛믪쓣 ?낅젰?섏꽭??\n?뺤떇: 珥?/ MM:SS / MM:SS:CC",
                initialvalue=format_seconds(self._now_elapsed(), show_centiseconds=True),
                parent=self.root,
            )
            if entered_value is None:
                return
            parsed_seconds = self._parse_elapsed_input(entered_value)
            if parsed_seconds is None:
                messagebox.showerror("?낅젰 ?ㅻ쪟", "?쒓컙 ?뺤떇???щ컮瑜댁? ?딆뒿?덈떎.")
                return
            self._apply_initial_elapsed_seconds(parsed_seconds)
        except Exception as exc:
            messagebox.showerror("珥??쒓컙 ?ㅼ젙 ?ㅻ쪟", str(exc))

    def _start_pause_blink(self) -> None:
        self._stop_pause_blink()
        if self.base_elapsed_seconds <= 0:
            return
        self.pause_blink_on = True
        self._pause_blink_tick()

    def _pause_blink_tick(self) -> None:
        if self.running:
            return
        self._set_elapsed_color("#cbd5e1" if self.pause_blink_on else "#6b7280")
        self.pause_blink_on = not self.pause_blink_on
        self.pause_blink_after_id = self.root.after(500, self._pause_blink_tick)

    def _stop_pause_blink(self) -> None:
        if self.pause_blink_after_id is not None:
            self.root.after_cancel(self.pause_blink_after_id)
            self.pause_blink_after_id = None
        self.pause_blink_on = False

    def _update_loop(self) -> None:
        self._refresh_ui()
        if self.running:
            self._schedule_update()

    def _refresh_ui(self) -> None:
        current_elapsed = self._now_elapsed()
        self._update_elapsed_display(format_seconds(current_elapsed, show_centiseconds=True))
        if self.reached_70_display_seconds is not None:
            self.reached_70_var.set(format_seconds(self.reached_70_display_seconds, show_centiseconds=True))
        self._update_prediction_labels(current_elapsed)
        self._update_effects()

    def _get_cut_expected_total_seconds(self) -> float | None:
        if self.reached_70_display_seconds is None:
            return None
        return self.reached_70_display_seconds * 10 / 7

    def _update_prediction_labels(self, current_elapsed: float) -> None:
        if self.reached_70_calc_seconds is None or self.reached_70_display_seconds is None:
            self.remain_90_var.set("00:00:00")
            self.remain_kill_var.set("00:00:00")
            self._set_overrun_display("00:00:00")
            self._set_overrun_visibility(False)
            self.current_percent = None
            self._draw_progress_graph(None)
            return
        remain_90_total = self.reached_70_calc_seconds * 2 / 7
        remain_kill_total = self.reached_70_calc_seconds * 3 / 7
        elapsed_after_record = max(0.0, current_elapsed - self.reached_70_display_seconds)
        remain_90_now = max(0.0, remain_90_total - elapsed_after_record)
        remain_kill_now = max(0.0, remain_kill_total - elapsed_after_record)
        self.remain_90_var.set(format_seconds(remain_90_now, show_centiseconds=True))
        self.remain_kill_var.set(format_seconds(remain_kill_now, show_centiseconds=True))
        overrun_now = max(0.0, elapsed_after_record - remain_kill_total) if remain_kill_now <= 0 else 0.0
        self._set_overrun_display(format_seconds(overrun_now, show_centiseconds=True))
        self._set_overrun_visibility(overrun_now > 0.0)
        if remain_90_now <= 0 and not self.blink_90_active:
            self.blink_90_active = True
            self.blink_90_end_time = time.perf_counter() + 3.0
        if remain_kill_now <= 0 and not self.blink_kill_active:
            self.blink_kill_active = True
            self.blink_kill_end_time = time.perf_counter() + 3.0
        progress_ratio = 0.0 if remain_kill_total <= 0 else min(1.0, elapsed_after_record / remain_kill_total)
        self.current_percent = 70.0 + (30.0 * progress_ratio)
        self._draw_progress_graph(self.current_percent)

    def _update_effects(self) -> None:
        now = time.perf_counter()
        self._update_boss_cut_button_effect(now)
        if self.blink_90_active:
            if now >= self.blink_90_end_time:
                self.blink_90_active = False
                self.remain_90_box.config(bg="#eef2ff")
            else:
                self.remain_90_box.config(bg="#fef3c7" if int(now * 2) % 2 == 0 else "#fecaca")
        else:
            self.remain_90_box.config(bg="#eef2ff")
        if self.blink_kill_active:
            if now >= self.blink_kill_end_time:
                self.blink_kill_active = False
                self.remain_kill_box.config(bg="#f8f1df")
            else:
                self.remain_kill_box.config(bg="#fee2e2" if int(now * 2) % 2 == 0 else "#fde68a")
        else:
            self.remain_kill_box.config(bg="#f8f1df")
        self._draw_alert_banner()

    def _draw_alert_banner(self) -> None:
        self.alert_canvas.delete("all")
        if self.reached_70_display_seconds is None:
            self._draw_sleepy_pomeranian(45, 78)
            self.alert_canvas.create_oval(126, 10, 214, 42, fill="#f8fafc", outline="#cbd5e1", width=2)
            self.alert_canvas.create_text(170, 26, text="천천히...", fill="#475569", font=self.header_font)
            return
        if self.current_percent is not None and self.current_percent >= 95.0:
            speech = "洹몃깷 鍮〓뵜??鍮〓뵜~!"
        elif self.current_percent is not None and self.current_percent >= 90.0:
            speech = "鍮〓뵜 ?댁빞??嫄?"
        elif self.current_percent is not None and self.current_percent >= 85.0:
            speech = "85% ?뚰뙆!!"
        elif self.current_percent is not None and self.current_percent >= 80.0:
            speech = "80% ?뚰뙆!!"
        else:
            speech = "愿??대떎!!"
        self._draw_sleepy_pomeranian(80, 33)
        self.alert_canvas.create_oval(126, 10, 268, 44, fill="#f8fafc", outline="#cbd5e1", width=2)
        self.alert_canvas.create_text(197, 27, text=speech, fill="#475569", font=self.header_font)
        self._draw_percent_burst(344, 33, self.current_percent or 70.0)

    def _draw_progress_graph(self, current_percent: float | None) -> None:
        canvas = self.graph_canvas
        canvas.delete("all")
        left, right, top, bottom = 18, 395, 32, 48
        width = right - left
        canvas.create_line(left, top - 14, right, top - 14, fill="#bfdbfe", width=1)
        canvas.create_rectangle(left, top, right, bottom, fill="#dbeafe", outline="#60a5fa", width=2)
        for index, label in enumerate([70, 80, 90, 100]):
            x = left + (width * index / 3)
            canvas.create_line(x, top - 8, x, bottom + 8, fill="#60a5fa", width=1)
            canvas.create_text(x, bottom + 14, text=f"{label}%", fill="#1e3a8a", font=self.percent_font)
        if current_percent is None:
            canvas.create_text(154, 10, text="而??덉긽 ?쒓컙:", fill="#7c3aed", font=self.header_font)
            canvas.create_text(258, 10, text="--:--:--", fill="#1e3a8a", font=self.header_font)
            return
        clamped_percent = max(70.0, min(100.0, current_percent))
        progress_ratio = (clamped_percent - 70.0) / 30.0
        progress_x = left + (width * progress_ratio)
        cut_expected_total = self._get_cut_expected_total_seconds()
        if cut_expected_total is not None:
            canvas.create_text(154, 10, text="而??덉긽 ?쒓컙:", fill="#7c3aed", font=self.header_font)
            canvas.create_text(258, 10, text=format_seconds(cut_expected_total, show_centiseconds=True), fill="#1e3a8a", font=self.header_font)
        if clamped_percent >= 90.0:
            progress_fill = "#dc2626"
        elif clamped_percent >= 80.0:
            progress_fill = "#f59e0b"
        else:
            progress_fill = "#fde047"
        canvas.create_rectangle(left, top, progress_x, bottom, fill=progress_fill, outline="")
        self._draw_dog_icon(canvas, progress_x, top + 2)
        if clamped_percent >= 90.0:
            self._draw_urgent_effect(canvas, progress_x, top + 2, clamped_percent >= 100.0)
        text_x = min(progress_x + 28, right - 16)
        canvas.create_text(text_x, top - 12, text=f"{clamped_percent:04.1f}%", fill="#1e3a8a", font=self.percent_font)
        self._draw_small_banner(canvas, clamped_percent)

    def _draw_dog_icon(self, canvas: tk.Canvas, center_x: float, base_y: float) -> None:
        head_left = center_x - 11
        head_top = base_y - 22
        head_right = center_x + 11
        head_bottom = base_y
        canvas.create_polygon(center_x - 9, head_top + 6, center_x - 2, head_top - 4, center_x + 1, head_top + 7, fill="#fff7ed", outline="#d6d3d1", width=1)
        canvas.create_polygon(center_x + 9, head_top + 6, center_x + 2, head_top - 4, center_x - 1, head_top + 7, fill="#fff7ed", outline="#d6d3d1", width=1)
        canvas.create_oval(head_left, head_top, head_right, head_bottom, fill="#ffffff", outline="#d6d3d1", width=2)
        canvas.create_oval(center_x - 6, head_top + 8, center_x - 3, head_top + 10, fill="#111827", outline="")
        canvas.create_oval(center_x + 3, head_top + 8, center_x + 6, head_top + 10, fill="#111827", outline="")
        canvas.create_oval(center_x - 3, head_top + 13, center_x + 3, head_top + 17, fill="#111827", outline="")
        canvas.create_arc(center_x - 5, head_top + 13, center_x + 5, head_top + 21, start=200, extent=140, style="arc", outline="#64748b", width=2)

    def _draw_urgent_effect(self, canvas: tk.Canvas, center_x: float, base_y: float, maxed: bool) -> None:
        effect_color = "#facc15" if not maxed else "#fb7185"
        radius = 16 if not maxed else 20
        for angle in range(0, 360, 45):
            dx = radius * math.cos(math.radians(angle))
            dy = radius * math.sin(math.radians(angle))
            canvas.create_line(center_x + dx * 0.7, base_y - 12 + dy * 0.7, center_x + dx, base_y - 12 + dy, fill=effect_color, width=2)

    def _draw_sleepy_pomeranian(self, x: float, y: float) -> None:
        c = self.alert_canvas
        c.create_polygon(x - 14, y - 10, x - 6, y - 22, x - 2, y - 6, fill="#fff7ed", outline="#d6d3d1", width=2)
        c.create_polygon(x + 14, y - 10, x + 6, y - 22, x + 2, y - 6, fill="#fff7ed", outline="#d6d3d1", width=2)
        c.create_oval(x - 18, y - 16, x + 18, y + 16, fill="#ffffff", outline="#d6d3d1", width=2)
        c.create_line(x - 8, y - 1, x - 3, y - 4, fill="#475569", width=2)
        c.create_line(x + 3, y - 4, x + 8, y - 1, fill="#475569", width=2)
        c.create_oval(x - 3, y + 3, x + 3, y + 8, fill="#111827", outline="")
        c.create_arc(x - 8, y + 5, x + 8, y + 14, start=200, extent=140, style="arc", outline="#64748b", width=2)
        c.create_text(x + 24, y - 18, text="z", fill="#93c5fd", font=self.percent_font)
        c.create_text(x + 33, y - 24, text="z", fill="#bfdbfe", font=self.percent_font)

    def _draw_percent_burst(self, x: float, y: float, percent: float) -> None:
        c = self.alert_canvas
        fill1 = "#d9f99d"
        fill2 = "#bef264"
        if percent >= 95.0:
            fill1, fill2 = ("#ecfccb", "#d9f99d") if int(time.perf_counter() * 6) % 2 == 0 else ("#bef264", "#a3e635")
        c.create_polygon(x - 48, y - 20, x + 36, y - 24, x + 42, y + 10, x - 40, y + 16, fill=fill1, outline="")
        c.create_polygon(x - 42, y - 14, x + 40, y - 18, x + 34, y + 16, x - 48, y + 8, fill=fill2, outline="")
        c.create_line(x - 34, y - 24, x - 12, y - 30, fill="#84cc16", width=3)
        c.create_line(x + 22, y + 18, x + 36, y + 24, fill="#84cc16", width=3)
        c.create_text(x, y, text=f"{max(70.0, min(100.0, percent)):04.1f}%", fill="#dc2626", font=self.burst_font)

    def _draw_small_banner(self, canvas: tk.Canvas, percent: float) -> None:
        if percent < 90.0:
            return
        pulse = int(time.perf_counter() * 6) % 3
        if percent >= 100.0:
            bg_colors = ["#7f1d1d", "#991b1b", "#b91c1c"]
            fg_colors = ["#fef08a", "#ffffff", "#fde68a"]
            text = "????鍮???!!"
        elif percent >= 95.0:
            bg_colors = ["#9a3412", "#c2410c", "#ea580c"]
            fg_colors = ["#fff7ed", "#ffffff", "#ffedd5"]
            text = "鍮〓뵜??鍮〓뵜~!"
        else:
            bg_colors = ["#991b1b", "#b91c1c", "#dc2626"]
            fg_colors = ["#fff7ed", "#ffffff", "#fef2f2"]
            text = "蹂댁뒪 鍮〓뵜!!"
        canvas.create_rectangle(252, 58, 404, 78, fill=bg_colors[pulse], outline="", width=0)
        canvas.create_text(328, 68, text=text, fill=fg_colors[pulse], font=self.banner_font)

    def _apply_default_boxes(self) -> None:
        self.remain_90_box.config(bg="#eef2ff")
        self.remain_kill_box.config(bg="#f8f1df")

    def _reset_effects(self) -> None:
        self.blink_90_active = False
        self.blink_90_end_time = 0.0
        self.blink_kill_active = False
        self.blink_kill_end_time = 0.0
        self.alert_canvas.delete("all")

    def _flash_save_notice(self) -> None:
        if not hasattr(self, "save_notice_label") or not self.save_notice_label.winfo_exists():
            return
        now = time.perf_counter()
        if now >= self.settings_notice_end_time:
            self.save_notice_label.config(text="")
            self.save_notice_label.place_forget()
            self.settings_notice_after_id = None
            return
        self.save_notice_label.place(x=18, y=404)
        self.save_notice_label.config(text="저장되었습니다" if int(now * 2) % 2 == 0 else "")
        self.settings_notice_after_id = self.settings_window.after(500, self._flash_save_notice)

    def open_settings_window(self) -> None:
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.focus_force()
            return
        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title("환경설정")
        self.settings_window.geometry(f"430x500+{self.settings_window_x}+{self.settings_window_y}")
        self.settings_window.resizable(False, False)
        self.settings_window.protocol("WM_DELETE_WINDOW", self.close_settings_window)
        self.settings_bg_label = tk.Label(self.settings_window, bd=0)
        self.settings_bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        if self.background_image is not None:
            self.settings_bg_label.config(image=self.background_image)

        tk.Label(self.settings_window, text="환경설정", font=self.header_font, bg="#000001", fg="#f8fafc").place(x=18, y=14)
        tk.Label(self.settings_window, text="배경 이미지", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=48)
        tk.Entry(self.settings_window, textvariable=self.settings_path_var, font=(self.current_font_family, 10), width=33, bd=0, highlightthickness=0).place(x=18, y=76, width=276, height=26)
        tk.Button(self.settings_window, text="파일 선택", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.select_background_file, cursor="hand2").place(x=304, y=74, width=98, height=28)
        tk.Button(self.settings_window, text="기본배경", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_default_background, cursor="hand2").place(x=18, y=106, width=86, height=28)
        tk.Button(self.settings_window, text="벽지", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_blue_wallpaper, cursor="hand2").place(x=112, y=106, width=86, height=28)
        tk.Button(self.settings_window, text="장원영", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_jang_wonyoung_background, cursor="hand2").place(x=206, y=106, width=86, height=28)
        tk.Label(self.settings_window, text="諛곌꼍 ?뺣젹", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=142)
        tk.Radiobutton(self.settings_window, text="좌상단", value="nw", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=18, y=168)
        tk.Radiobutton(self.settings_window, text="以묒븰", value="center", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=104, y=168)
        tk.Checkbutton(
            self.settings_window,
            text="媛뺤븘吏/留먰뭾???쒖떆",
            variable=self.show_alert_overlay_var,
            command=self.apply_alert_overlay_setting,
            font=self.button_font,
            bg="#000001",
            fg="#f8fafc",
            selectcolor="#1e293b",
            activebackground="#000001",
            activeforeground="#f8fafc",
            highlightthickness=0,
            bd=0,
        ).place(x=18, y=202)
        tk.Checkbutton(
            self.settings_window,
            text="?쇱꽱???쒖떆",
            variable=self.show_alert_percent_var,
            command=self.apply_alert_percent_setting,
            font=self.button_font,
            bg="#000001",
            fg="#f8fafc",
            selectcolor="#1e293b",
            activebackground="#000001",
            activeforeground="#f8fafc",
            highlightthickness=0,
            bd=0,
        ).place(x=18, y=226)
        tk.Checkbutton(
            self.settings_window,
            text="?몃뱾媛??ㅻ뜑 諛곕꼫",
            variable=self.show_hodulgap_banner_var,
            command=self.apply_hodulgap_banner_setting,
            font=self.button_font,
            bg="#000001",
            fg="#f8fafc",
            selectcolor="#1e293b",
            activebackground="#000001",
            activeforeground="#f8fafc",
            highlightthickness=0,
            bd=0,
        ).place(x=18, y=250)
        tk.Label(self.settings_window, text="?고듃", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=276)

        self.font_menu = tk.OptionMenu(self.settings_window, self.font_family_var, *self.available_font_families)
        self.font_menu.config(font=self.button_font, bg="#1e293b", fg="white", activebackground="#334155", activeforeground="white", highlightthickness=0, bd=0)
        self.font_menu["menu"].config(font=(self.current_font_family, 9))
        self.font_menu.place(x=18, y=300, width=276, height=30)

        self.apply_button = tk.Button(self.settings_window, text="저장", font=self.button_font, bg="#2563eb", fg="white", activebackground="#1d4ed8", activeforeground="white", relief="flat", bd=0, highlightthickness=0, command=self.apply_settings, cursor="hand2")
        self.apply_button.place(x=304, y=334, width=98, height=30)

        self.save_notice_label = tk.Label(self.settings_window, text="", font=self.button_font, bg="#000001", fg="#fef08a")
        self.save_notice_label.place(x=18, y=338)
        tk.Label(self.settings_window, text=f"Made by {AUTHOR_NAME}", font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#b45309").place(x=18, y=358)
        tk.Label(self.settings_window, text=APP_VERSION, font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#b45309").place(x=132, y=358)
        tk.Label(self.settings_window, text=f"留덉?留??묒뾽?쇱옄: {LAST_UPDATED}", font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#7c3aed").place(x=18, y=378)

    def apply_settings(self) -> None:
        if self.font_family_var.get() in self.available_font_families:
            self._apply_font_family(self.font_family_var.get())
        self.background_alignment = self.background_alignment_var.get()
        self.show_alert_overlay = self.show_alert_overlay_var.get()
        self.show_alert_percent = self.show_alert_percent_var.get()
        self.show_hodulgap_banner = self.show_hodulgap_banner_var.get()
        self._apply_background(self.settings_path_var.get().strip())
        self._draw_alert_banner()
        self._draw_progress_graph(self.current_percent)
        self._update_window_positions()
        self._save_settings()
        if self.settings_notice_after_id is not None and hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.after_cancel(self.settings_notice_after_id)
        self.settings_notice_end_time = time.perf_counter() + 3.0
        self._flash_save_notice()

    def select_background_file(self) -> None:
        path = filedialog.askopenfilename(
            title="諛곌꼍 ?대?吏 ?좏깮",
            initialdir=self._get_background_dialog_dir(),
            filetypes=[("Image Files", "*.png *.gif *.ppm *.pgm"), ("All Files", "*.*")],
        )
        if path:
            self.settings_path_var.set(path)
            self._apply_background(path)

    def apply_default_background(self) -> None:
        self.settings_path_var.set(DEFAULT_BG_KEY)
        self._apply_background(DEFAULT_BG_KEY)

    def apply_blue_wallpaper(self) -> None:
        self.settings_path_var.set(ALT_BG_KEY)
        self._apply_background(ALT_BG_KEY)

    def apply_jang_wonyoung_background(self) -> None:
        self.settings_path_var.set(JANG_WONYOUNG_BG_KEY)
        self._apply_background(JANG_WONYOUNG_BG_KEY)

    def apply_background_alignment(self) -> None:
        self.background_alignment = self.background_alignment_var.get()
        self._apply_background(self.background_path, update_setting_var=False)

    def _update_window_positions(self) -> None:
        self.root.update_idletasks()
        self.main_window_x = self.root.winfo_x()
        self.main_window_y = self.root.winfo_y()
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.update_idletasks()
            self.settings_window_x = self.settings_window.winfo_x()
            self.settings_window_y = self.settings_window.winfo_y()

    def _alert_point(self, x: float, y: float) -> tuple[float, float]:
        return ALERT_AREA_X + x, ALERT_AREA_Y + y

    def _graph_point(self, x: float, y: float) -> tuple[float, float]:
        return GRAPH_AREA_X + x, GRAPH_AREA_Y + y

    def _reset_effects(self) -> None:
        self.blink_90_active = False
        self.blink_90_end_time = 0.0
        self.blink_kill_active = False
        self.blink_kill_end_time = 0.0
        self.bg_canvas.delete(ALERT_TAG)
        self.bg_canvas.delete(GRAPH_TAG)

    def _draw_alert_banner(self) -> None:
        self.bg_canvas.delete(ALERT_TAG)
        if not self.show_alert_overlay:
            return
        if self.reached_70_display_seconds is None:
            self._draw_sleepy_pomeranian(45, 78)
            self.bg_canvas.create_oval(160, 307, 238, 333, fill="#f8fafc", outline="#cbd5e1", width=2, tags=ALERT_TAG)
            self.bg_canvas.create_text(199, 320, text="천천히...", fill="#475569", font=self.alert_font, tags=ALERT_TAG)
            self.bg_canvas.tag_raise(ALERT_TAG, self.background_item)
            return
        if self.current_percent is not None and self.current_percent >= 94.0:
            speech = "洹몃깷 鍮〓뵜??"
        elif self.current_percent is not None and self.current_percent >= 90.0:
            speech = "鍮〓뵜 以鍮?!"
        elif self.current_percent is not None and self.current_percent >= 85.0:
            speech = "85% ?뚰뙆!!"
        elif self.current_percent is not None and self.current_percent >= 80.0:
            speech = "80% ?뚰뙆!"
        else:
            speech = "愿??녹뼱~!"
        self._draw_sleepy_pomeranian(80, 33)
        self.bg_canvas.create_oval(160, 307, 292, 335, fill="#f8fafc", outline="#cbd5e1", width=2, tags=ALERT_TAG)
        self.bg_canvas.create_text(226, 321, text=speech, fill="#475569", font=self.alert_font, tags=ALERT_TAG)
        self._draw_percent_burst(344, 33, self.current_percent or 70.0)
        self.bg_canvas.tag_raise(ALERT_TAG, self.background_item)

    def _draw_sleepy_pomeranian(self, x: float, y: float) -> None:
        c = self.bg_canvas
        x, y = self._alert_point(x, y)
        c.create_polygon(x - 14, y - 10, x - 6, y - 22, x - 2, y - 6, fill="#fff7ed", outline="#d6d3d1", width=2, tags=ALERT_TAG)
        c.create_polygon(x + 14, y - 10, x + 6, y - 22, x + 2, y - 6, fill="#fff7ed", outline="#d6d3d1", width=2, tags=ALERT_TAG)
        c.create_oval(x - 18, y - 16, x + 18, y + 16, fill="#ffffff", outline="#d6d3d1", width=2, tags=ALERT_TAG)
        c.create_line(x - 8, y - 1, x - 3, y - 4, fill="#475569", width=2, tags=ALERT_TAG)
        c.create_line(x + 3, y - 4, x + 8, y - 1, fill="#475569", width=2, tags=ALERT_TAG)
        c.create_oval(x - 3, y + 3, x + 3, y + 8, fill="#111827", outline="", tags=ALERT_TAG)
        c.create_arc(x - 8, y + 5, x + 8, y + 14, start=200, extent=140, style="arc", outline="#64748b", width=2, tags=ALERT_TAG)
        c.create_text(x + 24, y - 18, text="z", fill="#93c5fd", font=self.percent_font, tags=ALERT_TAG)
        c.create_text(x + 33, y - 24, text="z", fill="#bfdbfe", font=self.percent_font, tags=ALERT_TAG)

    def _draw_percent_burst(self, x: float, y: float, percent: float) -> None:
        c = self.bg_canvas
        x, y = self._alert_point(x, y)
        fill1 = "#fef08a"
        fill2 = "#facc15"
        if percent >= 95.0:
            fill1, fill2 = ("#fef9c3", "#fde047") if int(time.perf_counter() * 6) % 2 == 0 else ("#facc15", "#eab308")
        c.create_polygon(x - 62, y - 20, x + 50, y - 24, x + 56, y + 10, x - 54, y + 16, fill=fill1, outline="", tags=ALERT_TAG)
        c.create_polygon(x - 56, y - 14, x + 54, y - 18, x + 48, y + 16, x - 62, y + 8, fill=fill2, outline="", tags=ALERT_TAG)
        c.create_line(x - 48, y - 24, x - 20, y - 30, fill="#ca8a04", width=3, tags=ALERT_TAG)
        c.create_line(x + 34, y + 18, x + 50, y + 24, fill="#ca8a04", width=3, tags=ALERT_TAG)
        c.create_text(x, y, text=f"{max(70.0, min(100.0, percent)):04.1f}%", fill="#dc2626", font=self.burst_font, tags=ALERT_TAG)

    def _draw_progress_graph(self, current_percent: float | None) -> None:
        canvas = self.bg_canvas
        canvas.delete(GRAPH_TAG)
        left, right, top, bottom = 18, 395, 32, 48
        width = right - left
        x1, y1 = self._graph_point(left, top - 14)
        x2, y2 = self._graph_point(right, top - 14)
        canvas.create_line(x1, y1, x2, y2, fill="#bfdbfe", width=1, tags=GRAPH_TAG)
        x1, y1 = self._graph_point(left, top)
        x2, y2 = self._graph_point(right, bottom)
        canvas.create_rectangle(x1, y1, x2, y2, fill="#dbeafe", outline="#60a5fa", width=2, tags=GRAPH_TAG)
        for index, label in enumerate([70, 80, 90, 100]):
            x = left + (width * index / 3)
            x1, y1 = self._graph_point(x, top - 8)
            x2, y2 = self._graph_point(x, bottom + 8)
            canvas.create_line(x1, y1, x2, y2, fill="#60a5fa", width=1, tags=GRAPH_TAG)
            tx, ty = self._graph_point(x, bottom + 14)
            canvas.create_text(tx, ty, text=f"{label}%", fill="#1e3a8a", font=self.percent_font, tags=GRAPH_TAG)
        if current_percent is None:
            tx, ty = self._graph_point(154, 10)
            canvas.create_text(tx, ty, text="?덉긽 ?쒓컙:", fill="#7c3aed", font=self.header_font, tags=GRAPH_TAG)
            tx, ty = self._graph_point(258, 10)
            canvas.create_text(tx, ty, text="--:--:--", fill="#1e3a8a", font=self.header_font, tags=GRAPH_TAG)
            canvas.tag_raise(GRAPH_TAG, self.background_item)
            return
        clamped_percent = max(70.0, min(100.0, current_percent))
        progress_ratio = (clamped_percent - 70.0) / 30.0
        progress_x = left + (width * progress_ratio)
        cut_expected_total = self._get_cut_expected_total_seconds()
        if cut_expected_total is not None:
            tx, ty = self._graph_point(154, 10)
            canvas.create_text(tx, ty, text="?덉긽 ?쒓컙:", fill="#7c3aed", font=self.header_font, tags=GRAPH_TAG)
            tx, ty = self._graph_point(258, 10)
            canvas.create_text(tx, ty, text=format_seconds(cut_expected_total, show_centiseconds=True), fill="#1e3a8a", font=self.header_font, tags=GRAPH_TAG)
        x1, y1 = self._graph_point(left, top)
        x2, y2 = self._graph_point(progress_x, bottom)
        if clamped_percent >= 90.0:
            progress_fill = "#dc2626"
        elif clamped_percent >= 80.0:
            progress_fill = "#f59e0b"
        else:
            progress_fill = "#fde047"
        canvas.create_rectangle(x1, y1, x2, y2, fill=progress_fill, outline="", tags=GRAPH_TAG)
        self._draw_dog_icon(progress_x, top + 2)
        if clamped_percent >= 90.0:
            self._draw_urgent_effect(progress_x, top + 2, clamped_percent >= 100.0)
        text_x = min(progress_x + 28, right - 16)
        tx, ty = self._graph_point(text_x, top - 12)
        canvas.create_text(tx, ty, text=f"{clamped_percent:04.1f}%", fill="#1e3a8a", font=self.percent_font, tags=GRAPH_TAG)
        self._draw_small_banner(clamped_percent)
        canvas.tag_raise(GRAPH_TAG, self.background_item)

    def _draw_dog_icon(self, center_x: float, base_y: float) -> None:
        canvas = self.bg_canvas
        center_x, base_y = self._graph_point(center_x, base_y)
        head_left = center_x - 11
        head_top = base_y - 22
        head_right = center_x + 11
        head_bottom = base_y
        canvas.create_polygon(center_x - 9, head_top + 6, center_x - 2, head_top - 4, center_x + 1, head_top + 7, fill="#fff7ed", outline="#d6d3d1", width=1, tags=GRAPH_TAG)
        canvas.create_polygon(center_x + 9, head_top + 6, center_x + 2, head_top - 4, center_x - 1, head_top + 7, fill="#fff7ed", outline="#d6d3d1", width=1, tags=GRAPH_TAG)
        canvas.create_oval(head_left, head_top, head_right, head_bottom, fill="#ffffff", outline="#d6d3d1", width=2, tags=GRAPH_TAG)
        canvas.create_oval(center_x - 6, head_top + 8, center_x - 3, head_top + 10, fill="#111827", outline="", tags=GRAPH_TAG)
        canvas.create_oval(center_x + 3, head_top + 8, center_x + 6, head_top + 10, fill="#111827", outline="", tags=GRAPH_TAG)
        canvas.create_oval(center_x - 3, head_top + 13, center_x + 3, head_top + 17, fill="#111827", outline="", tags=GRAPH_TAG)
        canvas.create_arc(center_x - 5, head_top + 13, center_x + 5, head_top + 21, start=200, extent=140, style="arc", outline="#64748b", width=2, tags=GRAPH_TAG)

    def _draw_urgent_effect(self, center_x: float, base_y: float, maxed: bool) -> None:
        canvas = self.bg_canvas
        center_x, base_y = self._graph_point(center_x, base_y)
        effect_color = "#facc15" if not maxed else "#fb7185"
        radius = 16 if not maxed else 20
        for angle in range(0, 360, 45):
            dx = radius * math.cos(math.radians(angle))
            dy = radius * math.sin(math.radians(angle))
            canvas.create_line(center_x + dx * 0.7, base_y - 12 + dy * 0.7, center_x + dx, base_y - 12 + dy, fill=effect_color, width=2, tags=GRAPH_TAG)

    def _draw_small_banner(self, percent: float) -> None:
        if not self.show_hodulgap_banner:
            return
        if percent < 90.0:
            return
        pulse = int(time.perf_counter() * 6) % 3
        if percent >= 100.0:
            bg_colors = ["#7f1d1d", "#991b1b", "#b91c1c"]
            fg_colors = ["#fef08a", "#ffffff", "#fde68a"]
            text = "빡딜~!!"
        elif percent >= 95.0:
            bg_colors = ["#9a3412", "#c2410c", "#ea580c"]
            fg_colors = ["#fff7ed", "#ffffff", "#ffedd5"]
            text = "빡딜!!"
        else:
            bg_colors = ["#991b1b", "#b91c1c", "#dc2626"]
            fg_colors = ["#fff7ed", "#ffffff", "#fef2f2"]
            text = "보스 딜 준비!!"
        canvas = self.bg_canvas
        x1, y1 = self._graph_point(232, 98)
        x2, y2 = self._graph_point(384, 118)
        canvas.create_rectangle(x1, y1, x2, y2, fill=bg_colors[pulse], outline="", width=0, tags=GRAPH_TAG)
        tx, ty = self._graph_point(308, 108)
        canvas.create_text(tx, ty, text=text, fill=fg_colors[pulse], font=self.banner_font, tags=GRAPH_TAG)

    def _draw_progress_graph(self, current_percent: float | None) -> None:
        canvas = self.bg_canvas
        canvas.delete(GRAPH_TAG)
        left, right, top, bottom = 18, 395, 36, 44
        width = right - left
        clamped_percent = max(70.0, min(100.0, current_percent)) if current_percent is not None else None
        x1, y1 = self._graph_point(left, top)
        x2, y2 = self._graph_point(right, bottom)
        canvas.create_rectangle(x1, y1, x2, y2, fill="#dbeafe", outline="#60a5fa", width=2, tags=GRAPH_TAG)
        for index, label in enumerate([70, 80, 90, 100]):
            x = left + (width * index / 3)
            x1, y1 = self._graph_point(x, top - 6)
            x2, y2 = self._graph_point(x, bottom + 8)
            canvas.create_line(x1, y1, x2, y2, fill="#60a5fa", width=1, tags=GRAPH_TAG)
            brush_x, brush_y = self._graph_point(int(x - 24), bottom + 4)
            brush_color = "#bfdbfe"
            text_color = "#1e3a8a"
            if clamped_percent is not None and clamped_percent >= label:
                if label == 70:
                    brush_color = "#fde68a"
                    text_color = "#92400e"
                elif label == 80:
                    brush_color = "#fdba74"
                    text_color = "#9a3412"
                elif label == 90:
                    brush_color = "#fca5a5"
                    text_color = "#991b1b"
                else:
                    brush_color = "#f9a8d4"
                    text_color = "#9d174d"
            self._draw_brush_stroke(canvas, int(brush_x), int(brush_y), 48, 18, brush_color, tags=GRAPH_TAG)
            tx, ty = self._graph_point(x, bottom + 14)
            canvas.create_text(tx, ty, text=f"{label}%", fill=text_color, font=self.percent_font, tags=GRAPH_TAG)
        if self.reached_70_display_seconds is not None:
            expected_value = "--:--:--"
            cut_expected_total = self._get_cut_expected_total_seconds()
            if cut_expected_total is not None:
                expected_value = format_seconds(cut_expected_total, show_centiseconds=True)
            self._draw_brush_stroke(canvas, 140, 10, 102, 24, "#f59e0b", tags=GRAPH_TAG)
            self._draw_brush_stroke(canvas, 244, 10, 104, 24, "#fde68a", tags=GRAPH_TAG)
            canvas.create_text(196, 22, text="而??덉긽:", fill="#ffffff", font=self.header_font, tags=GRAPH_TAG)
            canvas.create_text(296, 22, text=expected_value, fill="#1e3a8a", font=self.header_font, tags=GRAPH_TAG)
        if current_percent is None:
            canvas.tag_raise(GRAPH_TAG, self.background_item)
            return
        progress_ratio = (clamped_percent - 70.0) / 30.0
        progress_x = left + (width * progress_ratio)
        x1, y1 = self._graph_point(left, top)
        x2, y2 = self._graph_point(progress_x, bottom)
        if clamped_percent >= 90.0:
            progress_fill = "#dc2626"
        elif clamped_percent >= 80.0:
            progress_fill = "#f59e0b"
        else:
            progress_fill = "#fde047"
        canvas.create_rectangle(x1, y1, x2, y2, fill=progress_fill, outline="", tags=GRAPH_TAG)
        self._draw_dog_icon(progress_x, top + 2)
        if clamped_percent >= 90.0:
            self._draw_urgent_effect(progress_x, top + 2, clamped_percent >= 100.0)
        self._draw_small_banner(clamped_percent)
        canvas.tag_raise(GRAPH_TAG, self.background_item)

    def _draw_alert_banner(self) -> None:
        self.bg_canvas.delete(ALERT_TAG)
        if not self.show_alert_overlay and not self.show_alert_percent:
            return
        if self.reached_70_display_seconds is None:
            if not self.show_alert_overlay:
                return
            self._draw_sleepy_pomeranian(45, 78)
            self.bg_canvas.create_oval(145, 342, 223, 368, fill="#f8fafc", outline="#cbd5e1", width=2, tags=ALERT_TAG)
            self.bg_canvas.create_text(199, 320, text="천천히...", fill="#475569", font=self.alert_font, tags=ALERT_TAG)
            self.bg_canvas.tag_raise(ALERT_TAG, self.background_item)
            return
        if self.current_percent is not None and self.current_percent >= 94.0:
            speech = "洹몃깷 鍮〓뵜 ??!!"
        elif self.current_percent is not None and self.current_percent >= 90.0:
            speech = "鍮〓뵜 以鍮?!"
        elif self.current_percent is not None and self.current_percent >= 85.0:
            speech = "85% ?뚰뙆!!"
        elif self.current_percent is not None and self.current_percent >= 80.0:
            speech = "80% ?뚰뙆!"
        else:
            speech = "愿??댁뼱??!"
        if self.show_alert_overlay:
            self._draw_sleepy_pomeranian(45, 78)
            self.bg_canvas.create_oval(145, 342, 277, 370, fill="#f8fafc", outline="#cbd5e1", width=2, tags=ALERT_TAG)
            self.bg_canvas.create_text(226, 321, text=speech, fill="#475569", font=self.alert_font, tags=ALERT_TAG)
        if self.show_alert_percent:
            self._draw_percent_burst(344, 33, self.current_percent or 70.0)
        self.bg_canvas.tag_raise(ALERT_TAG, self.background_item)

    def _apply_background(self, path: str, update_setting_var: bool = True) -> None:
        source, resolved_path = self._resolve_background_path(path)
        if not os.path.exists(resolved_path):
            if not self._is_builtin_background(source):
                messagebox.showerror("諛곌꼍 ?ㅻ쪟", "?좏깮???대?吏 ?뚯씪??李얠쓣 ???놁뒿?덈떎.")
            source = DEFAULT_BG_KEY
            resolved_path = get_builtin_background_path(DEFAULT_BG_KEY)
        try:
            image = tk.PhotoImage(file=resolved_path)
        except tk.TclError:
            if not self._is_builtin_background(source):
                messagebox.showerror("諛곌꼍 ?ㅻ쪟", "吏?먰븯吏 ?딅뒗 ?대?吏 ?뺤떇?낅땲??\nPNG, GIF, PPM, PGM ?뚯씪???ъ슜??二쇱꽭??")
                source = DEFAULT_BG_KEY
                resolved_path = get_builtin_background_path(DEFAULT_BG_KEY)
                image = tk.PhotoImage(file=resolved_path)
            else:
                raise
        self.background_image = image
        self.background_path = source
        self.bg_canvas.itemconfig(self.background_item, image=self.background_image)
        if self.background_alignment == "nw":
            self.bg_canvas.itemconfig(self.background_item, anchor="nw")
            self.bg_canvas.coords(self.background_item, 0, 0)
        else:
            self.bg_canvas.itemconfig(self.background_item, anchor="center")
            self.bg_canvas.coords(self.background_item, WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2)
        self.bg_canvas.tag_lower(self.background_item)
        if hasattr(self, "settings_bg_label") and self.settings_bg_label.winfo_exists():
            self.settings_bg_label.config(image=self.background_image)
        if update_setting_var:
            self.settings_path_var.set(source)

    def open_settings_window(self) -> None:
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.focus_force()
            return
        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title("?섍꼍?ㅼ젙")
        self.settings_window.geometry(f"430x470+{self.settings_window_x}+{self.settings_window_y}")
        self.settings_window.resizable(False, False)
        self.settings_window.protocol("WM_DELETE_WINDOW", self.close_settings_window)
        self.settings_bg_label = tk.Label(self.settings_window, bd=0)
        self.settings_bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        if self.background_image is not None:
            self.settings_bg_label.config(image=self.background_image)

        tk.Label(self.settings_window, text="?섍꼍?ㅼ젙", font=self.header_font, bg="#000001", fg="#f8fafc").place(x=18, y=14)
        tk.Label(self.settings_window, text="諛곌꼍 ?대?吏", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=48)
        tk.Entry(self.settings_window, textvariable=self.settings_path_var, font=(self.current_font_family, 10), width=33, bd=0, highlightthickness=0).place(x=18, y=76, width=276, height=26)
        tk.Button(self.settings_window, text="?뚯씪 ?좏깮", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.select_background_file, cursor="hand2").place(x=304, y=74, width=98, height=28)
        tk.Button(self.settings_window, text="湲곕낯諛곌꼍", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_default_background, cursor="hand2").place(x=18, y=106, width=86, height=28)
        tk.Button(self.settings_window, text="踰쎌?", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_blue_wallpaper, cursor="hand2").place(x=112, y=106, width=86, height=28)
        tk.Button(self.settings_window, text="장원영", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_jang_wonyoung_background, cursor="hand2").place(x=206, y=106, width=86, height=28)
        tk.Label(self.settings_window, text="배경 정렬", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=142)
        tk.Radiobutton(self.settings_window, text="좌상단", value="nw", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=18, y=168)
        tk.Radiobutton(self.settings_window, text="중앙", value="center", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=104, y=168)
        tk.Checkbutton(self.settings_window, text="말풍선 표시", variable=self.show_alert_overlay_var, command=self.apply_alert_overlay_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=202)
        tk.Checkbutton(self.settings_window, text="퍼센트 표시", variable=self.show_alert_percent_var, command=self.apply_alert_percent_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=226)
        tk.Checkbutton(self.settings_window, text="호들갑 오더 배너", variable=self.show_hodulgap_banner_var, command=self.apply_hodulgap_banner_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=250)
        tk.Checkbutton(self.settings_window, text="스톱워치 붓 배경 사용", variable=self.show_elapsed_brush_var, command=self.apply_elapsed_brush_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=274)
        tk.Label(self.settings_window, text="스톱워치 배경색", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=300)
        self.elapsed_brush_color_menu = tk.OptionMenu(self.settings_window, self.elapsed_brush_color_var, *ELAPSED_BRUSH_COLORS.keys(), command=self.apply_elapsed_brush_setting)
        self.elapsed_brush_color_menu.config(font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", highlightthickness=0, bd=0)
        self.elapsed_brush_color_menu["menu"].config(font=(self.current_font_family, 9), bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18")
        self.elapsed_brush_color_menu.place(x=18, y=324, width=130, height=30)
        tk.Label(self.settings_window, text="폰트", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=360)
        self.font_menu = tk.OptionMenu(self.settings_window, self.font_family_var, *self.available_font_families)
        self.font_menu.config(font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", highlightthickness=0, bd=0)
        self.font_menu["menu"].config(font=(self.current_font_family, 9), bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18")
        self.font_menu.place(x=18, y=384, width=276, height=30)
        self.apply_button = tk.Button(self.settings_window, text="저장", font=self.button_font, bg="#2563eb", fg="white", activebackground="#1d4ed8", activeforeground="white", relief="flat", bd=0, highlightthickness=0, command=self.apply_settings, cursor="hand2")
        self.apply_button.place(x=304, y=432, width=98, height=30)
        self.save_notice_label = tk.Label(self.settings_window, text="", font=self.button_font, bg="#000001", fg="#fef08a")
        self.save_notice_label.place(x=18, y=404)
        tk.Label(self.settings_window, text=f"Made by {AUTHOR_NAME}", font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#b45309").place(x=18, y=422)
        tk.Label(self.settings_window, text=APP_VERSION, font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#b45309").place(x=132, y=422)
        tk.Label(self.settings_window, text=f"留덉?留??묒뾽?쇱옄: {LAST_UPDATED}", font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#7c3aed").place(x=18, y=460)

    def apply_settings(self) -> None:
        if self.font_family_var.get() in self.available_font_families:
            self._apply_font_family(self.font_family_var.get())
        self.background_alignment = self.background_alignment_var.get()
        self.show_alert_overlay = self.show_alert_overlay_var.get()
        self.show_alert_percent = self.show_alert_percent_var.get()
        self.show_hodulgap_banner = self.show_hodulgap_banner_var.get()
        if self.elapsed_brush_color_var.get() in ELAPSED_BRUSH_COLORS:
            self.elapsed_brush_color_name = self.elapsed_brush_color_var.get()
        self.show_elapsed_brush = self.elapsed_brush_color_name != "배경 없음"
        self._apply_background(self.settings_path_var.get().strip())
        self._draw_elapsed_brush()
        self.bg_canvas.tag_raise(self.elapsed_shadow_item)
        self.bg_canvas.tag_raise(self.elapsed_text_item)
        self._draw_alert_banner()
        self._draw_progress_graph(self.current_percent)
        self._update_window_positions()
        self._save_settings()
        if self.settings_notice_after_id is not None and hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.after_cancel(self.settings_notice_after_id)
        self.settings_notice_end_time = time.perf_counter() + 3.0
        self._flash_save_notice()

    def open_settings_window(self) -> None:
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            if str(self.settings_window.state()) == "iconic":
                self.settings_window.deiconify()
            self.settings_window.lift()
            self.settings_window.focus_force()
            return
        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title("?섍꼍?ㅼ젙")
        self.settings_window.geometry(f"430x470+{self.settings_window_x}+{self.settings_window_y}")
        self.settings_window.resizable(False, False)
        self.settings_window.protocol("WM_DELETE_WINDOW", self.close_settings_window)
        self.settings_bg_label = tk.Label(self.settings_window, bd=0)
        self.settings_bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        if self.background_image is not None:
            self.settings_bg_label.config(image=self.background_image)

        tk.Label(self.settings_window, text="?섍꼍?ㅼ젙", font=self.header_font, bg="#000001", fg="#f8fafc").place(x=18, y=14)
        tk.Label(self.settings_window, text="諛곌꼍 ?대?吏", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=48)
        tk.Entry(self.settings_window, textvariable=self.settings_path_var, font=(self.current_font_family, 10), width=33, bd=0, highlightthickness=0).place(x=18, y=76, width=276, height=26)
        tk.Button(self.settings_window, text="?뚯씪 ?좏깮", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.select_background_file, cursor="hand2").place(x=304, y=74, width=98, height=28)
        tk.Button(self.settings_window, text="湲곕낯諛곌꼍", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_default_background, cursor="hand2").place(x=18, y=106, width=86, height=28)
        tk.Button(self.settings_window, text="踰쎌?", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_blue_wallpaper, cursor="hand2").place(x=112, y=106, width=86, height=28)
        tk.Button(self.settings_window, text="장원영", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_jang_wonyoung_background, cursor="hand2").place(x=206, y=106, width=86, height=28)
        tk.Label(self.settings_window, text="諛곌꼍 ?뺣젹", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=142)
        tk.Radiobutton(self.settings_window, text="좌상단", value="nw", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=18, y=168)
        tk.Radiobutton(self.settings_window, text="以묒븰", value="center", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=104, y=168)
        tk.Checkbutton(self.settings_window, text="媛뺤븘吏/留먰뭾???쒖떆", variable=self.show_alert_overlay_var, command=self.apply_alert_overlay_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=202)
        tk.Checkbutton(self.settings_window, text="?쇱꽱???쒖떆", variable=self.show_alert_percent_var, command=self.apply_alert_percent_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=226)
        tk.Checkbutton(self.settings_window, text="?몃뱾媛??ㅻ뜑 諛곕꼫", variable=self.show_hodulgap_banner_var, command=self.apply_hodulgap_banner_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=250)
        tk.Checkbutton(self.settings_window, text="?ㅽ넲?뚯튂 遺?諛곌꼍 ?ъ슜", variable=self.show_elapsed_brush_var, command=self.apply_elapsed_brush_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=274)
        tk.Label(self.settings_window, text="스톱워치 배경색", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=300)
        self.elapsed_brush_color_menu = tk.OptionMenu(self.settings_window, self.elapsed_brush_color_var, *ELAPSED_BRUSH_COLORS.keys(), command=self.apply_elapsed_brush_setting)
        self.elapsed_brush_color_menu.config(font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", highlightthickness=0, bd=0)
        self.elapsed_brush_color_menu["menu"].config(font=(self.current_font_family, 9), bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18")
        self.elapsed_brush_color_menu.place(x=18, y=324, width=130, height=30)
        tk.Label(self.settings_window, text="?고듃", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=360)
        self.font_menu = tk.OptionMenu(self.settings_window, self.font_family_var, *self.available_font_families)
        self.font_menu.config(font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", highlightthickness=0, bd=0)
        self.font_menu["menu"].config(font=(self.current_font_family, 9), bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18")
        self.font_menu.place(x=18, y=384, width=276, height=30)
        self.apply_button = tk.Button(self.settings_window, text="저장", font=self.button_font, bg="#2563eb", fg="white", activebackground="#1d4ed8", activeforeground="white", relief="flat", bd=0, highlightthickness=0, command=self.apply_settings, cursor="hand2")
        self.apply_button.place(x=304, y=432, width=98, height=30)
        self.save_notice_label = tk.Label(self.settings_window, text="", font=self.button_font, bg="#f8f1df", fg="#b45309")
        tk.Label(self.settings_window, text=f"Made by {AUTHOR_NAME}", font=(self.current_font_family, 10, "bold"), bg="#f8f1df", fg="#b45309").place(x=18, y=422)
        tk.Label(self.settings_window, text=APP_VERSION, font=(self.current_font_family, 10, "bold"), bg="#f8f1df", fg="#b45309").place(x=132, y=422)
        tk.Label(self.settings_window, text=f"마지막 작업일자: {LAST_UPDATED}", font=(self.current_font_family, 10, "bold"), bg="#f8f1df", fg="#7c3aed").place(x=18, y=440)


    def _parse_elapsed_input(self, raw_value: str) -> float | None:
        value = (raw_value or "").strip()
        parts = value.split(":")
        if len(parts) != 2:
            return None
        try:
            minutes = int(parts[0])
            seconds = int(parts[1])
        except ValueError:
            return None
        if seconds >= 60:
            return None
        return max(0.0, minutes * 60 + seconds)

    def _on_total_label_click(self, _event=None) -> None:
        self.bg_canvas.config(cursor="")
        was_running = self.running
        if was_running:
            self.stop_timer()
        try:
            initial_value = format_seconds(self._now_elapsed())
            entered_value = self._prompt_total_time_input(initial_value)
            if entered_value is None or entered_value == initial_value:
                return
            parsed_seconds = self._parse_elapsed_input(entered_value)
            if parsed_seconds is None:
                messagebox.showerror("?낅젰 ?ㅻ쪟", "?뺤떇? MM:SS留??낅젰?????덉뒿?덈떎.")
                return
            self._apply_initial_elapsed_seconds(parsed_seconds)
        except Exception as exc:
            messagebox.showerror("珥??쒓컙 ?ㅼ젙 ?ㅻ쪟", str(exc))
        finally:
            if was_running and not self.running:
                self.start_timer()

    def _draw_progress_graph(self, current_percent: float | None) -> None:
        canvas = self.bg_canvas
        canvas.delete(GRAPH_TAG)
        left, top, right, bottom = 28, 58, 392, 70
        width = right - left
        clamped_percent = 70.0 if current_percent is None else max(70.0, min(100.0, current_percent))
        x1, y1 = self._graph_point(left, top)
        x2, y2 = self._graph_point(right, bottom)
        canvas.create_rectangle(x1, y1, x2, y2, fill="#dbeafe", outline="#60a5fa", width=1, tags=GRAPH_TAG)
        for percent, label in [(70, "70"), (80, "80"), (90, "90"), (100, "100")]:
            x = left + ((percent - 70) / 30) * width
            brush_x, brush_y = self._graph_point(x - 24, bottom + 5)
            brush_color = "#dbeafe"
            text_color = "#1d4ed8"
            if current_percent is not None and clamped_percent >= percent:
                if percent == 70:
                    brush_color = "#fde68a"
                    text_color = "#92400e"
                elif percent == 80:
                    brush_color = "#fdba74"
                    text_color = "#9a3412"
                elif percent == 90:
                    brush_color = "#fca5a5"
                    text_color = "#991b1b"
                else:
                    brush_color = "#f9a8d4"
                    text_color = "#9d174d"
            self._draw_brush_stroke(canvas, int(brush_x), int(brush_y), 48, 18, brush_color, tags=GRAPH_TAG)
            tx, ty = self._graph_point(x, bottom + 14)
            canvas.create_text(tx, ty, text=f"{label}%", fill=text_color, font=self.percent_font, tags=GRAPH_TAG)
        if self.reached_70_display_seconds is not None:
            expected_value = "--:--:--"
            cut_expected_total = self._get_cut_expected_total_seconds()
            if cut_expected_total is not None:
                expected_value = format_seconds(cut_expected_total, show_centiseconds=True)
            self._draw_header_brush(canvas, 10, "#f59e0b", tags=GRAPH_TAG)
            self._draw_brush_stroke(canvas, 122, 11, 176, 34, "#fde68a", tags=GRAPH_TAG)
            canvas.create_text(33, 26, anchor="w", text="컷 예상", fill="#ffffff", font=self.header_font, tags=GRAPH_TAG)
            canvas.create_text(138, 28, anchor="w", text=expected_value, fill="#1e3a8a", font=self.expected_value_font, tags=GRAPH_TAG)
        if current_percent is None:
            canvas.tag_raise(GRAPH_TAG, self.background_item)
            return
        progress_ratio = (clamped_percent - 70.0) / 30.0
        progress_x = left + (width * progress_ratio)
        x1, y1 = self._graph_point(left, top)
        x2, y2 = self._graph_point(progress_x, bottom)
        if clamped_percent >= 90.0:
            progress_fill = "#dc2626"
        elif clamped_percent >= 80.0:
            progress_fill = "#f59e0b"
        else:
            progress_fill = "#fde047"
        canvas.create_rectangle(x1, y1, x2, y2, fill=progress_fill, outline="", tags=GRAPH_TAG)
        self._draw_dog_icon(progress_x, top + 2)
        if clamped_percent >= 90.0:
            self._draw_urgent_effect(progress_x, top + 2, clamped_percent >= 100.0)
        self._draw_small_banner(clamped_percent)
        canvas.tag_raise(GRAPH_TAG, self.background_item)

    def _draw_alert_banner(self) -> None:
        self.bg_canvas.delete(ALERT_TAG)
        if not self.show_alert_overlay and not self.show_alert_percent:
            return
        if self.reached_70_display_seconds is None:
            if not self.show_alert_overlay:
                return
            self.bg_canvas.create_oval(145, 342, 223, 368, fill="#f8fafc", outline="#cbd5e1", width=2, tags=ALERT_TAG)
            self.bg_canvas.create_text(184, 355, text="광 체크", fill="#475569", font=self.alert_font, tags=ALERT_TAG)
            self.bg_canvas.tag_raise(ALERT_TAG, self.background_item)
            return
        if self.current_percent is not None and self.current_percent >= 94.0:
            speech = "보스 딜~!!"
        elif self.current_percent is not None and self.current_percent >= 90.0:
            speech = "90%!!!"
        elif self.current_percent is not None and self.current_percent >= 85.0:
            speech = "85%!!"
        elif self.current_percent is not None and self.current_percent >= 80.0:
            speech = "80%!"
        else:
            speech = "광 떴다!"
        if self.show_alert_overlay:
            self.bg_canvas.create_oval(145, 342, 277, 370, fill="#f8fafc", outline="#cbd5e1", width=2, tags=ALERT_TAG)
            self.bg_canvas.create_text(211, 356, text=speech, fill="#475569", font=self.alert_font, tags=ALERT_TAG)
        if self.show_alert_percent:
            self._draw_percent_burst(329, 53, self.current_percent or 70.0)
        self.bg_canvas.tag_raise(ALERT_TAG, self.background_item)

    def _draw_total_time_label(self, brush_color: str = TOTAL_LABEL_BRUSH_NORMAL, text_color: str = TOTAL_LABEL_TEXT_NORMAL) -> None:
        points = [
            18, 55,
            32.72, 50,
            51.12, 56,
            75.04, 51,
            110, 58,
            104, 76,
            78.72, 74,
            52.96, 76,
            26, 73,
        ]
        if not hasattr(self, "total_label_brush_item") or not self.bg_canvas.type(self.total_label_brush_item):
            self.total_label_brush_item = self.bg_canvas.create_polygon(
                points,
                fill=brush_color,
                outline="",
                smooth=True,
                splinesteps=12,
                tags=TOTAL_LABEL_TAG,
            )
            self.total_label_text_item = self.bg_canvas.create_text(
                33, 66, anchor="w", text="총 시간", font=self.header_font, fill=text_color, tags=TOTAL_LABEL_TAG
            )
            self.total_label_hitbox_item = self.bg_canvas.create_rectangle(
                18, 50, 110, 76, fill="", outline="", tags=TOTAL_LABEL_HITBOX_TAG
            )
            self.bg_canvas.tag_bind(self.total_label_hitbox_item, "<Enter>", self._on_total_label_enter)
            self.bg_canvas.tag_bind(self.total_label_hitbox_item, "<Leave>", self._on_total_label_leave)
            self.bg_canvas.tag_bind(self.total_label_hitbox_item, "<Button-1>", self._on_total_label_click)
            self.bg_canvas.tag_raise(self.total_label_hitbox_item)
        else:
            self.bg_canvas.coords(self.total_label_brush_item, *points)
            self.bg_canvas.coords(self.total_label_text_item, 33, 66)
            self.bg_canvas.coords(self.total_label_hitbox_item, 18, 50, 110, 76)
            self.bg_canvas.itemconfig(self.total_label_brush_item, fill=brush_color)
            self.bg_canvas.itemconfig(self.total_label_text_item, text="총 시간", fill=text_color, font=self.header_font)

    def _on_total_label_enter(self, _event=None) -> None:
        self.bg_canvas.config(cursor="hand2")
        if hasattr(self, "total_label_brush_item"):
            self.bg_canvas.itemconfig(self.total_label_brush_item, fill=TOTAL_LABEL_BRUSH_HOVER)
        if hasattr(self, "total_label_text_item"):
            self.bg_canvas.itemconfig(self.total_label_text_item, fill=TOTAL_LABEL_TEXT_HOVER)

    def _on_total_label_leave(self, _event=None) -> None:
        self.bg_canvas.config(cursor="")
        if hasattr(self, "total_label_brush_item"):
            self.bg_canvas.itemconfig(self.total_label_brush_item, fill=TOTAL_LABEL_BRUSH_NORMAL)
        if hasattr(self, "total_label_text_item"):
            self.bg_canvas.itemconfig(self.total_label_text_item, fill=TOTAL_LABEL_TEXT_NORMAL)

    def _draw_record_label(self, brush_color: str = "#2563eb", text_color: str = "#ffffff") -> None:
        top_y = 196
        if not hasattr(self, "record_label_brush_item") or not self.bg_canvas.type(self.record_label_brush_item):
            self.record_label_brush_item = self.bg_canvas.create_polygon(
                18, top_y + 5,
                32.72, top_y,
                51.12, top_y + 6,
                75.04, top_y + 1,
                110, top_y + 8,
                104, top_y + 26,
                78.72, top_y + 24,
                52.96, top_y + 26,
                26, top_y + 23,
                fill=brush_color,
                outline="",
                smooth=True,
                splinesteps=12,
                tags=RECORD_LABEL_TAG,
            )
            self.record_label_text_item = self.bg_canvas.create_text(
                34, 212, anchor="w", text="광 시간", font=self.label_font, fill=text_color, tags=RECORD_LABEL_TAG
            )
            self.record_label_hitbox_item = self.bg_canvas.create_rectangle(
                18, top_y, 110, top_y + 26, fill="", outline="", tags=RECORD_LABEL_HITBOX_TAG
            )
            self.bg_canvas.tag_bind(self.record_label_hitbox_item, "<Enter>", self._on_record_label_enter)
            self.bg_canvas.tag_bind(self.record_label_hitbox_item, "<Leave>", self._on_record_label_leave)
            self.bg_canvas.tag_bind(self.record_label_hitbox_item, "<Button-1>", self._on_record_label_click)
            self.bg_canvas.tag_raise(self.record_label_hitbox_item)
        else:
            self.bg_canvas.itemconfig(self.record_label_brush_item, fill=brush_color)
            self.bg_canvas.itemconfig(self.record_label_text_item, text="광 시간", fill=text_color, font=self.label_font)

    def _on_record_label_enter(self, _event=None) -> None:
        self.bg_canvas.config(cursor="hand2")
        if hasattr(self, "record_label_brush_item"):
            self.bg_canvas.itemconfig(self.record_label_brush_item, fill=TOTAL_LABEL_BRUSH_HOVER)
        if hasattr(self, "record_label_text_item"):
            self.bg_canvas.itemconfig(self.record_label_text_item, fill=TOTAL_LABEL_TEXT_HOVER)

    def _on_record_label_leave(self, _event=None) -> None:
        self.bg_canvas.config(cursor="")
        if hasattr(self, "record_label_brush_item"):
            self.bg_canvas.itemconfig(self.record_label_brush_item, fill="#2563eb")
        if hasattr(self, "record_label_text_item"):
            self.bg_canvas.itemconfig(self.record_label_text_item, fill="#ffffff")

    def _prompt_total_time_input(self, initial_value: str) -> str | None:
        dialog = tk.Toplevel(self.root)
        dialog.title("총 시간 설정")
        dialog.geometry("420x225")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.configure(bg="#2563eb")

        result = {"value": None}
        initial_mmss = initial_value[-5:] if len(initial_value) >= 5 and ":" in initial_value else "00:00"
        value_var = tk.StringVar(value=initial_mmss)

        def move_cursor(position: int) -> None:
            safe_position = max(0, min(5, position))
            if safe_position == 2:
                safe_position += 1
            dialog.after_idle(lambda pos=safe_position: entry.icursor(pos))

        def close_with(value) -> None:
            result["value"] = value
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()

        def submit(_event=None) -> str:
            close_with(value_var.get().strip())
            return "break"

        def cancel(_event=None) -> str:
            close_with(None)
            return "break"

        def handle_keypress(event) -> str | None:
            current = value_var.get()
            if len(current) != 5 or ":" not in current:
                current = "00:00"
            if event.keysym == "Escape":
                return cancel()
            if event.keysym in {"Return", "KP_Enter"}:
                return submit()
            cursor = entry.index(tk.INSERT)
            if cursor == 2 and event.keysym not in {"Left", "KP_Left", "Home"}:
                cursor += 1
            if event.keysym == "Home":
                move_cursor(0)
                return "break"
            if event.keysym in {"Left", "KP_Left"}:
                target = cursor - 1
                if target == 2:
                    target -= 1
                move_cursor(target)
                return "break"
            if event.keysym in {"Right", "KP_Right"}:
                target = cursor + 1
                if target == 2:
                    target += 1
                move_cursor(target)
                return "break"
            if event.keysym == "BackSpace":
                target = cursor - 1
                if target == 2:
                    target -= 1
                if target < 0:
                    target = 0
                chars = list(current)
                chars[target] = "0"
                value_var.set("".join(chars))
                move_cursor(target)
                return "break"
            if event.keysym == "Delete":
                target = cursor if cursor != 2 else cursor + 1
                if target > 4:
                    target = 4
                chars = list(current)
                chars[target] = "0"
                value_var.set("".join(chars))
                move_cursor(target)
                return "break"
            if event.char and event.char.isdigit():
                if entry.selection_present():
                    target = 0
                    entry.selection_clear()
                else:
                    target = cursor if cursor != 2 else cursor + 1
                if target > 4:
                    target = 4
                chars = list(current)
                chars[target] = event.char
                value_var.set("".join(chars))
                move_cursor(target + 1)
                return "break"
            return "break" if event.keysym not in {"Tab"} else None

        label_font = tkfont.Font(family=self.current_font_family, size=17, weight="bold")
        entry_font = tkfont.Font(family=self.current_font_family, size=20, weight="bold")
        button_font = tkfont.Font(family=self.current_font_family, size=14, weight="bold")

        tk.Label(dialog, text="형식: MM:SS", font=label_font, bg="#2563eb", fg="#ffffff").place(x=28, y=26)
        entry = tk.Entry(
            dialog,
            textvariable=value_var,
            font=entry_font,
            justify="center",
            bd=0,
            highlightthickness=0,
            bg="#dbeafe",
            fg="#1e3a8a",
            insertbackground="#1e3a8a",
        )
        entry.place(x=28, y=76, width=364, height=50)
        entry.bind("<KeyPress>", handle_keypress)

        tk.Button(dialog, text="확인", font=button_font, bg="#7dd3fc", fg="#0f172a", activebackground="#38bdf8", activeforeground="#0f172a", relief="flat", bd=0, highlightthickness=0, command=submit, cursor="hand2").place(x=58, y=154, width=132, height=40)
        tk.Button(dialog, text="취소(ESC)", font=button_font, bg="#fef08a", fg="#713f12", activebackground="#fde047", activeforeground="#713f12", relief="flat", bd=0, highlightthickness=0, command=cancel, cursor="hand2").place(x=230, y=154, width=132, height=40)

        dialog.bind("<Escape>", cancel)
        dialog.bind("<Return>", submit)
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.update_idletasks()
        dialog.geometry(f"420x225+{self.root.winfo_x() + 40}+{self.root.winfo_y() + 115}")
        dialog.lift()
        dialog.focus_force()
        entry.focus_set()
        entry.icursor(0)
        dialog.grab_set()
        self.root.wait_window(dialog)
        return result["value"]

    def _on_record_label_click(self, _event=None) -> None:
        self.bg_canvas.config(cursor="")
        try:
            entered_value = self._prompt_total_time_input("00:00")
            if entered_value is None:
                return
            self.reached_70_var.set(f"{entered_value}:00")
            parsed_seconds = self._parse_elapsed_input(entered_value)
            current_elapsed = self._now_elapsed()
            if parsed_seconds is None or current_elapsed < 1.0 or current_elapsed < parsed_seconds + 1.0:
                self.reached_70_calc_seconds = None
                self.reached_70_display_seconds = None
                self.current_percent = None
                self.remain_90_var.set("00:00:00")
                self.remain_kill_var.set("00:00:00")
                self._set_overrun_display("00:00:00")
                self._set_overrun_visibility(False)
                self._reset_effects()
                self._apply_default_boxes()
                self._draw_progress_graph(None)
                self._draw_alert_banner()
                return
            self.reached_70_calc_seconds = int(parsed_seconds)
            self.reached_70_display_seconds = parsed_seconds
            self._reset_effects()
            self._configure_record_button_style("#ffffff", 2, "#7f1d1d")
            self._update_prediction_labels(current_elapsed)
        except Exception as exc:
            messagebox.showerror("광 체크 설정 오류", str(exc))

    def _on_total_label_click(self, _event=None) -> None:
        self.bg_canvas.config(cursor="")
        was_running = self.running
        if was_running:
            self.stop_timer()
        try:
            elapsed_seconds = int(self._now_elapsed())
            initial_value = f"{elapsed_seconds // 60:02d}:{elapsed_seconds % 60:02d}"
            entered_value = self._prompt_total_time_input(initial_value)
            if entered_value is None or entered_value == initial_value:
                return
            parsed_seconds = self._parse_elapsed_input(entered_value)
            if parsed_seconds is None:
                messagebox.showerror("입력 오류", "형식은 MM:SS만 입력할 수 있습니다.")
                return
            self._apply_initial_elapsed_seconds(parsed_seconds)
        except Exception as exc:
            messagebox.showerror("총 시간 설정 오류", str(exc))
        finally:
            if was_running and not self.running:
                self.start_timer()

    def _create_canvas_icon_button(self, icon_key: str, x: int, y: int, command):
        image = self._ensure_button_image(icon_key, "normal")
        tag = f"icon_button_{icon_key}"
        item_id = self.bg_canvas.create_image(x, y, image=image, anchor="center", tags=(tag,))
        hitbox_id = self.bg_canvas.create_rectangle(x - 24, y - 24, x + 24, y + 24, fill="", outline="", tags=(f"{tag}_hitbox",))
        self.bg_canvas.tag_raise(hitbox_id)
        self.canvas_icon_positions[item_id] = (x, y)
        self.canvas_icon_keys[item_id] = icon_key
        self.canvas_icon_hitboxes[item_id] = hitbox_id
        self.bg_canvas.tag_bind(hitbox_id, "<Enter>", lambda event, current_id=item_id: self._hover_canvas_icon_button(current_id))
        self.bg_canvas.tag_bind(hitbox_id, "<Leave>", lambda event, current_id=item_id: self._reset_canvas_icon_button(current_id))
        self.bg_canvas.tag_bind(hitbox_id, "<ButtonPress-1>", lambda event, current_id=item_id: self._press_canvas_icon_button(current_id))
        self.bg_canvas.tag_bind(hitbox_id, "<ButtonRelease-1>", lambda event, current_id=item_id, action=command: self._release_canvas_icon_button(current_id, action, event))
        return item_id

    def _hover_canvas_icon_button(self, item_id: int) -> None:
        self.bg_canvas.config(cursor="hand2")
        icon_key = self.canvas_icon_keys.get(item_id)
        if icon_key is not None:
            self.bg_canvas.itemconfig(item_id, image=self._ensure_button_image(icon_key, "pressed"))

    def _press_canvas_icon_button(self, item_id: int) -> None:
        icon_key = self.canvas_icon_keys.get(item_id)
        if icon_key is not None:
            self.bg_canvas.itemconfig(item_id, image=self._ensure_button_image(icon_key, "pressed"))

    def _release_canvas_icon_button(self, item_id: int, command, event) -> None:
        inside = self._is_canvas_release_inside_item(item_id, event)
        self._reset_canvas_icon_button(item_id)
        if not inside:
            return
        try:
            command()
        except Exception as exc:
            messagebox.showerror("버튼 실행 오류", str(exc))

    def _reset_canvas_icon_button(self, item_id: int) -> None:
        self.bg_canvas.config(cursor="")
        icon_key = self.canvas_icon_keys.get(item_id)
        if icon_key is not None:
            self.bg_canvas.itemconfig(item_id, image=self._ensure_button_image(icon_key, "normal"))
        self._hide_tooltip()

    def _is_canvas_release_inside_item(self, item_id: int, event) -> bool:
        bbox = self.bg_canvas.bbox(item_id)
        if bbox is None:
            return False
        left, top, right, bottom = bbox
        return left <= event.x <= right and top <= event.y <= bottom

    def open_settings_window(self) -> None:
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            if str(self.settings_window.state()) == "iconic":
                self.settings_window.deiconify()
            self.settings_window.lift()
            self.settings_window.focus_force()
            return
        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title("환경설정")
        self.settings_window.geometry(f"430x470+{self.settings_window_x}+{self.settings_window_y}")
        self.settings_window.resizable(False, False)
        self.settings_window.protocol("WM_DELETE_WINDOW", self.close_settings_window)
        self.settings_bg_label = tk.Label(self.settings_window, bd=0)
        self.settings_bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        if self.background_image is not None:
            self.settings_bg_label.config(image=self.background_image)

        tk.Label(self.settings_window, text="환경설정", font=self.header_font, bg="#000001", fg="#f8fafc").place(x=18, y=14)
        tk.Label(self.settings_window, text="배경 이미지", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=48)
        tk.Entry(self.settings_window, textvariable=self.settings_path_var, font=(self.current_font_family, 10), width=33, bd=0, highlightthickness=0).place(x=18, y=76, width=276, height=26)
        tk.Button(self.settings_window, text="파일 선택", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.select_background_file, cursor="hand2").place(x=304, y=74, width=98, height=28)
        tk.Button(self.settings_window, text="기본배경", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_default_background, cursor="hand2").place(x=18, y=106, width=86, height=28)
        tk.Button(self.settings_window, text="벽지", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_blue_wallpaper, cursor="hand2").place(x=112, y=106, width=86, height=28)
        tk.Button(self.settings_window, text="장원영", font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", relief="flat", bd=0, highlightthickness=0, command=self.apply_jang_wonyoung_background, cursor="hand2").place(x=206, y=106, width=86, height=28)
        tk.Label(self.settings_window, text="배경 정렬", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=142)
        tk.Radiobutton(self.settings_window, text="좌상단", value="nw", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=18, y=168)
        tk.Radiobutton(self.settings_window, text="중앙", value="center", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=104, y=168)
        tk.Checkbutton(self.settings_window, text="말풍선 표시", variable=self.show_alert_overlay_var, command=self.apply_alert_overlay_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=202)
        tk.Checkbutton(self.settings_window, text="퍼센트 표시", variable=self.show_alert_percent_var, command=self.apply_alert_percent_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=226)
        tk.Checkbutton(self.settings_window, text="호들갑 오더 배너", variable=self.show_hodulgap_banner_var, command=self.apply_hodulgap_banner_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=250)
        tk.Label(self.settings_window, text="스톱워치 배경색", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=274)
        self.elapsed_brush_color_menu = tk.OptionMenu(self.settings_window, self.elapsed_brush_color_var, *ELAPSED_BRUSH_COLORS.keys(), command=self.apply_elapsed_brush_setting)
        self.elapsed_brush_color_menu.config(font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", highlightthickness=0, bd=0)
        self.elapsed_brush_color_menu["menu"].config(font=(self.current_font_family, 9), bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18")
        self.elapsed_brush_color_menu.place(x=18, y=298, width=130, height=30)
        tk.Label(self.settings_window, text="폰트", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=342)
        self.font_menu = tk.OptionMenu(self.settings_window, self.font_family_var, *self.available_font_families)
        self.font_menu.config(font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", highlightthickness=0, bd=0)
        self.font_menu["menu"].config(font=(self.current_font_family, 9), bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18")
        self.font_menu.place(x=18, y=366, width=276, height=30)
        self.apply_button = tk.Button(self.settings_window, text="저장", font=self.button_font, bg="#2563eb", fg="white", activebackground="#1d4ed8", activeforeground="white", relief="flat", bd=0, highlightthickness=0, command=self.apply_settings, cursor="hand2")
        self.apply_button.place(x=304, y=414, width=98, height=30)
        self.save_notice_label = tk.Label(self.settings_window, text="", font=self.button_font, bg="#f8f1df", fg="#b45309")
        tk.Label(self.settings_window, text=f"Made by {AUTHOR_NAME}", font=(self.current_font_family, 10, "bold"), bg="#f8f1df", fg="#b45309").place(x=18, y=404)
        tk.Label(self.settings_window, text=APP_VERSION, font=(self.current_font_family, 10, "bold"), bg="#f8f1df", fg="#b45309").place(x=132, y=404)
        tk.Label(self.settings_window, text=f"마지막 작업일자: {LAST_UPDATED}", font=(self.current_font_family, 10, "bold"), bg="#f8f1df", fg="#7c3aed").place(x=18, y=422)

    def close_settings_window(self) -> None:
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.update_idletasks()
            self.settings_window_x = self.settings_window.winfo_x()
            self.settings_window_y = self.settings_window.winfo_y()
            self._save_settings()
            self.settings_window.destroy()

    def toggle_log_panel(self) -> None:
        if self.log_panel_open:
            self.close_log_panel()
        else:
            self.open_log_panel()

    def open_log_panel(self) -> None:
        self._ensure_log_panel()
        self.log_panel_open = True
        self._position_log_panel()
        self.log_panel.deiconify()
        self.log_panel.lift()
        self.log_panel.focus_force()
        if hasattr(self, "log_boss_entry"):
            self.log_boss_entry.focus_set()
            schedule_korean_keyboard_activation(self.log_boss_entry)
        self.log_panel_toggle_button.config(text="닫기")
        self._refresh_log_panel()

    def close_log_panel(self) -> None:
        if self.log_panel is not None and self.log_panel.winfo_exists():
            self.log_panel.withdraw()
        self.log_panel_open = False
        if self.log_panel_toggle_button is not None:
            self.log_panel_toggle_button.config(text="로그")

    def toggle_analysis_window(self) -> None:
        if self.analysis_window_open:
            self.close_analysis_window()
        else:
            self.open_analysis_window()

    def open_analysis_window(self) -> None:
        self._ensure_analysis_window()
        self.analysis_window_open = True
        self._position_analysis_window()
        self.analysis_window.deiconify()
        self.analysis_window.lift()
        self.analysis_window.focus_force()
        if hasattr(self, "analysis_window_boss_entry"):
            self.analysis_window_boss_entry.focus_set()
            schedule_korean_keyboard_activation(self.analysis_window_boss_entry)
        self.refresh_analysis_view()

    def close_analysis_window(self) -> None:
        if self.analysis_window is not None and self.analysis_window.winfo_exists():
            self.analysis_window.withdraw()
        self.analysis_window_open = False

    def _position_analysis_window(self) -> None:
        if self.analysis_window is None or not self.analysis_window.winfo_exists() or not self.analysis_window_open:
            return
        if self.log_panel is not None and self.log_panel.winfo_exists() and self.log_panel_open:
            x = self.log_panel.winfo_x() + self.log_panel.winfo_width() - ANALYSIS_WINDOW_WIDTH
            y = self.log_panel.winfo_y() + self.log_panel.winfo_height() + 34
        else:
            x = self.root.winfo_x() + WINDOW_WIDTH + 8
            y = self.root.winfo_y()
        self.analysis_window.geometry(f"{ANALYSIS_WINDOW_WIDTH}x{ANALYSIS_WINDOW_HEIGHT}+{x}+{y}")

    def _position_log_panel(self) -> None:
        if self.log_panel is None or not self.log_panel.winfo_exists() or not self.log_panel_open:
            return
        x = self.root.winfo_x() + WINDOW_WIDTH + 8
        y = self.root.winfo_y()
        self.log_panel.geometry(f"{LOG_PANEL_WIDTH}x{LOG_PANEL_HEIGHT}+{x}+{y}")

    def _ensure_log_panel(self) -> None:
        if self.log_panel is not None and self.log_panel.winfo_exists():
            return
        self.log_panel = tk.Toplevel(self.root)
        self.log_panel.title("기록 로그")
        self.log_panel.resizable(False, False)
        self.log_panel.protocol("WM_DELETE_WINDOW", self.close_log_panel)
        self.log_panel.configure(bg="#e2e8f0")
        self.log_header_frame = tk.Frame(self.log_panel, bg="#dbeafe")
        self.log_header_frame.place(x=0, y=0, width=LOG_PANEL_WIDTH, height=52)
        tk.Label(self.log_header_frame, text="기록 로그", font=self.header_font, bg="#dbeafe", fg="#0f172a").place(x=18, y=14)
        self.log_main_tab_button = tk.Button(
            self.log_header_frame,
            text="기록",
            font=self.button_font,
            bg="#1d4ed8",
            fg="#ffffff",
            activebackground="#1e40af",
            activeforeground="#ffffff",
            relief="raised",
            bd=1,
            highlightthickness=0,
            command=lambda: self.switch_log_panel_view("log"),
            cursor="hand2",
        )
        self.log_main_tab_button.place(x=160, y=14, width=74, height=30)
        self._bind_hover_button(self.log_main_tab_button, "#1d4ed8", "#1e40af", "#ffffff", "#ffffff")
        self.analysis_main_tab_button = tk.Button(
            self.log_header_frame,
            text="분석 창",
            font=self.button_font,
            bg="#0f766e",
            fg="#ffffff",
            activebackground="#115e59",
            activeforeground="#ffffff",
            relief="raised",
            bd=1,
            highlightthickness=0,
            command=self.toggle_analysis_window,
            cursor="hand2",
        )
        self.analysis_main_tab_button.place(x=244, y=14, width=96, height=30)
        self._bind_hover_button(self.analysis_main_tab_button, "#0f766e", "#115e59", "#ffffff", "#ffffff")
        self.main_tab_indicator = tk.Frame(self.log_header_frame, bg="#1d4ed8")
        self.main_tab_indicator.place(x=160, y=44, width=74, height=4)
        self.main_section_divider = tk.Frame(self.log_panel, bg="#1d4ed8")
        self.main_section_divider.place(x=0, y=49, width=LOG_PANEL_WIDTH, height=3)
        self.main_section_divider_shadow = tk.Frame(self.log_panel, bg="#94a3b8")
        self.main_section_divider_shadow.place(x=0, y=52, width=LOG_PANEL_WIDTH, height=1)

        self.log_content_frame = tk.Frame(self.log_panel, bg="#e2e8f0")
        self.log_content_frame.place(x=0, y=52, width=LOG_PANEL_WIDTH, height=LOG_PANEL_HEIGHT - 52)
        self.analysis_content_frame = tk.Frame(self.log_panel, bg="#e2e8f0")
        self.analysis_content_frame.place(x=0, y=52, width=LOG_PANEL_WIDTH, height=LOG_PANEL_HEIGHT - 52)

        self.record_candidate_tab_button = tk.Button(
            self.log_content_frame,
            text="저장 후보",
            font=self.button_font,
            bg="#f59e0b",
            fg="#ffffff",
            activebackground="#d97706",
            activeforeground="#ffffff",
            relief="raised",
            bd=1,
            highlightthickness=0,
            command=lambda: self.switch_record_subview("candidate"),
            cursor="hand2",
        )
        self.record_candidate_tab_button.place(x=18, y=8, width=92, height=28)
        self._bind_hover_button(self.record_candidate_tab_button, "#f59e0b", "#d97706", "#ffffff", "#ffffff")
        self.record_history_tab_button = tk.Button(
            self.log_content_frame,
            text="기록 히스토리",
            font=self.button_font,
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief="raised",
            bd=1,
            highlightthickness=0,
            command=lambda: self.switch_record_subview("history"),
            cursor="hand2",
        )
        self.record_history_tab_button.place(x=116, y=8, width=104, height=28)
        self._bind_hover_button(self.record_history_tab_button, "#2563eb", "#1d4ed8", "#ffffff", "#ffffff")
        self.record_subtab_indicator = tk.Frame(self.log_content_frame, bg="#f59e0b")
        self.record_subtab_indicator.place(x=18, y=38, width=92, height=4)
        self.record_section_divider = tk.Frame(self.log_content_frame, bg="#f59e0b")
        self.record_section_divider.place(x=0, y=42, width=LOG_PANEL_WIDTH, height=2)

        tk.Label(self.log_content_frame, text="보스 이름", font=self.label_font, bg="#e2e8f0", fg="#0f172a").place(x=18, y=46)
        self.log_boss_entry = tk.Entry(
            self.log_content_frame,
            textvariable=self.log_boss_name_var,
            font=(self.current_font_family, 11, "bold"),
            bd=0,
            highlightthickness=1,
            highlightbackground="#94a3b8",
            highlightcolor="#2563eb",
            bg="#f8fafc",
            fg="#0f172a",
        )
        self.log_boss_entry.place(x=18, y=72, width=200, height=28)
        self.log_boss_entry.bind("<KeyRelease>", self._on_log_boss_name_change)
        self.log_boss_entry.bind("<FocusIn>", self._on_korean_entry_focus)

        self.log_load_button = tk.Button(
            self.log_content_frame,
            text="로그 불러오기",
            font=self.button_font,
            bg="#f8f1df",
            fg="#7c2d12",
            activebackground="#f3e2bf",
            activeforeground="#7c2d12",
            relief="raised",
            bd=1,
            highlightthickness=1,
            highlightbackground="#93c5fd",
            highlightcolor="#2563eb",
            command=self.load_current_boss_log,
            cursor="hand2",
        )
        self.log_load_button.place(x=240, y=70, width=100, height=30)
        self._bind_hover_button(self.log_load_button, "#f8f1df", "#efe2c5", "#7c2d12", "#7c2d12")
        self.log_refresh_button = tk.Button(
            self.log_content_frame,
            text="현재값 가져오기",
            font=self.button_font,
            bg="#f59e0b",
            fg="#ffffff",
            activebackground="#d97706",
            activeforeground="#ffffff",
            relief="raised",
            bd=1,
            highlightthickness=1,
            highlightbackground="#fdba74",
            highlightcolor="#d97706",
            command=self.capture_boss_cut_candidate,
            cursor="hand2",
        )
        self.log_refresh_button.place(x=240, y=70, width=100, height=30)
        self._bind_hover_button(self.log_refresh_button, "#f59e0b", "#d97706", "#ffffff", "#ffffff")

        self.log_candidate_frame = tk.Frame(self.log_content_frame, bg="#e2e8f0")
        self.log_candidate_frame.place(x=0, y=112, width=LOG_PANEL_WIDTH, height=286)
        self.log_history_frame = tk.Frame(self.log_content_frame, bg="#e2e8f0")
        self.log_history_frame.place(x=0, y=112, width=LOG_PANEL_WIDTH, height=286)

        self.log_preview_text = tk.Text(
            self.log_candidate_frame,
            font=(self.current_font_family, 10, "bold"),
            bg="#fff7ed",
            fg="#431407",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#fdba74",
            wrap="word",
        )
        self.log_preview_text.place(x=18, y=0, width=322, height=182)
        self.log_preview_text.bind("<FocusIn>", self._on_korean_entry_focus)

        self.log_commit_button = tk.Button(
            self.log_candidate_frame,
            text="기록 확정",
            font=self.button_font,
            bg="#16a34a",
            fg="#ffffff",
            activebackground="#15803d",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            highlightthickness=0,
            command=self.commit_pending_log_record,
            cursor="hand2",
        )
        self.log_commit_button.place(x=18, y=192, width=100, height=30)
        self._bind_hover_button(self.log_commit_button, "#16a34a", "#15803d", "#ffffff", "#ffffff")
        self.log_discard_button = tk.Button(
            self.log_candidate_frame,
            text="폐기",
            font=self.button_font,
            bg="#dc2626",
            fg="#ffffff",
            activebackground="#b91c1c",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            highlightthickness=0,
            command=self.discard_pending_log_record,
            cursor="hand2",
        )
        self.log_discard_button.place(x=128, y=192, width=72, height=30)
        self._bind_hover_button(self.log_discard_button, "#dc2626", "#b91c1c", "#ffffff", "#ffffff")
        self.log_restore_button = tk.Button(
            self.log_candidate_frame,
            text="폐기 복원",
            font=self.button_font,
            bg="#7c3aed",
            fg="#ffffff",
            activebackground="#6d28d9",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            highlightthickness=0,
            command=self.restore_discarded_log_record,
            cursor="hand2",
        )
        self.log_restore_button.place(x=210, y=192, width=130, height=30)
        self._bind_hover_button(self.log_restore_button, "#7c3aed", "#6d28d9", "#ffffff", "#ffffff")

        self.log_status_label = tk.Label(
            self.log_candidate_frame,
            textvariable=self.log_status_var,
            font=(self.current_font_family, 9, "bold"),
            bg="#e2e8f0",
            fg="#334155",
            anchor="w",
            justify="left",
            wraplength=322,
        )
        self.log_status_label.place(x=18, y=228, width=322, height=52)

        self.log_history_scrollbar = tk.Scrollbar(self.log_history_frame, orient="vertical")
        self.log_history_text = tk.Text(
            self.log_history_frame,
            font=(self.current_font_family, 9, "bold"),
            bg="#eff6ff",
            fg="#1e3a8a",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#93c5fd",
            wrap="word",
            yscrollcommand=self.log_history_scrollbar.set,
        )
        self.log_history_text.place(x=18, y=0, width=300, height=256)
        self.log_history_scrollbar.config(command=self.log_history_text.yview)
        self.log_history_scrollbar.place(x=320, y=0, width=20, height=256)

        tk.Label(self.analysis_content_frame, text="분석 대상 보스", font=self.label_font, bg="#e2e8f0", fg="#0f172a").place(x=18, y=6)
        self.analysis_boss_entry = tk.Entry(
            self.analysis_content_frame,
            textvariable=self.log_boss_name_var,
            font=(self.current_font_family, 11, "bold"),
            bd=0,
            highlightthickness=1,
            highlightbackground="#94a3b8",
            highlightcolor="#2563eb",
            bg="#f8fafc",
            fg="#0f172a",
        )
        self.analysis_boss_entry.place(x=18, y=32, width=180, height=28)
        self.analysis_boss_entry.bind("<KeyRelease>", self._on_log_boss_name_change)
        self.analysis_boss_entry.bind("<FocusIn>", self._on_korean_entry_focus)
        self.analysis_load_button = tk.Button(
            self.analysis_content_frame,
            text="분석 불러오기",
            font=self.button_font,
            bg="#f8f1df",
            fg="#7c2d12",
            activebackground="#f3e2bf",
            activeforeground="#7c2d12",
            relief="flat",
            bd=0,
            highlightthickness=0,
            command=self.refresh_analysis_view,
            cursor="hand2",
        )
        self.analysis_load_button.place(x=206, y=30, width=134, height=30)
        self.analysis_load_button.config(highlightthickness=1, highlightbackground="#cbd5e1", highlightcolor="#2563eb", bd=1, relief="raised")
        tk.Label(self.analysis_content_frame, text="최근 기록", font=self.label_font, bg="#e2e8f0", fg="#0f172a").place(x=18, y=66)
        self.analysis_count_menu = tk.OptionMenu(
            self.analysis_content_frame,
            self.analysis_count_var,
            "5개",
            "10개",
            "20개",
            "30개",
            "50개",
            command=lambda *_args: self.refresh_analysis_view(),
        )
        self.analysis_count_menu.config(font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", highlightthickness=0, bd=0)
        self.analysis_count_menu["menu"].config(font=(self.current_font_family, 9), bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18")
        self.analysis_count_menu.place(x=92, y=64, width=120, height=30)
        self.analysis_count_menu.config(highlightthickness=1, highlightbackground="#cbd5e1", highlightcolor="#2563eb", bd=1, relief="raised")
        self.analysis_summary_label = tk.Label(
            self.analysis_content_frame,
            text="분석할 검증 완료 기록이 없습니다.",
            font=(self.current_font_family, 9, "bold"),
            bg="#fff7ed",
            fg="#7c2d12",
            justify="left",
            anchor="nw",
            padx=8,
            pady=8,
        )
        self.analysis_summary_label.place(x=18, y=102, width=322, height=70)
        self.analysis_canvas = tk.Canvas(
            self.analysis_content_frame,
            width=322,
            height=170,
            bg="#eff6ff",
            bd=0,
            highlightthickness=1,
            highlightbackground="#93c5fd",
        )
        self.analysis_canvas.place(x=18, y=180)
        self.analysis_list_text = tk.Text(
            self.analysis_content_frame,
            font=(self.current_font_family, 9, "bold"),
            bg="#f8fafc",
            fg="#334155",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            wrap="word",
        )
        self.analysis_list_text.place(x=18, y=358, width=322, height=72)
        self.switch_log_panel_view("log")
        self.switch_record_subview("candidate")
        self.close_log_panel()

    def _ensure_analysis_window(self) -> None:
        if self.analysis_window is not None and self.analysis_window.winfo_exists():
            return
        self.analysis_window = tk.Toplevel(self.root)
        self.analysis_window.title("보스 분석")
        self.analysis_window.resizable(False, False)
        self.analysis_window.protocol("WM_DELETE_WINDOW", self.close_analysis_window)
        self.analysis_window.configure(bg="#e2e8f0")
        tk.Label(self.analysis_window, text="보스", font=self.label_font, bg="#e2e8f0", fg="#0f172a").place(x=18, y=14)
        self.analysis_window_boss_entry = tk.Entry(
            self.analysis_window,
            textvariable=self.log_boss_name_var,
            font=(self.current_font_family, 11, "bold"),
            bd=0,
            highlightthickness=1,
            highlightbackground="#94a3b8",
            highlightcolor="#2563eb",
            bg="#f8fafc",
            fg="#0f172a",
        )
        self.analysis_window_boss_entry.place(x=58, y=12, width=160, height=30)
        self.analysis_window_boss_entry.bind("<KeyRelease>", self._on_log_boss_name_change)
        self.analysis_window_boss_entry.bind("<FocusIn>", self._on_korean_entry_focus)
        self.analysis_window_load_button = tk.Button(
            self.analysis_window,
            text="불러오기",
            font=self.button_font,
            bg="#f8f1df",
            fg="#7c2d12",
            activebackground="#f3e2bf",
            activeforeground="#7c2d12",
            relief="raised",
            bd=1,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#2563eb",
            command=self.refresh_analysis_view,
            cursor="hand2",
        )
        self.analysis_window_load_button.place(x=228, y=12, width=100, height=30)
        self._bind_hover_button(self.analysis_window_load_button, "#f8f1df", "#efe2c5", "#7c2d12", "#7c2d12")
        tk.Label(self.analysis_window, text="최근", font=self.label_font, bg="#e2e8f0", fg="#0f172a").place(x=342, y=14)
        self.analysis_window_count_menu = tk.OptionMenu(
            self.analysis_window,
            self.analysis_count_var,
            "5개",
            "10개",
            "20개",
            "30개",
            "50개",
            command=lambda *_args: self.refresh_analysis_view(),
        )
        self.analysis_window_count_menu.config(font=self.button_font, bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18", highlightthickness=1, highlightbackground="#cbd5e1", highlightcolor="#2563eb", bd=1, relief="raised")
        self.analysis_window_count_menu["menu"].config(font=(self.current_font_family, 9), bg="#f8f1df", fg="#3f2d18", activebackground="#efe2c5", activeforeground="#3f2d18")
        self.analysis_window_count_menu.place(x=384, y=12, width=96, height=30)
        self.analysis_info_box = tk.Frame(self.analysis_window, bg="#fff7ed", highlightthickness=1, highlightbackground="#fdba74")
        self.analysis_info_box.place(x=18, y=56, width=250, height=68)
        self.analysis_info_title_label = tk.Label(
            self.analysis_info_box,
            text="최근 0개 검증 기록",
            font=(self.current_font_family, 11, "bold"),
            bg="#fff7ed",
            fg="#7c2d12",
            anchor="w",
            justify="left",
            padx=10,
            pady=10,
        )
        self.analysis_info_title_label.place(x=0, y=0, width=248, height=66)

        self.analysis_metric_box = tk.Frame(self.analysis_window, bg="#f8fafc", highlightthickness=1, highlightbackground="#cbd5e1")
        self.analysis_metric_box.place(x=292, y=56, width=250, height=68)
        self.analysis_average_cut_label = tk.Label(
            self.analysis_metric_box,
            text="평균 컷 시간: --:--:--",
            font=(self.current_font_family, 12, "bold"),
            bg="#f8fafc",
            fg="#dc2626",
            anchor="w",
            justify="left",
            padx=10,
        )
        self.analysis_average_cut_label.place(x=0, y=6, width=248, height=26)
        self.analysis_average_expected_label = tk.Label(
            self.analysis_metric_box,
            text="평균 예상시간: --:--:--",
            font=(self.current_font_family, 12, "bold"),
            bg="#f8fafc",
            fg="#16a34a",
            anchor="w",
            justify="left",
            padx=10,
        )
        self.analysis_average_expected_label.place(x=0, y=34, width=248, height=26)
        self.analysis_window_canvas = tk.Canvas(
            self.analysis_window,
            width=524,
            height=180,
            bg="#eff6ff",
            bd=0,
            highlightthickness=1,
            highlightbackground="#93c5fd",
        )
        self.analysis_window_canvas.place(x=18, y=136)
        self.analysis_window_list_scrollbar = tk.Scrollbar(self.analysis_window, orient="vertical")
        self.analysis_window_list_text = tk.Text(
            self.analysis_window,
            font=(self.current_font_family, 9, "bold"),
            bg="#f8fafc",
            fg="#334155",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            wrap="word",
            yscrollcommand=self.analysis_window_list_scrollbar.set,
        )
        self.analysis_window_list_text.place(x=18, y=328, width=500, height=152)
        self.analysis_window_list_scrollbar.config(command=self.analysis_window_list_text.yview)
        self.analysis_window_list_scrollbar.place(x=520, y=328, width=22, height=152)
        self.close_analysis_window()

    def _set_text_widget(self, widget: tk.Text, value: str, readonly: bool = True) -> None:
        widget.config(state="normal")
        widget.delete("1.0", tk.END)
        for line in value.splitlines():
            tag = None
            if line.startswith("컷 시간:"):
                tag = "cut_time"
            elif line.startswith("컷 예상:"):
                tag = "expected_time"
            widget.insert(tk.END, line + "\n", tag)
        widget.tag_configure("cut_time", foreground="#dc2626")
        widget.tag_configure("expected_time", foreground="#15803d")
        widget.config(state="disabled" if readonly else "normal")

    def _parse_candidate_preview_text(self) -> tuple[dict | None, str | None]:
        raw_text = self.log_preview_text.get("1.0", tk.END).strip()
        if not raw_text or raw_text.startswith("[정보]"):
            return None, "먼저 현재값 가져오기를 눌러 저장 후보를 만드세요."
        parsed = self._parse_log_block(raw_text)
        boss_name = self._sanitize_boss_name(parsed.get("boss_name", ""))
        if not boss_name:
            return None, "형식에 맞지 않습니다. 보스 이름을 확인한 뒤 현재값 가져오기를 눌러 다시 불러오세요."
        actual_cut_time = parsed.get("actual_cut_time", "")
        actual_cut_seconds = self._parse_log_time_value(actual_cut_time)
        if actual_cut_seconds is None:
            return None, "형식에 맞지 않습니다. 컷 시간을 확인한 뒤 현재값 가져오기를 눌러 다시 불러오세요."
        expected_time = parsed.get("expected_time", "")
        overrun_time = parsed.get("overrun_time", "")
        gwang_time = parsed.get("gwang_time", "")
        expected_total_seconds = self._parse_log_time_value(expected_time) if expected_time and expected_time != "계산 불가" else None
        overrun_seconds = self._parse_log_time_value(overrun_time) if overrun_time and overrun_time != "계산 불가" else None
        gwang_seconds = self._parse_log_time_value(gwang_time) if gwang_time and gwang_time != "미확정" else None
        if expected_time and expected_time != "계산 불가" and expected_total_seconds is None:
            return None, "형식에 맞지 않습니다. 컷 예상 형식을 확인한 뒤 현재값 가져오기를 눌러 다시 불러오세요."
        if overrun_time and overrun_time != "계산 불가" and overrun_seconds is None:
            return None, "형식에 맞지 않습니다. 초과시간 형식을 확인한 뒤 현재값 가져오기를 눌러 다시 불러오세요."
        if gwang_time and gwang_time != "미확정" and gwang_seconds is None:
            return None, "형식에 맞지 않습니다. 광타임 형식을 확인한 뒤 현재값 가져오기를 눌러 다시 불러오세요."
        record = {
            "recorded_at": parsed.get("recorded_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "boss_name": boss_name,
            "validation_state": parsed.get("validation_state") or "수동 수정",
            "validation_note": parsed.get("validation_note", ""),
            "gwang_time": gwang_time or "미확정",
            "expected_time": expected_time or "계산 불가",
            "actual_cut_time": actual_cut_time,
            "overrun_time": overrun_time or "계산 불가",
            "gwang_seconds": gwang_seconds,
            "expected_total_seconds": expected_total_seconds,
            "actual_cut_seconds": actual_cut_seconds,
            "overrun_seconds": overrun_seconds,
        }
        return record, None

    def _on_log_boss_name_change(self, _event=None) -> None:
        if self.pending_log_record is not None:
            self.pending_log_record["boss_name"] = self._sanitize_boss_name(self.log_boss_name_var.get()) or "미지정보스"
            self._refresh_log_preview()
        self.refresh_analysis_view()

    def _on_korean_entry_focus(self, _event=None) -> None:
        widget = getattr(_event, "widget", None) if _event is not None else None
        activate_korean_keyboard_for_widget(widget)
        schedule_korean_keyboard_activation(widget)

    def _apply_log_main_tab_style(self) -> None:
        active_mode = self.log_view_mode_var.get()
        self.log_main_tab_button.config(bg="#1d4ed8", fg="#ffffff", activebackground="#1e40af", activeforeground="#ffffff", relief="sunken", bd=1)
        self.analysis_main_tab_button.config(bg="#0f766e", fg="#ffffff", activebackground="#115e59", activeforeground="#ffffff", relief="raised", bd=1)
        self.log_main_tab_button.place_configure(y=14, height=30)
        self.analysis_main_tab_button.place_configure(y=14, height=30)
        self.main_tab_indicator.config(bg="#1d4ed8")
        self.main_tab_indicator.place_configure(x=160, width=74)
        self.main_section_divider.config(bg="#1d4ed8")
        self.main_section_divider_shadow.config(bg="#93c5fd")

    def _apply_record_subtab_style(self) -> None:
        active_mode = self.log_record_subview_var.get()
        if active_mode == "history":
            self.record_candidate_tab_button.config(bg="#cbd5e1", fg="#334155", activebackground="#cbd5e1", activeforeground="#334155", relief="raised", bd=1)
            self.record_history_tab_button.config(bg="#2563eb", fg="#ffffff", activebackground="#1d4ed8", activeforeground="#ffffff", relief="sunken", bd=1)
            self.record_history_tab_button.place_configure(y=8, height=28)
            self.record_candidate_tab_button.place_configure(y=8, height=28)
            self.record_subtab_indicator.config(bg="#2563eb")
            self.record_subtab_indicator.place_configure(x=116, width=104)
            self.record_section_divider.config(bg="#2563eb")
        else:
            self.record_candidate_tab_button.config(bg="#f59e0b", fg="#ffffff", activebackground="#d97706", activeforeground="#ffffff", relief="sunken", bd=1)
            self.record_history_tab_button.config(bg="#cbd5e1", fg="#334155", activebackground="#cbd5e1", activeforeground="#334155", relief="raised", bd=1)
            self.record_candidate_tab_button.place_configure(y=8, height=28)
            self.record_history_tab_button.place_configure(y=8, height=28)
            self.record_subtab_indicator.config(bg="#f59e0b")
            self.record_subtab_indicator.place_configure(x=18, width=92)
            self.record_section_divider.config(bg="#f59e0b")

    def switch_log_panel_view(self, mode: str) -> None:
        self.log_view_mode_var.set("log")
        self._apply_log_main_tab_style()
        self.analysis_content_frame.place_forget()
        self.log_content_frame.place(x=0, y=52, width=LOG_PANEL_WIDTH, height=LOG_PANEL_HEIGHT - 52)

    def switch_record_subview(self, mode: str) -> None:
        self.log_record_subview_var.set(mode)
        self._apply_record_subtab_style()
        if mode == "history":
            self.log_candidate_frame.place_forget()
            self.log_history_frame.place(x=0, y=112, width=LOG_PANEL_WIDTH, height=286)
            self.log_refresh_button.place_forget()
            self.log_load_button.place(x=240, y=70, width=100, height=30)
            if self._sanitize_boss_name(self.log_boss_name_var.get()):
                self.load_current_boss_log()
        else:
            self.log_history_frame.place_forget()
            self.log_candidate_frame.place(x=0, y=112, width=LOG_PANEL_WIDTH, height=286)
            self.log_load_button.place_forget()
            self.log_refresh_button.place(x=240, y=70, width=100, height=30)

    def _refresh_log_panel(self) -> None:
        self._refresh_log_preview()
        self.load_current_boss_log()
        self.refresh_analysis_view()

    def _refresh_log_preview(self) -> None:
        if self.log_panel is None or not self.log_panel.winfo_exists():
            return
        if self.pending_log_record is None:
            preview_text = (
                "[정보]\n"
                "스톱워치에 정보가 없다면 수동으로 만들 수 있습니다.\n"
                "- 보스 이름을 입력하세요\n"
                "- 총 시간을 눌러 보스 시간을 입력한 뒤 보스 컷 또는 현재값 가져오기를 사용하세요\n"
                "- 읽어온 후보를 기록 확정하면 같은 보스 기준 최근 50개까지 저장되고, 분석 평균값에 사용됩니다"
            )
        else:
            preview_text = self._format_log_record_block(self.pending_log_record)
        self._set_text_widget(self.log_preview_text, preview_text, readonly=False)

    def load_current_boss_log(self) -> None:
        if self.log_panel is None or not self.log_panel.winfo_exists():
            return
        boss_name = self._sanitize_boss_name(self.log_boss_name_var.get()) or "미지정보스"
        blocks = self._read_log_blocks(boss_name)
        history_text = self._format_history_blocks_for_display(blocks[-10:])
        self._set_text_widget(self.log_history_text, history_text)
        self.refresh_analysis_view()

    def refresh_analysis_view(self) -> None:
        if self.analysis_window is None or not self.analysis_window.winfo_exists():
            return
        boss_name = self._sanitize_boss_name(self.log_boss_name_var.get()) or "미지정보스"
        blocks = self._read_log_blocks(boss_name)
        parsed_records = [self._parse_log_block(block) for block in blocks]
        valid_records = [
            record
            for record in parsed_records
            if record.get("actual_cut_seconds") is not None
            and record.get("expected_total_seconds") is not None
            and (
                not record.get("validation_state")
                or record.get("validation_state", "").startswith("검증 완료")
            )
        ]
        limited_records = valid_records[-self._get_analysis_limit():]
        if not limited_records:
            self.analysis_info_title_label.config(text="최근 0개 검증 기록")
            self.analysis_average_cut_label.config(text="평균 컷 시간: --:--:--")
            self.analysis_average_expected_label.config(text="평균 예상시간: --:--:--")
            self.analysis_window_canvas.delete("all")
            self._set_text_widget(self.analysis_window_list_text, "표시할 분석 기록이 없습니다.")
            return
        cut_values = [record["actual_cut_seconds"] for record in limited_records if record["actual_cut_seconds"] is not None]
        expected_values = [record["expected_total_seconds"] for record in limited_records if record["expected_total_seconds"] is not None]
        if not cut_values:
            self.analysis_info_title_label.config(text=f"최근 {len(limited_records)}개 검증 기록")
            self.analysis_average_cut_label.config(text="평균 컷 시간: --:--:--")
            self.analysis_average_expected_label.config(text="평균 예상시간: --:--:--")
            self.analysis_window_canvas.delete("all")
            self._set_text_widget(self.analysis_window_list_text, "표시할 분석 기록이 없습니다.")
            return
        average_cut = sum(cut_values) / len(cut_values)
        average_expected = sum(expected_values) / len(expected_values) if expected_values else 0.0
        average_gap = average_cut - average_expected if expected_values else 0.0
        self.analysis_info_title_label.config(text=f"최근 {len(limited_records)}개 검증 기록")
        self.analysis_average_cut_label.config(text=f"평균 컷 시간: {format_seconds(average_cut, show_centiseconds=True)}")
        self.analysis_average_expected_label.config(
            text=f"평균 예상시간: {format_seconds(average_expected, show_centiseconds=True) if expected_values else '--:--:--'}"
        )
        self._draw_analysis_graph(limited_records)
        list_lines = []
        for index, record in enumerate(limited_records, start=1):
            diff_seconds = None
            if record["actual_cut_seconds"] is not None and record["expected_total_seconds"] is not None:
                diff_seconds = record["actual_cut_seconds"] - record["expected_total_seconds"]
            diff_text = format_seconds(abs(diff_seconds), show_centiseconds=True) if diff_seconds is not None else "계산 불가"
            diff_state = "늦음" if diff_seconds is not None and diff_seconds >= 0 else "빠름"
            list_lines.append(
                f"{index}. {record['recorded_at']} | 컷 {record['actual_cut_time']} | 예상 {record['expected_time']} | 차이 {diff_text} {diff_state}"
            )
        self._set_text_widget(self.analysis_window_list_text, "\n".join(list_lines))

    def _draw_analysis_graph(self, records: list[dict]) -> None:
        canvas = self.analysis_window_canvas
        canvas.delete("all")
        width = int(canvas.cget("width"))
        height = int(canvas.cget("height"))
        left, top, right, bottom = 34, 18, width - 12, height - 28
        canvas.create_rectangle(left, top, right, bottom, outline="#93c5fd", width=1)
        values = []
        for record in records:
            if record["actual_cut_seconds"] is not None:
                values.append(record["actual_cut_seconds"])
            if record["expected_total_seconds"] is not None:
                values.append(record["expected_total_seconds"])
        if not values:
            canvas.create_text(width // 2, height // 2, text="그래프 데이터가 없습니다.", fill="#475569", font=self.percent_font)
            return
        min_value = min(values)
        max_value = max(values)
        if abs(max_value - min_value) < 0.01:
            max_value = min_value + 1.0
        for ratio in (0.0, 0.5, 1.0):
            y = bottom - ((bottom - top) * ratio)
            value = min_value + ((max_value - min_value) * ratio)
            canvas.create_line(left, y, right, y, fill="#dbeafe", width=1)
            canvas.create_text(18, y, text=format_seconds(value), fill="#1e3a8a", font=self.percent_font)
        if len(records) == 1:
            x_positions = [(left + right) / 2]
        else:
            step = (right - left) / max(1, len(records) - 1)
            x_positions = [left + (step * index) for index in range(len(records))]
        actual_points = []
        expected_points = []
        for index, record in enumerate(records):
            x = x_positions[index]
            actual_y = bottom - ((record["actual_cut_seconds"] - min_value) / (max_value - min_value)) * (bottom - top)
            actual_points.extend((x, actual_y))
            canvas.create_text(x, bottom + 12, text=str(index + 1), fill="#64748b", font=self.percent_font)
            if record["expected_total_seconds"] is not None:
                expected_y = bottom - ((record["expected_total_seconds"] - min_value) / (max_value - min_value)) * (bottom - top)
                expected_points.extend((x, expected_y))
        if len(actual_points) >= 4:
            canvas.create_line(*actual_points, fill="#dc2626", width=2, smooth=True)
        for index in range(0, len(actual_points), 2):
            canvas.create_oval(actual_points[index] - 3, actual_points[index + 1] - 3, actual_points[index] + 3, actual_points[index + 1] + 3, fill="#dc2626", outline="")
        if len(expected_points) >= 4:
            canvas.create_line(*expected_points, fill="#16a34a", width=2, smooth=True)
        for index in range(0, len(expected_points), 2):
            canvas.create_oval(expected_points[index] - 3, expected_points[index + 1] - 3, expected_points[index] + 3, expected_points[index + 1] + 3, fill="#16a34a", outline="")
        canvas.create_text(left, height - 10, anchor="w", text="빨강: 컷 시간", fill="#dc2626", font=self.percent_font)
        canvas.create_text(right, height - 10, anchor="e", text="초록: 예상시간", fill="#16a34a", font=self.percent_font)

    def capture_boss_cut_candidate(self) -> None:
        if self.running:
            self.stop_timer()
        self.open_log_panel()
        record, error_message = self._build_trusted_log_record()
        if record is None:
            self.log_status_var.set(error_message or "저장 후보를 만들 수 없습니다.")
            self.pending_log_record = None
            self._refresh_log_preview()
            return
        if not self._sanitize_boss_name(self.log_boss_name_var.get()):
            self.log_boss_name_var.set(record["boss_name"])
        else:
            record["boss_name"] = self._sanitize_boss_name(self.log_boss_name_var.get()) or "미지정보스"
        self.pending_log_record = record
        if record["validation_state"] == "검증 완료":
            self.log_status_var.set("저장 후보를 갱신했습니다. 보스 이름을 확인한 뒤 기록 확정을 누르세요.")
        else:
            self.log_status_var.set(f"주의: {record['validation_state']} 상태입니다. 후보를 검토한 뒤 저장 또는 폐기를 선택하세요.")
        self.log_record_subview_var.set("candidate")
        self._refresh_log_preview()
        self.switch_record_subview("candidate")
        self.load_current_boss_log()

    def commit_pending_log_record(self) -> None:
        if self.pending_log_record is None:
            self.log_status_var.set("먼저 현재값 가져오기를 눌러 저장 후보를 만드세요.")
            self._refresh_log_preview()
            return
        edited_record, error_message = self._parse_candidate_preview_text()
        if edited_record is None:
            self.log_status_var.set(error_message or "형식에 맞지 않습니다. 현재값 가져오기를 눌러 다시 불러오세요.")
            return
        boss_name = self._sanitize_boss_name(self.log_boss_name_var.get())
        if not boss_name:
            self.log_status_var.set("보스 이름을 입력해야 기록할 수 있습니다.")
            return
        record = dict(edited_record)
        record["boss_name"] = boss_name
        self._append_log_record(record)
        self.pending_log_record = None
        self.log_status_var.set(f"{boss_name} 기록을 저장했습니다.")
        self._refresh_log_preview()
        self.load_current_boss_log()

    def discard_pending_log_record(self) -> None:
        if self.pending_log_record is None:
            self.log_status_var.set("폐기할 저장 후보가 없습니다.")
            return
        self.discarded_log_records.append(dict(self.pending_log_record))
        self.pending_log_record = None
        self.log_status_var.set("저장 후보를 폐기했습니다. 필요하면 폐기 복원으로 되돌릴 수 있습니다.")
        self._refresh_log_preview()

    def restore_discarded_log_record(self) -> None:
        if not self.discarded_log_records:
            self.log_status_var.set("복원할 폐기 기록이 없습니다.")
            return
        self.pending_log_record = self.discarded_log_records.pop()
        self.log_boss_name_var.set(self.pending_log_record["boss_name"])
        self.log_status_var.set("마지막 폐기 기록을 복원했습니다.")
        self._refresh_log_preview()
        self.load_current_boss_log()

    def on_close(self) -> None:
        self.running = False
        self._cancel_update()
        self._stop_pause_blink()
        self._update_window_positions()
        self._save_settings()
        if self.settings_notice_after_id is not None and hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.after_cancel(self.settings_notice_after_id)
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.destroy()
        if self.log_panel is not None and self.log_panel.winfo_exists():
            self.log_panel.destroy()
        if self.analysis_window is not None and self.analysis_window.winfo_exists():
            self.analysis_window.destroy()
        self.root.destroy()


def main() -> None:
    configure_windows_dpi()
    root = tk.Tk()
    configure_tk_scaling(root)
    app = BossTimerApp(root)
    app._set_elapsed_color("#cbd5e1")
    root.mainloop()


if __name__ == "__main__":
    main()

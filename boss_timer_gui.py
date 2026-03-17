import configparser
import ctypes
import math
import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import font as tkfont


WINDOW_WIDTH = 470
WINDOW_HEIGHT = 438
ALERT_AREA_X = 29
ALERT_AREA_Y = 294
ALERT_TAG = "alert_overlay"
GRAPH_AREA_X = 29
GRAPH_AREA_Y = 330
GRAPH_TAG = "graph_overlay"
APP_VERSION = "v1.1.0"
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
    "settings": 27,
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
ELAPSED_BRUSH_COLORS = {
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


def get_resource_root() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(get_app_root(), "boss_timer_settings.ini")


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


def get_progress_bar_path(bar_key: str) -> str:
    parts = PROGRESS_BAR_FILES[bar_key]
    return os.path.join(get_resource_root(), *parts)


def configure_windows_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
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
        self.progress_bar_images: dict[str, tk.PhotoImage] = {}
        self.progress_fill_cache: dict[int, tk.PhotoImage] = {}
        self.settings_notice_after_id = None
        self.settings_notice_end_time = 0.0

        self.title_font = tkfont.Font(family=self.current_font_family, size=40, weight="bold")
        self.header_font = tkfont.Font(family=self.current_font_family, size=13, weight="bold")
        self.alert_font = tkfont.Font(family=self.current_font_family, size=11, weight="bold")
        self.label_font = tkfont.Font(family=self.current_font_family, size=12, weight="bold")
        self.value_font = tkfont.Font(family=self.current_font_family, size=15, weight="bold")
        self.button_font = tkfont.Font(family=self.current_font_family, size=10, weight="bold")
        self.banner_font = tkfont.Font(family=self.current_font_family, size=13, weight="bold")
        self.percent_font = tkfont.Font(family=self.current_font_family, size=9, weight="bold")
        self.burst_font = tkfont.Font(family=self.current_font_family, size=28, weight="bold")
        self.signature_font = tkfont.Font(family=self.current_font_family, size=9, weight="bold")
        self.icon_font = tkfont.Font(family=self.current_font_family, size=12, weight="bold")

        self.elapsed_var = tk.StringVar(value="00:00:00")
        self.reached_70_var = tk.StringVar(value="00:00:00")
        self.remain_90_var = tk.StringVar(value="00:00:00")
        self.remain_kill_var = tk.StringVar(value="00:00:00")

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
        self.show_elapsed_brush = saved_elapsed_brush
        if saved_elapsed_brush_color in ELAPSED_BRUSH_COLORS:
            self.elapsed_brush_color_name = saved_elapsed_brush_color

    def _parse_int(self, value: str | None, fallback: int) -> int:
        try:
            return int(value) if value is not None else fallback
        except (TypeError, ValueError):
            return fallback

    def _save_settings(self) -> None:
        config = configparser.ConfigParser()
        config["settings"] = {
            "background_path": self._normalize_background_source(self.background_path),
            "font_family": self.current_font_family,
            "background_alignment": self.background_alignment,
            "show_alert_overlay": str(self.show_alert_overlay),
            "show_alert_percent": str(self.show_alert_percent),
            "show_hodulgap_banner": str(self.show_hodulgap_banner),
            "show_elapsed_brush": str(self.show_elapsed_brush),
            "elapsed_brush_color": self.elapsed_brush_color_name,
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
        self.root.resizable(True, True)

        self.bg_canvas = tk.Canvas(self.root, width=WINDOW_WIDTH, height=WINDOW_HEIGHT, bd=0, highlightthickness=0)
        self.bg_canvas.pack(fill="both", expand=True)

        self.background_item = self.bg_canvas.create_image(0, 0, anchor="nw")

        self.root.bind("<Configure>", self._on_root_resize)

        self.settings_button = self._create_canvas_icon_button("settings", 440, 26, self.open_settings_window)

        self.elapsed_color = "#cbd5e1"
        self._draw_elapsed_brush()
        self.elapsed_shadow_item = self.bg_canvas.create_text(
            238,
            61,
            text=self.elapsed_var.get(),
            fill="#0f172a",
            font=self.title_font,
        )
        self.elapsed_text_item = self.bg_canvas.create_text(
            236,
            59,
            text=self.elapsed_var.get(),
            fill=self.elapsed_color,
            font=self.title_font,
        )

        self.start_button = self._create_canvas_icon_button("play", 203, 118, self.toggle_timer)
        self.reset_button = self._create_canvas_icon_button("reset", 268, 118, self.reset_timer)

        self.record_button_frame = tk.Frame(
            self.root,
            bg="#fb923c",
            bd=1,
            relief="raised",
            highlightthickness=0,
            highlightbackground="#fdba74",
            highlightcolor="#fdba74",
            cursor="hand2",
        )
        self.record_paw_label = tk.Label(
            self.record_button_frame,
            text="🐾",
            font=tkfont.Font(family=self.current_font_family, size=16, weight="bold"),
            bg="#fb923c",
            fg="#111827",
            padx=1,
            pady=0,
            cursor="hand2",
        )
        self.record_paw_label.pack(side="left", padx=(2, 0))
        self.record_button = tk.Button(
            self.record_button_frame,
            text="광 췍",
            font=tkfont.Font(family=self.current_font_family, size=11, weight="bold"),
            bg="#fb923c",
            fg="#ffffff",
            activebackground="#ea580c",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=10,
            pady=0,
            command=self.record_70_percent_time,
            cursor="hand2",
        )
        self.record_button.pack(side="left", padx=(0, 3), pady=0)
        self.record_paw_label.bind("<ButtonPress-1>", lambda event: self._set_record_button_relief("sunken"))
        self.record_paw_label.bind("<ButtonRelease-1>", self._handle_record_button_release)
        self.record_button_frame.bind("<ButtonPress-1>", lambda event: self._set_record_button_relief("sunken"))
        self.record_button_frame.bind("<ButtonRelease-1>", self._handle_record_button_release)
        self.bg_canvas.create_window(17, 162, anchor="w", window=self.record_button_frame)
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
        self.bg_canvas.create_window(150, 162, anchor="w", window=self.record_time_label, width=116)

        self._create_timer_row(208, "컷 남은", self.remain_kill_var, "#fef2f2", "#dc2626", store_as="remain_kill_box", label_fg="#ffffff", brush_color="#8f2b2b")
        self._create_timer_row(236, "90%", self.remain_90_var, "#eef2ff", "#ea580c", store_as="remain_90_box", visible=False)

        self._draw_brush_stroke(self.bg_canvas, 18, 40, 92, 26, "#e84141")
        self.bg_canvas.create_text(33, 56, anchor="w", text="총 시간", font=self.header_font, fill="#ffffff")
        self._draw_brush_stroke(self.bg_canvas, 0, WINDOW_HEIGHT - 28, 96, 22, "#d8bea1")
        self.bg_canvas.create_text(10, WINDOW_HEIGHT - 8, anchor="sw", text="밤비 is Back", font=self.signature_font, fill="#4a2c1d")
        self.bg_canvas.create_text(WINDOW_WIDTH - 10, WINDOW_HEIGHT - 8, anchor="se", text=APP_VERSION, font=self.percent_font, fill="#9ca3af")

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

    def _draw_elapsed_brush(self) -> None:
        self.bg_canvas.delete(ELAPSED_BRUSH_TAG)
        if not self.show_elapsed_brush:
            return
        brush_color = ELAPSED_BRUSH_COLORS.get(self.elapsed_brush_color_name, ELAPSED_BRUSH_COLORS["노랑"])
        self._draw_brush_stroke(self.bg_canvas, 84, 41, 302, 40, brush_color, tags=ELAPSED_BRUSH_TAG)
        self.bg_canvas.tag_raise(ELAPSED_BRUSH_TAG, self.background_item)

    def _create_timer_row(self, y: int, label_text: str, value_var: tk.StringVar, box_bg: str, box_fg: str, store_as: str | None = None, visible: bool = True, label_fg: str = "#f8fafc", brush_color: str = "#c65d1e") -> None:
        if not visible:
            hidden_box = tk.Label(self.root, textvariable=value_var)
            if store_as:
                setattr(self, store_as, hidden_box)
            return
        self._draw_brush_stroke(self.bg_canvas, 14, y - 14, 110, 24, brush_color)
        self.bg_canvas.create_text(34, y, anchor="w", text=label_text, font=self.label_font, fill=label_fg)
        value_box = tk.Label(self.root, textvariable=value_var, font=self.value_font, width=9, bg=box_bg, fg=box_fg, relief="sunken", bd=2, padx=5, pady=4, anchor="center")
        self.bg_canvas.create_window(206, y, anchor="center", window=value_box)
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

    def _crop_photo_image(self, source: tk.PhotoImage, x1: int, y1: int, x2: int, y2: int) -> tk.PhotoImage:
        cropped = tk.PhotoImage(width=x2 - x1, height=y2 - y1)
        cropped.tk.call(str(cropped), "copy", str(source), "-from", x1, y1, x2, y2, "-to", 0, 0)
        return cropped

    def _ensure_progress_bar_image(self, bar_key: str) -> tk.PhotoImage:
        if bar_key in self.progress_bar_images:
            return self.progress_bar_images[bar_key]
        source = tk.PhotoImage(file=get_progress_bar_path(bar_key))
        x1, y1, x2, y2 = PROGRESS_BAR_CROP
        cropped = self._crop_photo_image(source, x1, y1, x2, y2)
        if PROGRESS_BAR_SCALE > 1:
            cropped = cropped.subsample(PROGRESS_BAR_SCALE, PROGRESS_BAR_SCALE)
        self.progress_bar_images[bar_key] = cropped
        return cropped

    def _get_progress_fill_image(self, width: int) -> tk.PhotoImage | None:
        if width <= 0:
            return None
        full_image = self._ensure_progress_bar_image("full")
        width = min(width, full_image.width())
        if width in self.progress_fill_cache:
            return self.progress_fill_cache[width]
        partial = tk.PhotoImage(width=width, height=full_image.height())
        partial.tk.call(str(partial), "copy", str(full_image), "-from", 0, 0, width, full_image.height(), "-to", 0, 0)
        self.progress_fill_cache[width] = partial
        return partial

    def _make_image_icon_button(self, icon_key: str, command) -> tk.Button:
        image = self._ensure_button_image(icon_key, "normal")
        button = tk.Button(
            self.root,
            image=image,
            command=command,
            bg="#000001",
            activebackground="#000001",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=0,
            pady=0,
            cursor="hand2",
        )
        button.bind("<ButtonPress-1>", lambda event, widget=button: widget.config(relief="sunken"))
        button.bind("<ButtonRelease-1>", lambda event, widget=button: widget.config(relief="flat"))
        return button

    def _create_canvas_icon_button(self, icon_key: str, x: int, y: int, command):
        image = self._ensure_button_image(icon_key, "normal")
        tag = f"icon_button_{icon_key}"
        item_id = self.bg_canvas.create_image(x, y, image=image, anchor="center", tags=(tag,))
        self.canvas_icon_positions[item_id] = (x, y)
        self.canvas_icon_keys[item_id] = icon_key
        self.bg_canvas.tag_bind(tag, "<ButtonPress-1>", lambda event, current_id=item_id: self._press_canvas_icon_button(current_id))
        self.bg_canvas.tag_bind(tag, "<ButtonRelease-1>", lambda event, current_id=item_id, action=command: self._release_canvas_icon_button(current_id, action, event))
        self.bg_canvas.tag_bind(tag, "<Leave>", lambda event, current_id=item_id: self._reset_canvas_icon_button(current_id))
        return item_id

    def _set_canvas_icon_button_image(self, item_id: int, icon_key: str) -> None:
        self.canvas_icon_keys[item_id] = icon_key
        self.bg_canvas.itemconfig(item_id, image=self._ensure_button_image(icon_key, "normal"))

    def _press_canvas_icon_button(self, item_id: int) -> None:
        icon_key = self.canvas_icon_keys.get(item_id)
        if icon_key is not None:
            self.bg_canvas.itemconfig(item_id, image=self._ensure_button_image(icon_key, "pressed"))
        x, y = self.bg_canvas.coords(item_id)
        self.bg_canvas.coords(item_id, x, y + 2)

    def _release_canvas_icon_button(self, item_id: int, command, event) -> None:
        self._reset_canvas_icon_button(item_id)
        if self._is_canvas_release_inside_item(item_id, event):
            command()

    def _reset_canvas_icon_button(self, item_id: int) -> None:
        original_position = self.canvas_icon_positions.get(item_id)
        if original_position is not None:
            self.bg_canvas.coords(item_id, *original_position)
        icon_key = self.canvas_icon_keys.get(item_id)
        if icon_key is not None:
            self.bg_canvas.itemconfig(item_id, image=self._ensure_button_image(icon_key, "normal"))

    def _make_round_icon_button(self, icon: str, bg: str, active_bg: str, command) -> tk.Canvas:
        button = tk.Canvas(self.root, width=58, height=58, bd=0, highlightthickness=0, bg="#000001", cursor="hand2")
        button.icon_kind = icon
        button.base_bg = bg
        button.active_bg = active_bg
        button.command = command
        self._draw_round_icon_button(button, pressed=False)
        button.bind("<ButtonPress-1>", lambda event, widget=button: self._draw_round_icon_button(widget, pressed=True))
        button.bind("<ButtonRelease-1>", lambda event, widget=button: self._handle_round_icon_release(widget, event.x, event.y))
        button.bind("<Leave>", lambda event, widget=button: self._draw_round_icon_button(widget, pressed=False))
        return button

    def _handle_round_icon_release(self, button: tk.Canvas, x: int, y: int) -> None:
        self._draw_round_icon_button(button, pressed=False)
        if 0 <= x <= 58 and 0 <= y <= 58:
            button.command()

    def _draw_round_icon_button(self, button: tk.Canvas, pressed: bool) -> None:
        button.delete("all")
        offset = 2 if pressed else 0
        shadow_color = "#111827" if not pressed else "#0f172a"
        button.create_oval(6, 8, 52, 54, fill=shadow_color, outline="")
        button.create_oval(6, 6 + offset, 52, 52 + offset, fill=button.active_bg if pressed else button.base_bg, outline="", width=0)
        if button.icon_kind == "play":
            self._draw_play_icon(button, offset)
        elif button.icon_kind == "pause":
            self._draw_pause_icon(button, offset)
        else:
            self._draw_reset_icon(button, offset)

    def _draw_play_icon(self, canvas: tk.Canvas, offset: int) -> None:
        canvas.create_polygon(24, 20 + offset, 24, 38 + offset, 39, 29 + offset, fill="#111827", outline="")

    def _draw_pause_icon(self, canvas: tk.Canvas, offset: int) -> None:
        canvas.create_rectangle(21, 19 + offset, 27, 39 + offset, fill="#111827", outline="")
        canvas.create_rectangle(31, 19 + offset, 37, 39 + offset, fill="#111827", outline="")

    def _draw_reset_icon(self, canvas: tk.Canvas, offset: int) -> None:
        canvas.create_arc(18, 16 + offset, 40, 38 + offset, start=35, extent=260, style="arc", outline="#ffffff", width=2)
        canvas.create_polygon(19, 18 + offset, 14, 18 + offset, 17, 23 + offset, fill="#ffffff", outline="")

    def _make_pressable_button(self, text: str, bg: str, active_bg: str, command, padx: int = 18, pady: int = 8) -> tk.Button:
        button = tk.Button(self.root, text=text, font=tkfont.Font(family=self.current_font_family, size=12, weight="bold"), bg=bg, fg="white", activebackground=active_bg, activeforeground="white", relief="raised", bd=2, highlightthickness=0, padx=18, pady=8, command=command, cursor="hand2")
        button.config(padx=padx, pady=pady)
        button.bind("<ButtonPress-1>", lambda event: button.config(relief="sunken"))
        button.bind("<ButtonRelease-1>", lambda event: button.config(relief="raised"))
        return button

    def _set_record_button_relief(self, relief: str) -> None:
        if hasattr(self, "record_button_frame"):
            self.record_button_frame.config(relief=relief)

    def _invoke_record_button(self) -> None:
        self._set_record_button_relief("raised")
        self.record_button.invoke()

    def _is_pointer_within_widget(self, widget: tk.Widget) -> bool:
        pointer_x, pointer_y = widget.winfo_pointerxy()
        left = widget.winfo_rootx()
        top = widget.winfo_rooty()
        right = left + widget.winfo_width()
        bottom = top + widget.winfo_height()
        return left <= pointer_x <= right and top <= pointer_y <= bottom

    def _handle_record_button_release(self, event=None) -> None:
        if self._is_pointer_within_widget(self.record_button_frame):
            self._invoke_record_button()
        else:
            self._set_record_button_relief("raised")

    def _is_canvas_release_inside_item(self, item_id: int, event) -> bool:
        bbox = self.bg_canvas.bbox(item_id)
        if bbox is None:
            return False
        left, top, right, bottom = bbox
        return left <= event.x <= right and top <= event.y <= bottom

    def _configure_record_button_style(self, text_fg: str, highlight_thickness: int, highlight_color: str) -> None:
        self.record_button.config(fg=text_fg, activeforeground=text_fg)
        self.record_button_frame.config(
            highlightthickness=highlight_thickness,
            highlightbackground=highlight_color,
            highlightcolor=highlight_color,
        )

    def apply_alert_overlay_setting(self) -> None:
        self.show_alert_overlay = self.show_alert_overlay_var.get()
        self._draw_alert_banner()

    def apply_alert_percent_setting(self) -> None:
        self.show_alert_percent = self.show_alert_percent_var.get()
        self._draw_alert_banner()

    def apply_hodulgap_banner_setting(self) -> None:
        self.show_hodulgap_banner = self.show_hodulgap_banner_var.get()
        self._draw_progress_graph(self.current_percent)

    def apply_elapsed_brush_setting(self, *_args) -> None:
        self.show_elapsed_brush = self.show_elapsed_brush_var.get()
        selected_color = self.elapsed_brush_color_var.get()
        if selected_color in ELAPSED_BRUSH_COLORS:
            self.elapsed_brush_color_name = selected_color
        self._draw_elapsed_brush()
        self.bg_canvas.tag_raise(self.elapsed_shadow_item)
        self.bg_canvas.tag_raise(self.elapsed_text_item)
        self._save_settings()

    def _apply_font_family(self, family: str) -> None:
        self.current_font_family = family
        for font_obj in [self.title_font, self.header_font, self.alert_font, self.label_font, self.value_font, self.button_font, self.banner_font, self.percent_font, self.burst_font, self.signature_font, self.icon_font]:
            font_obj.config(family=family)
        self.record_button.config(font=tkfont.Font(family=family, size=11, weight="bold"))
        self.record_paw_label.config(font=tkfont.Font(family=family, size=16, weight="bold"))
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
        self.base_elapsed_seconds = 0.0
        self.reached_70_calc_seconds = None
        self.reached_70_display_seconds = None
        self.current_percent = None
        self._stop_pause_blink()
        self._update_elapsed_display("00:00:00")
        self._set_elapsed_color("#cbd5e1")
        self.reached_70_var.set("00:00:00")
        self.remain_90_var.set("00:00:00")
        self.remain_kill_var.set("00:00:00")
        self._reset_effects()
        self._apply_default_boxes()
        self._draw_progress_graph(None)
        self._configure_record_button_style("#ffffff", 1, "#fecdd3")
        self._sync_start_button_icon()

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
                self.remain_kill_box.config(bg="#fef2f2")
            else:
                self.remain_kill_box.config(bg="#fee2e2" if int(now * 2) % 2 == 0 else "#fde68a")
        else:
            self.remain_kill_box.config(bg="#fef2f2")
        self._draw_alert_banner()

    def _draw_alert_banner(self) -> None:
        self.alert_canvas.delete("all")
        if self.reached_70_display_seconds is None:
            self._draw_sleepy_pomeranian(80, 33)
            self.alert_canvas.create_oval(126, 10, 214, 42, fill="#f8fafc", outline="#cbd5e1", width=2)
            self.alert_canvas.create_text(170, 26, text="천천히 해", fill="#475569", font=self.header_font)
            return
        if self.current_percent is not None and self.current_percent >= 95.0:
            speech = "그냥 빡딜해 빡딜~!"
        elif self.current_percent is not None and self.current_percent >= 90.0:
            speech = "빡딜 해야될 걸?"
        elif self.current_percent is not None and self.current_percent >= 85.0:
            speech = "85% 돌파!!"
        elif self.current_percent is not None and self.current_percent >= 80.0:
            speech = "80% 돌파!!"
        else:
            speech = "광 떴다!!"
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
            canvas.create_text(154, 10, text="컷 예상 시간:", fill="#7c3aed", font=self.header_font)
            canvas.create_text(258, 10, text="--:--:--", fill="#1e3a8a", font=self.header_font)
            return
        clamped_percent = max(70.0, min(100.0, current_percent))
        progress_ratio = (clamped_percent - 70.0) / 30.0
        progress_x = left + (width * progress_ratio)
        cut_expected_total = self._get_cut_expected_total_seconds()
        if cut_expected_total is not None:
            canvas.create_text(154, 10, text="컷 예상 시간:", fill="#7c3aed", font=self.header_font)
            canvas.create_text(258, 10, text=format_seconds(cut_expected_total, show_centiseconds=True), fill="#1e3a8a", font=self.header_font)
        canvas.create_rectangle(left, top, progress_x, bottom, fill="#f59e0b", outline="")
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
            text = "ㄱ ㅐ 빡 딜 !!"
        elif percent >= 95.0:
            bg_colors = ["#9a3412", "#c2410c", "#ea580c"]
            fg_colors = ["#fff7ed", "#ffffff", "#ffedd5"]
            text = "빡딜해 빡딜~!"
        else:
            bg_colors = ["#991b1b", "#b91c1c", "#dc2626"]
            fg_colors = ["#fff7ed", "#ffffff", "#fef2f2"]
            text = "보스 빡딜!!"
        canvas.create_rectangle(252, 58, 404, 78, fill=bg_colors[pulse], outline="", width=0)
        canvas.create_text(328, 68, text=text, fill=fg_colors[pulse], font=self.banner_font)

    def _apply_default_boxes(self) -> None:
        self.remain_90_box.config(bg="#eef2ff")
        self.remain_kill_box.config(bg="#fef2f2")

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
        self.save_notice_label.config(text="설정이 저장 되었습니다." if int(now * 2) % 2 == 0 else "")
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
        tk.Label(self.settings_window, text="배경 정렬", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=142)
        tk.Radiobutton(self.settings_window, text="좌상단", value="nw", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=18, y=168)
        tk.Radiobutton(self.settings_window, text="중앙", value="center", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=104, y=168)
        tk.Checkbutton(
            self.settings_window,
            text="강아지/말풍선 표시",
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
            text="퍼센트 표시",
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
            text="호들갑 오더 배너",
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
        tk.Label(self.settings_window, text="폰트", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=276)

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
        tk.Label(self.settings_window, text=f"마지막 작업일자: {LAST_UPDATED}", font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#7c3aed").place(x=18, y=378)

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
            title="배경 이미지 선택",
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
            self._draw_sleepy_pomeranian(80, 33)
            self.bg_canvas.create_oval(160, 307, 238, 333, fill="#f8fafc", outline="#cbd5e1", width=2, tags=ALERT_TAG)
            self.bg_canvas.create_text(199, 320, text="천천히 해", fill="#475569", font=self.alert_font, tags=ALERT_TAG)
            self.bg_canvas.tag_raise(ALERT_TAG, self.background_item)
            return
        if self.current_percent is not None and self.current_percent >= 94.0:
            speech = "그냥 빡딜해~"
        elif self.current_percent is not None and self.current_percent >= 90.0:
            speech = "빡딜 준비~!"
        elif self.current_percent is not None and self.current_percent >= 85.0:
            speech = "85% 돌파!!"
        elif self.current_percent is not None and self.current_percent >= 80.0:
            speech = "80% 돌파!"
        else:
            speech = "광 떳어~!"
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
            canvas.create_text(tx, ty, text="예상 시간:", fill="#7c3aed", font=self.header_font, tags=GRAPH_TAG)
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
            canvas.create_text(tx, ty, text="예상 시간:", fill="#7c3aed", font=self.header_font, tags=GRAPH_TAG)
            tx, ty = self._graph_point(258, 10)
            canvas.create_text(tx, ty, text=format_seconds(cut_expected_total, show_centiseconds=True), fill="#1e3a8a", font=self.header_font, tags=GRAPH_TAG)
        x1, y1 = self._graph_point(left, top)
        x2, y2 = self._graph_point(progress_x, bottom)
        canvas.create_rectangle(x1, y1, x2, y2, fill="#f59e0b", outline="", tags=GRAPH_TAG)
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
            text = "ㄱㅐ 빡 딜!!"
        elif percent >= 95.0:
            bg_colors = ["#9a3412", "#c2410c", "#ea580c"]
            fg_colors = ["#fff7ed", "#ffffff", "#ffedd5"]
            text = "빡딜해 빡딜~!"
        else:
            bg_colors = ["#991b1b", "#b91c1c", "#dc2626"]
            fg_colors = ["#fff7ed", "#ffffff", "#fef2f2"]
            text = "보스 빡딜!!"
        canvas = self.bg_canvas
        x1, y1 = self._graph_point(232, 73)
        x2, y2 = self._graph_point(384, 93)
        canvas.create_rectangle(x1, y1, x2, y2, fill=bg_colors[pulse], outline="", width=0, tags=GRAPH_TAG)
        tx, ty = self._graph_point(308, 83)
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
            canvas.create_text(196, 22, text="컷 예상:", fill="#ffffff", font=self.header_font, tags=GRAPH_TAG)
            canvas.create_text(296, 22, text=expected_value, fill="#1e3a8a", font=self.header_font, tags=GRAPH_TAG)
        if current_percent is None:
            canvas.tag_raise(GRAPH_TAG, self.background_item)
            return
        progress_ratio = (clamped_percent - 70.0) / 30.0
        progress_x = left + (width * progress_ratio)
        x1, y1 = self._graph_point(left, top)
        x2, y2 = self._graph_point(progress_x, bottom)
        canvas.create_rectangle(x1, y1, x2, y2, fill="#f59e0b", outline="", tags=GRAPH_TAG)
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
            self._draw_sleepy_pomeranian(80, 33)
            self.bg_canvas.create_oval(160, 307, 238, 333, fill="#f8fafc", outline="#cbd5e1", width=2, tags=ALERT_TAG)
            self.bg_canvas.create_text(199, 320, text="천천히 해", fill="#475569", font=self.alert_font, tags=ALERT_TAG)
            self.bg_canvas.tag_raise(ALERT_TAG, self.background_item)
            return
        if self.current_percent is not None and self.current_percent >= 94.0:
            speech = "그냥 빡딜 해~!!"
        elif self.current_percent is not None and self.current_percent >= 90.0:
            speech = "빡딜 준비~!"
        elif self.current_percent is not None and self.current_percent >= 85.0:
            speech = "85% 돌파!!"
        elif self.current_percent is not None and self.current_percent >= 80.0:
            speech = "80% 돌파!"
        else:
            speech = "광 떴어요~!"
        if self.show_alert_overlay:
            self._draw_sleepy_pomeranian(80, 33)
            self.bg_canvas.create_oval(160, 307, 292, 335, fill="#f8fafc", outline="#cbd5e1", width=2, tags=ALERT_TAG)
            self.bg_canvas.create_text(226, 321, text=speech, fill="#475569", font=self.alert_font, tags=ALERT_TAG)
        if self.show_alert_percent:
            self._draw_percent_burst(344, 33, self.current_percent or 70.0)
        self.bg_canvas.tag_raise(ALERT_TAG, self.background_item)

    def _apply_background(self, path: str, update_setting_var: bool = True) -> None:
        source, resolved_path = self._resolve_background_path(path)
        if not os.path.exists(resolved_path):
            if not self._is_builtin_background(source):
                messagebox.showerror("배경 오류", "선택한 이미지 파일을 찾을 수 없습니다.")
            source = DEFAULT_BG_KEY
            resolved_path = get_builtin_background_path(DEFAULT_BG_KEY)
        try:
            image = tk.PhotoImage(file=resolved_path)
        except tk.TclError:
            if not self._is_builtin_background(source):
                messagebox.showerror("배경 오류", "지원하지 않는 이미지 형식입니다.\nPNG, GIF, PPM, PGM 파일을 사용해 주세요.")
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
        tk.Checkbutton(self.settings_window, text="강아지/말풍선 표시", variable=self.show_alert_overlay_var, command=self.apply_alert_overlay_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=202)
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
        tk.Label(self.settings_window, text=f"마지막 작업일자: {LAST_UPDATED}", font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#7c3aed").place(x=18, y=460)

    def apply_settings(self) -> None:
        if self.font_family_var.get() in self.available_font_families:
            self._apply_font_family(self.font_family_var.get())
        self.background_alignment = self.background_alignment_var.get()
        self.show_alert_overlay = self.show_alert_overlay_var.get()
        self.show_alert_percent = self.show_alert_percent_var.get()
        self.show_hodulgap_banner = self.show_hodulgap_banner_var.get()
        self.show_elapsed_brush = self.show_elapsed_brush_var.get()
        if self.elapsed_brush_color_var.get() in ELAPSED_BRUSH_COLORS:
            self.elapsed_brush_color_name = self.elapsed_brush_color_var.get()
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
        tk.Checkbutton(self.settings_window, text="강아지/말풍선 표시", variable=self.show_alert_overlay_var, command=self.apply_alert_overlay_setting, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", highlightthickness=0, bd=0).place(x=18, y=202)
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
        self.save_notice_label = tk.Label(self.settings_window, text="", font=self.button_font, bg="#f8f1df", fg="#b45309")
        tk.Label(self.settings_window, text=f"Made by {AUTHOR_NAME}", font=(self.current_font_family, 10, "bold"), bg="#f8f1df", fg="#b45309").place(x=18, y=422)
        tk.Label(self.settings_window, text=APP_VERSION, font=(self.current_font_family, 10, "bold"), bg="#f8f1df", fg="#b45309").place(x=132, y=422)
        tk.Label(self.settings_window, text=f"마지막 작업일자: {LAST_UPDATED}", font=(self.current_font_family, 10, "bold"), bg="#f8f1df", fg="#7c3aed").place(x=18, y=440)

    def close_settings_window(self) -> None:
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.update_idletasks()
            self.settings_window_x = self.settings_window.winfo_x()
            self.settings_window_y = self.settings_window.winfo_y()
            self._save_settings()
            self.settings_window.destroy()

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

import configparser
import math
import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import font as tkfont


WINDOW_WIDTH = 470
WINDOW_HEIGHT = 438
APP_VERSION = "v1.0.0"
LAST_UPDATED = "2026-03-16"
AUTHOR_NAME = "나츠"
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "boss_timer_settings.ini")
DEFAULT_BG_PATH = os.path.join(os.path.dirname(__file__), "assets", "기본배경.png")
ALT_BG_PATH = os.path.join(os.path.dirname(__file__), "assets", "벽지.png")
JANG_WONYOUNG_BG_PATH = os.path.join(os.path.dirname(__file__), "assets", "장원영.png")


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
        self.current_font_family = "Arial Black" if "Arial Black" in self.available_font_families else self.available_font_families[0]
        self.background_path = DEFAULT_BG_PATH
        self.background_alignment = "nw"
        self.main_window_x = 100
        self.main_window_y = 100
        self.settings_window_x = 140
        self.settings_window_y = 140
        self._load_settings()
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{self.main_window_x}+{self.main_window_y}")

        self.font_family_var = tk.StringVar(value=self.current_font_family)
        self.settings_path_var = tk.StringVar(value=self.background_path)
        self.background_alignment_var = tk.StringVar(value=self.background_alignment)

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
        self.settings_notice_after_id = None
        self.settings_notice_end_time = 0.0

        self.title_font = tkfont.Font(family=self.current_font_family, size=26, weight="bold")
        self.header_font = tkfont.Font(family=self.current_font_family, size=13, weight="bold")
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

    def _load_settings(self) -> None:
        config = configparser.ConfigParser()
        if not os.path.exists(CONFIG_PATH):
            self._save_settings()
            return
        config.read(CONFIG_PATH, encoding="utf-8")
        if "settings" not in config:
            return
        settings = config["settings"]
        saved_bg = settings.get("background_path", DEFAULT_BG_PATH)
        saved_font = settings.get("font_family", "Arial Black")
        saved_alignment = settings.get("background_alignment", "center")
        self.main_window_x = self._parse_int(settings.get("main_window_x"), self.main_window_x)
        self.main_window_y = self._parse_int(settings.get("main_window_y"), self.main_window_y)
        self.settings_window_x = self._parse_int(settings.get("settings_window_x"), self.settings_window_x)
        self.settings_window_y = self._parse_int(settings.get("settings_window_y"), self.settings_window_y)
        if os.path.exists(saved_bg):
            self.background_path = saved_bg
        if saved_font in self.available_font_families:
            self.current_font_family = saved_font
        if saved_alignment in {"center", "nw"}:
            self.background_alignment = saved_alignment

    def _parse_int(self, value: str | None, fallback: int) -> int:
        try:
            return int(value) if value is not None else fallback
        except (TypeError, ValueError):
            return fallback

    def _save_settings(self) -> None:
        config = configparser.ConfigParser()
        config["settings"] = {
            "background_path": self.background_path,
            "font_family": self.current_font_family,
            "background_alignment": self.background_alignment,
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
        self.bg_canvas = tk.Canvas(self.root, width=WINDOW_WIDTH, height=WINDOW_HEIGHT, bd=0, highlightthickness=0)
        self.bg_canvas.pack(fill="both", expand=True)
        self.background_item = self.bg_canvas.create_image(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2, anchor="center")

        self._draw_brush_stroke(self.bg_canvas, 18, 40, 92, 26, "#e84141")
        self.bg_canvas.create_text(33, 56, anchor="w", text="총 시간", font=self.header_font, fill="#ffffff")

        self.settings_button = tk.Button(
            self.root,
            text="⚙",
            font=self.icon_font,
            bg="#1e293b",
            fg="white",
            activebackground="#334155",
            activeforeground="white",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=5,
            pady=1,
            command=self.open_settings_window,
            cursor="hand2",
        )
        self.bg_canvas.create_window(446, 16, anchor="ne", window=self.settings_button)

        self.elapsed_label = tk.Label(
            self.root,
            textvariable=self.elapsed_var,
            font=self.title_font,
            bg="#000001",
            fg="#cbd5e1",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=10,
            pady=4,
        )
        self.bg_canvas.create_window(236, 30, anchor="n", window=self.elapsed_label, width=230)

        self.start_button = self._make_action_button("시작", "#16a34a", "#15803d", self.start_timer)
        self.stop_button = self._make_action_button("일시 정지", "#475569", "#334155", self.stop_timer)
        self.reset_button = self._make_action_button("초기화", "#2563eb", "#1d4ed8", self.reset_timer)
        self.bg_canvas.create_window(146, 91, anchor="n", window=self.start_button)
        self.bg_canvas.create_window(236, 91, anchor="n", window=self.stop_button)
        self.bg_canvas.create_window(326, 91, anchor="n", window=self.reset_button)

        self._create_timer_row(152, "70%(광)", self.reached_70_var, "#f3e8ff", "#111827", label_fg="#ffffff", brush_color="#c65d1e")
        self.record_button = self._make_pressable_button("광 체크", "#f59e0b", "#d97706", self.record_70_percent_time, padx=12, pady=2)
        self.bg_canvas.create_window(404, 198, anchor="center", window=self.record_button)

        self._create_timer_row(198, "컷 남은", self.remain_kill_var, "#fef2f2", "#dc2626", store_as="remain_kill_box", label_fg="#ffffff", brush_color="#8f2b2b")
        self._create_timer_row(236, "90%", self.remain_90_var, "#eef2ff", "#ea580c", store_as="remain_90_box", visible=False)

        self.alert_canvas = tk.Canvas(self.root, width=414, height=62, bg="#dbeafe", bd=0, highlightthickness=0)
        self.bg_canvas.create_window(236, 234, anchor="n", window=self.alert_canvas)

        self.graph_canvas = tk.Canvas(self.root, width=414, height=82, bg="#dbeafe", bd=0, highlightthickness=0)
        self.bg_canvas.create_window(236, 300, anchor="n", window=self.graph_canvas)

        self._draw_brush_stroke(self.bg_canvas, 0, WINDOW_HEIGHT - 28, 96, 22, "#d8bea1")
        self.bg_canvas.create_text(10, WINDOW_HEIGHT - 8, anchor="sw", text="밤비 is Back", font=self.signature_font, fill="#4a2c1d")
        self.bg_canvas.create_text(WINDOW_WIDTH - 10, WINDOW_HEIGHT - 8, anchor="se", text=APP_VERSION, font=self.percent_font, fill="#9ca3af")

    def _draw_brush_stroke(self, canvas: tk.Canvas, x: int, y: int, width: int, height: int, color: str) -> None:
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
        canvas.create_polygon(points, fill=color, outline="", smooth=True, splinesteps=12)

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

    def _make_pressable_button(self, text: str, bg: str, active_bg: str, command, padx: int = 18, pady: int = 8) -> tk.Button:
        button = tk.Button(self.root, text=text, font=tkfont.Font(family=self.current_font_family, size=12, weight="bold"), bg=bg, fg="white", activebackground=active_bg, activeforeground="white", relief="raised", bd=2, highlightthickness=0, padx=18, pady=8, command=command, cursor="hand2")
        button.config(padx=padx, pady=pady)
        button.bind("<ButtonPress-1>", lambda event: button.config(relief="sunken"))
        button.bind("<ButtonRelease-1>", lambda event: button.config(relief="raised"))
        return button

    def _apply_font_family(self, family: str) -> None:
        self.current_font_family = family
        for font_obj in [self.title_font, self.header_font, self.label_font, self.value_font, self.button_font, self.banner_font, self.percent_font, self.burst_font, self.signature_font, self.icon_font]:
            font_obj.config(family=family)
        self.settings_button.config(font=self.icon_font)
        self.record_button.config(font=tkfont.Font(family=family, size=12, weight="bold"))
        self._draw_progress_graph(self.current_percent)
        self._draw_alert_banner()

    def _now_elapsed(self) -> float:
        return self.base_elapsed_seconds + (time.perf_counter() - self.start_perf_time) if self.running else self.base_elapsed_seconds

    def start_timer(self) -> None:
        if self.running:
            return
        self.running = True
        self.start_perf_time = time.perf_counter()
        self.elapsed_label.config(fg="#16a34a")
        self._schedule_update()

    def stop_timer(self) -> None:
        if self.running:
            self.base_elapsed_seconds = self._now_elapsed()
        self.running = False
        self.elapsed_label.config(fg="#cbd5e1")
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
        self.elapsed_var.set("00:00:00")
        self.elapsed_label.config(fg="#cbd5e1")
        self.reached_70_var.set("00:00:00")
        self.remain_90_var.set("00:00:00")
        self.remain_kill_var.set("00:00:00")
        self._reset_effects()
        self._apply_default_boxes()
        self._draw_progress_graph(None)
        self.record_button.config(
            fg="white",
            highlightthickness=0,
            highlightbackground="#f59e0b",
            highlightcolor="#f59e0b",
        )

    def record_70_percent_time(self) -> None:
        current_elapsed = self._now_elapsed()
        self.reached_70_calc_seconds = int(current_elapsed)
        self.reached_70_display_seconds = current_elapsed
        self.reached_70_var.set(format_seconds(current_elapsed, show_centiseconds=True))
        self.record_button.config(
            fg="white",
            highlightthickness=2,
            highlightbackground="#7f1d1d",
            highlightcolor="#7f1d1d",
        )
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

    def _start_pause_blink(self) -> None:
        self._stop_pause_blink()
        if self.base_elapsed_seconds <= 0:
            return
        self.pause_blink_on = True
        self._pause_blink_tick()

    def _pause_blink_tick(self) -> None:
        if self.running:
            return
        self.elapsed_label.config(fg="#cbd5e1" if self.pause_blink_on else "#6b7280")
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
        self.elapsed_var.set(format_seconds(current_elapsed, show_centiseconds=True))
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
            self.settings_notice_after_id = None
            return
        self.save_notice_label.config(text="설정이 저장 되었습니다." if int(now * 2) % 2 == 0 else "")
        self.settings_notice_after_id = self.settings_window.after(500, self._flash_save_notice)

    def open_settings_window(self) -> None:
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.focus_force()
            return
        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title("환경설정")
        self.settings_window.geometry(f"430x320+{self.settings_window_x}+{self.settings_window_y}")
        self.settings_window.resizable(False, False)
        self.settings_window.protocol("WM_DELETE_WINDOW", self.close_settings_window)
        self.settings_bg_label = tk.Label(self.settings_window, bd=0)
        self.settings_bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        if self.background_image is not None:
            self.settings_bg_label.config(image=self.background_image)

        tk.Label(self.settings_window, text="환경설정", font=self.header_font, bg="#000001", fg="#f8fafc").place(x=18, y=14)
        tk.Label(self.settings_window, text="배경 이미지", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=48)
        tk.Entry(self.settings_window, textvariable=self.settings_path_var, font=(self.current_font_family, 10), width=33, bd=0, highlightthickness=0).place(x=18, y=76, width=276, height=26)
        tk.Button(self.settings_window, text="파일 선택", font=self.button_font, bg="#1e293b", fg="white", activebackground="#334155", activeforeground="white", relief="flat", bd=0, highlightthickness=0, command=self.select_background_file, cursor="hand2").place(x=304, y=74, width=98, height=28)
        tk.Button(self.settings_window, text="기본배경", font=self.button_font, bg="#1e293b", fg="white", activebackground="#334155", activeforeground="white", relief="flat", bd=0, highlightthickness=0, command=self.apply_default_background, cursor="hand2").place(x=18, y=106, width=86, height=28)
        tk.Button(self.settings_window, text="벽지", font=self.button_font, bg="#1e293b", fg="white", activebackground="#334155", activeforeground="white", relief="flat", bd=0, highlightthickness=0, command=self.apply_blue_wallpaper, cursor="hand2").place(x=112, y=106, width=86, height=28)
        tk.Button(self.settings_window, text="장원영", font=self.button_font, bg="#1e293b", fg="white", activebackground="#334155", activeforeground="white", relief="flat", bd=0, highlightthickness=0, command=self.apply_jang_wonyoung_background, cursor="hand2").place(x=206, y=106, width=86, height=28)
        tk.Label(self.settings_window, text="배경 정렬", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=142)
        tk.Radiobutton(self.settings_window, text="좌상단", value="nw", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=18, y=168)
        tk.Radiobutton(self.settings_window, text="중앙", value="center", variable=self.background_alignment_var, font=self.button_font, bg="#000001", fg="#f8fafc", selectcolor="#1e293b", activebackground="#000001", activeforeground="#f8fafc", command=self.apply_background_alignment).place(x=104, y=168)
        tk.Label(self.settings_window, text="폰트", font=self.label_font, bg="#000001", fg="#f8fafc").place(x=18, y=206)

        self.font_menu = tk.OptionMenu(self.settings_window, self.font_family_var, *self.available_font_families)
        self.font_menu.config(font=self.button_font, bg="#1e293b", fg="white", activebackground="#334155", activeforeground="white", highlightthickness=0, bd=0)
        self.font_menu["menu"].config(font=(self.current_font_family, 9))
        self.font_menu.place(x=18, y=234, width=276, height=30)

        self.apply_button = tk.Button(self.settings_window, text="저장", font=self.button_font, bg="#2563eb", fg="white", activebackground="#1d4ed8", activeforeground="white", relief="flat", bd=0, highlightthickness=0, command=self.apply_settings, cursor="hand2")
        self.apply_button.place(x=304, y=282, width=98, height=30)

        self.save_notice_label = tk.Label(self.settings_window, text="", font=self.button_font, bg="#000001", fg="#fef08a")
        self.save_notice_label.place(x=18, y=258)
        tk.Label(self.settings_window, text=f"Made by {AUTHOR_NAME}", font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#b45309").place(x=18, y=276)
        tk.Label(self.settings_window, text=APP_VERSION, font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#b45309").place(x=132, y=276)
        tk.Label(self.settings_window, text=f"마지막 작업일자: {LAST_UPDATED}", font=(self.current_font_family, 10, "bold"), bg="#000001", fg="#7c3aed").place(x=18, y=296)

    def apply_settings(self) -> None:
        if self.font_family_var.get() in self.available_font_families:
            self._apply_font_family(self.font_family_var.get())
        self.background_alignment = self.background_alignment_var.get()
        self._apply_background(self.settings_path_var.get().strip())
        self._update_window_positions()
        self._save_settings()
        if self.settings_notice_after_id is not None and hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.after_cancel(self.settings_notice_after_id)
        self.settings_notice_end_time = time.perf_counter() + 3.0
        self._flash_save_notice()

    def select_background_file(self) -> None:
        path = filedialog.askopenfilename(title="배경 이미지 선택", filetypes=[("Image Files", "*.png *.gif *.ppm *.pgm"), ("All Files", "*.*")])
        if path:
            self.settings_path_var.set(path)
            self._apply_background(path)

    def apply_default_background(self) -> None:
        self.settings_path_var.set(DEFAULT_BG_PATH)
        self._apply_background(DEFAULT_BG_PATH)

    def apply_blue_wallpaper(self) -> None:
        self.settings_path_var.set(ALT_BG_PATH)
        self._apply_background(ALT_BG_PATH)

    def apply_jang_wonyoung_background(self) -> None:
        self.settings_path_var.set(JANG_WONYOUNG_BG_PATH)
        self._apply_background(JANG_WONYOUNG_BG_PATH)

    def apply_background_alignment(self) -> None:
        self.background_alignment = self.background_alignment_var.get()
        self._apply_background(self.background_path, update_setting_var=False)

    def _apply_background(self, path: str, update_setting_var: bool = True) -> None:
        if not path:
            path = DEFAULT_BG_PATH
        if not os.path.exists(path):
            if path != DEFAULT_BG_PATH:
                messagebox.showerror("배경 오류", "선택한 이미지 파일을 찾을 수 없습니다.")
            path = DEFAULT_BG_PATH
        try:
            image = tk.PhotoImage(file=path)
        except tk.TclError:
            if path != DEFAULT_BG_PATH:
                messagebox.showerror("배경 오류", "지원하지 않는 이미지 형식입니다.\nPNG, GIF, PPM, PGM 파일을 사용해 주세요.")
                path = DEFAULT_BG_PATH
                image = tk.PhotoImage(file=path)
            else:
                raise
        self.background_image = image
        self.background_path = path
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
            self.settings_path_var.set(path)

    def _update_window_positions(self) -> None:
        self.root.update_idletasks()
        self.main_window_x = self.root.winfo_x()
        self.main_window_y = self.root.winfo_y()
        if hasattr(self, "settings_window") and self.settings_window.winfo_exists():
            self.settings_window.update_idletasks()
            self.settings_window_x = self.settings_window.winfo_x()
            self.settings_window_y = self.settings_window.winfo_y()

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
    root = tk.Tk()
    app = BossTimerApp(root)
    app.elapsed_label.config(fg="#cbd5e1")
    root.mainloop()


if __name__ == "__main__":
    main()

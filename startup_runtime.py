"""Early Windows GUI guard and responsive startup view, without GUI imports.

The loader owns a native window/message loop on a separate thread. It never
uses Tk objects, launches another EXE, or waits for the application's Tk loop.
"""
import atexit
import ctypes
from ctypes import wintypes as w
import hashlib
import os
import sys
import threading
import time


STAGES = (
    "프로그램 불러오기", "시즌·서버 자료 확인", "기본 자료·설정 준비",
    "보스·음성 준비", "기록 읽기", "스케줄 읽기", "화면 구성",
)


def _close_boot_splash():
    if not bool(getattr(sys, "frozen", False)):
        return
    try:
        import pyi_splash
        if pyi_splash.is_alive():
            pyi_splash.close()
    except (ImportError, RuntimeError, OSError):
        pass


def _api(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, list(args)
    return function


class WindowsStartup:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        H, P, U, I = w.HANDLE, ctypes.c_void_p, w.UINT, ctypes.c_int
        self.close_handle = _api(self.kernel, "CloseHandle", w.BOOL, H)
        self.wait = _api(self.kernel, "WaitForSingleObject", w.DWORD, H, w.DWORD)
        self.reset = _api(self.kernel, "ResetEvent", w.BOOL, H)
        self.set_event = _api(self.kernel, "SetEvent", w.BOOL, H)
        self.create_mutex = _api(self.kernel, "CreateMutexExW", H, P, w.LPCWSTR, w.DWORD, w.DWORD)
        self.create_event = _api(self.kernel, "CreateEventExW", H, P, w.LPCWSTR, w.DWORD, w.DWORD)
        self.post = _api(self.user, "PostMessageW", w.BOOL, w.HWND, U, w.WPARAM, w.LPARAM)
        self.show = _api(self.user, "ShowWindowAsync", w.BOOL, w.HWND, I)
        self.foreground = _api(self.user, "SetForegroundWindow", w.BOOL, w.HWND)
        self.position = _api(self.user, "SetWindowPos", w.BOOL, w.HWND, w.HWND, I, I, I, I, U)
        self.get_prop = _api(self.user, "GetPropW", H, w.HWND, w.LPCWSTR)
        self.set_prop = _api(self.user, "SetPropW", w.BOOL, w.HWND, w.LPCWSTR, H)
        self.remove_prop = _api(self.user, "RemovePropW", H, w.HWND, w.LPCWSTR)
        self.get_parent = _api(self.user, "GetParent", w.HWND, w.HWND)
        self.get_popup = _api(self.user, "GetLastActivePopup", w.HWND, w.HWND)
        self.visible = _api(self.user, "IsWindowVisible", w.BOOL, w.HWND)
        self.get_pid = _api(self.user, "GetWindowThreadProcessId", w.DWORD, w.HWND, ctypes.POINTER(w.DWORD))
        self.allow_foreground = _api(self.user, "AllowSetForegroundWindow", w.BOOL, w.DWORD)
        self.started = time.perf_counter()
        self.stage_index, self.detail = 0, "저장된 설정과 스케줄을 준비하고 있습니다."
        self.timings = []
        self.stage_started = self.started
        self.hwnd = self.root_hwnd = None
        self.event = self.mutex = None
        self.finished = False
        self.stop = threading.Event()
        self.hidden = threading.Event()
        self.root = None
        self._mutex_lock = threading.Lock()
        self.sid = self._user_sid()
        key = hashlib.sha256(self.sid.encode("ascii")).hexdigest()[:24]
        self.name = "Local\\BossTimer.GUI." + key
        self.property_name = "BossTimer.GUI." + key

    def _user_sid(self):
        token = w.HANDLE()
        get_process = _api(self.kernel, "GetCurrentProcess", w.HANDLE)
        open_token = _api(self.advapi, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
        token_info = _api(self.advapi, "GetTokenInformation", w.BOOL, w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD))
        convert = _api(self.advapi, "ConvertSidToStringSidW", w.BOOL, ctypes.c_void_p, ctypes.POINTER(w.LPWSTR))
        free = _api(self.kernel, "LocalFree", ctypes.c_void_p, ctypes.c_void_p)
        if not open_token(get_process(), 0x8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            length = w.DWORD()
            token_info(token, 1, None, 0, ctypes.byref(length))
            data = ctypes.create_string_buffer(length.value)
            if not token_info(token, 1, data, length, ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            result = w.LPWSTR()
            if not convert(sid, ctypes.byref(result)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return result.value
            finally:
                free(ctypes.cast(result, ctypes.c_void_p))
        finally:
            self.close_handle(token)

    def acquire(self):
        # Same-user normal/elevated launches must share the guard. The medium
        # label allows a normal launch to signal its elevated predecessor.
        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("length", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]
        descriptor = ctypes.c_void_p()
        convert = _api(self.advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL,
                       w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.DWORD))
        sddl = f"D:(A;;GA;;;{self.sid})(A;;GA;;;SY)S:(ML;;NW;;;ME)"
        if not convert(sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        free = _api(self.kernel, "LocalFree", ctypes.c_void_p, ctypes.c_void_p)
        try:
            ctypes.set_last_error(0)
            self.mutex = self.create_mutex(ctypes.byref(attributes), self.name, 0, 0x100000)
            error = ctypes.get_last_error()
            if not self.mutex:
                raise ctypes.WinError(error)
            existing = error == 183
            self.event = self.create_event(ctypes.byref(attributes), self.name + ".activate", 1, 0x100002)
            if not self.event:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            free(descriptor)
        if existing:
            self._activate_existing()
            self.set_event(self.event)
            _close_boot_splash()
            self.close()
            return False
        atexit.register(self.close)
        return True

    def _raise_window(self, hwnd):
        if not hwnd:
            return
        pid = w.DWORD()
        self.get_pid(hwnd, ctypes.byref(pid))
        self.allow_foreground(pid.value)
        self.show(hwnd, 9)  # SW_RESTORE; asynchronous even during initialization.
        self.position(hwnd, 0, 0, 0, 0, 0, 0x4003)  # Async TOP, keep position/size.
        self.foreground(hwnd)
        popup = self.get_popup(hwnd)
        if popup and popup != hwnd and self.visible(popup):
            self.show(popup, 9)
            self.foreground(popup)

    def _activate_existing(self):
        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        @callback_type
        def visit(hwnd, _):
            if self.get_prop(hwnd, self.property_name) and self.visible(hwnd):
                self._raise_window(hwnd)
            return True
        enumerate_windows = _api(self.user, "EnumWindows", w.BOOL, callback_type, w.LPARAM)
        enumerate_windows(visit, 0)

    def start_view(self):
        # Set DPI before either native controls or Tk creates a window.
        try:
            dpi = _api(self.user, "SetProcessDpiAwarenessContext", w.BOOL, ctypes.c_void_p)
            dpi(ctypes.c_void_p(-2))  # Same system-DPI mode as the existing GUI.
        except AttributeError:
            pass
        self.thread = threading.Thread(target=self._view, name="boss-timer-startup-view", daemon=True)
        self.thread.start()

    def stage(self, index, detail=""):
        now = time.perf_counter()
        self.timings.append((STAGES[self.stage_index], now - self.stage_started))
        self.stage_started = now
        self.stage_index = max(0, min(len(STAGES) - 1, int(index)))
        self.detail = str(detail or "저장된 설정과 스케줄을 준비하고 있습니다.")

    def attach(self, root):
        self.root = root
        hwnd = root.winfo_id()
        self.root_hwnd = self.get_parent(hwnd) or hwnd
        self.set_prop(self.root_hwnd, self.property_name, 1)
        root.after(100, self._poll_activation)

    def _poll_activation(self):
        if self.root is None:
            return
        try:
            if self.event and self.wait(self.event, 0) == 0:
                self.reset(self.event)
                if self.hidden.is_set() or self.finished:
                    self.root.deiconify()
                    self.root.lift()
                    self._raise_window(self.root_hwnd)
                else:
                    self._raise_window(self.hwnd)
            self.root.after(100, self._poll_activation)
        except Exception:
            return  # Root may have been destroyed during normal shutdown.

    def reveal(self, root):
        """Expose the main window before a first-run season/settings prompt."""
        self.hidden.set()
        if self.hwnd:
            self.show(self.hwnd, 0)
        root.deiconify()

    def finish(self, logger=None):
        if self.finished:
            return
        now = time.perf_counter()
        self.timings.append((STAGES[self.stage_index], now - self.stage_started))
        self.finished = True
        self.stop.set()
        if self.hwnd:
            self.post(self.hwnd, 0x8001, 0, 0)
        if logger:
            self._save_timings(now - self.started)
            logger("startup_ready total_seconds={:.3f} stages={}".format(
                now - self.started, ", ".join(f"{name}:{seconds:.3f}s" for name, seconds in self.timings)))

    def _save_timings(self, elapsed):
        # A single latest summary is useful even when release debug logs are off.
        import json
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, "BossTimer")
        target = os.path.join(folder, "startup_timing.json")
        temporary = target + f".{os.getpid()}.tmp"
        try:
            os.makedirs(folder, exist_ok=True)
            payload = {"schema": 1, "ready_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "total_seconds": round(elapsed, 3),
                       "stages": [{"name": name, "seconds": round(seconds, 3)} for name, seconds in self.timings]}
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, target)
        except OSError:
            pass
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    def close(self):
        self.finish()
        thread = getattr(self, "thread", None)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        # The guard is kept until interpreter exit, including bot shutdown.
        with self._mutex_lock:
            for name in ("event", "mutex"):
                handle = getattr(self, name, None)
                if handle:
                    self.close_handle(handle)
                    setattr(self, name, None)

    def _view(self):
        try:
            self._run_view()
        except Exception:
            # A display failure must not disable the already-acquired guard.
            if self.hwnd:
                destroy = _api(self.user, "DestroyWindow", w.BOOL, w.HWND)
                destroy(self.hwnd)
                self.hwnd = None
            _close_boot_splash()

    def _run_view(self):
        U, I, P = w.UINT, ctypes.c_int, ctypes.c_void_p
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, U, w.WPARAM, w.LPARAM)
        class WindowClass(ctypes.Structure):
            _fields_ = [("style", U), ("proc", callback_type), ("class_extra", I), ("window_extra", I),
                        ("instance", w.HINSTANCE), ("icon", w.HICON), ("cursor", w.HANDLE),
                        ("background", w.HBRUSH), ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]
        create = _api(self.user, "CreateWindowExW", w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR,
                      w.DWORD, I, I, I, I, w.HWND, w.HMENU, w.HINSTANCE, P)
        default = _api(self.user, "DefWindowProcW", ctypes.c_ssize_t, w.HWND, U, w.WPARAM, w.LPARAM)
        destroy = _api(self.user, "DestroyWindow", w.BOOL, w.HWND)
        quit_loop = _api(self.user, "PostQuitMessage", None, I)
        brush = _api(self.gdi, "CreateSolidBrush", w.HBRUSH, w.DWORD)
        delete_object = _api(self.gdi, "DeleteObject", w.BOOL, w.HANDLE)
        text_color = _api(self.gdi, "SetTextColor", w.DWORD, w.HDC, w.DWORD)
        background_color = _api(self.gdi, "SetBkColor", w.DWORD, w.HDC, w.DWORD)
        set_text = _api(self.user, "SetWindowTextW", w.BOOL, w.HWND, w.LPCWSTR)
        blue, white = 0x9E3A23, 0xFFFFFF
        blue_brush, white_brush = brush(blue), brush(white)
        controls = {}
        last_text = None
        try:
            scale = _api(self.user, "GetDpiForSystem", U)() / 96.0
        except AttributeError:
            scale = 1.0
        def px(value):
            return round(value * scale)
        @callback_type
        def procedure(hwnd, message, wp, lp):
            nonlocal last_text
            try:
                if message == 0x113:  # WM_TIMER, native loop stays live during imports/IO.
                    if self.stop.is_set():
                        destroy(hwnd)
                        return 0
                    if self.event and not self.hidden.is_set() and self.wait(self.event, 0) == 0:
                        self.reset(self.event)
                        self._raise_window(hwnd)
                    elapsed = time.perf_counter() - self.started
                    state = (self.stage_index, self.detail, int(elapsed))
                    if state != last_text:
                        set_text(controls['stage'], f"{self.stage_index + 1}/{len(STAGES)} · {STAGES[self.stage_index]}")
                        set_text(controls['detail'], self.detail)
                        set_text(controls['elapsed'], f"경과 {int(elapsed)}초 · 실행 중입니다. 잠시만 기다려주세요.")
                        last_text = state
                    self.position(controls['bar'], 0, px(24 + int(elapsed * 100) % 354), px(145), px(90), px(6), 0x14)
                    return 0
                if message == 0x8001:
                    destroy(hwnd)
                    return 0
                if message == 0x10:  # Dismiss the display, not the initialization.
                    self.show(hwnd, 0)
                    return 0
                if message == 0x2:
                    self.remove_prop(hwnd, self.property_name)
                    self.hwnd = None
                    quit_loop(0)
                    return 0
                if message == 0x138:  # WM_CTLCOLORSTATIC
                    header = lp == controls.get('header') or lp == controls.get('bar')
                    text_color(wp, white if header else 0x37271F)
                    background_color(wp, blue if header else white)
                    return blue_brush if header else white_brush
            except Exception:
                # ctypes callbacks must never let a Python exception escape.
                return default(hwnd, message, wp, lp)
            return default(hwnd, message, wp, lp)
        instance = _api(self.kernel, "GetModuleHandleW", w.HINSTANCE, w.LPCWSTR)(None)
        cursor = _api(self.user, "LoadCursorW", w.HANDLE, w.HINSTANCE, P)(None, ctypes.c_void_p(32512))
        name = "BossTimerStartup." + str(os.getpid())
        window_class = WindowClass(0, procedure, 0, 0, instance, None, cursor, white_brush, None, name)
        register = _api(self.user, "RegisterClassW", w.ATOM, ctypes.POINTER(WindowClass))
        unregister = _api(self.user, "UnregisterClassW", w.BOOL, w.LPCWSTR, w.HINSTANCE)
        if not register(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        fonts = []
        try:
            work = w.RECT()
            get_work = _api(self.user, "SystemParametersInfoW", w.BOOL, U, U, P, U)
            get_work(0x30, 0, ctypes.byref(work), 0)
            width, height = px(500), px(238)
            hwnd = create(0x40000, name, "보스전 타이머 시작 중", 0xCA0000,
                          work.left + (work.right - work.left - width) // 2,
                          work.top + (work.bottom - work.top - height) // 2,
                          width, height, None, None, instance, None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self.hwnd = hwnd
            self.set_prop(hwnd, self.property_name, 1)
            font = _api(self.gdi, "CreateFontW", w.HFONT, I, I, I, I, I, w.DWORD, w.DWORD,
                        w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.LPCWSTR)
            normal = font(-px(14), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Malgun Gothic")
            bold = font(-px(17), 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 5, 0, "Malgun Gothic")
            fonts.extend((normal, bold))
            send = _api(self.user, "SendMessageW", ctypes.c_ssize_t, w.HWND, U, w.WPARAM, w.LPARAM)
            def label(key, text, x, y, width, height, *, heading=False):
                control = create(0, "STATIC", text, 0x50000000, px(x), px(y), px(width), px(height), hwnd, None, instance, None)
                controls[key] = control
                send(control, 0x30, bold if heading else normal, 1)
            label('header', '  보스전 타이머 시작 중', 0, 0, 500, 43, heading=True)
            label('stage', '1/7 · 프로그램 불러오기', 24, 59, 445, 28, heading=True)
            label('detail', self.detail, 24, 96, 445, 43)
            label('bar', '', 24, 145, 90, 6)
            label('elapsed', '실행 중입니다. 잠시만 기다려주세요.', 24, 168, 445, 25)
            if not self.stop.is_set() and not self.hidden.is_set():
                self.show(hwnd, 5)
                self.foreground(hwnd)
            _close_boot_splash()
            _api(self.user, "SetTimer", ctypes.c_size_t, w.HWND, ctypes.c_size_t, U, P)(hwnd, 1, 80, None)
            message = w.MSG()
            get_message = _api(self.user, "GetMessageW", I, ctypes.POINTER(w.MSG), w.HWND, U, U)
            translate = _api(self.user, "TranslateMessage", w.BOOL, ctypes.POINTER(w.MSG))
            dispatch = _api(self.user, "DispatchMessageW", ctypes.c_ssize_t, ctypes.POINTER(w.MSG))
            while get_message(ctypes.byref(message), None, 0, 0) > 0:
                translate(ctypes.byref(message))
                dispatch(ctypes.byref(message))
        finally:
            if self.hwnd:
                destroy(self.hwnd)
            unregister(name, instance)
            for handle in fonts + [blue_brush, white_brush]:
                if handle:
                    delete_object(handle)


def bootstrap():
    """Called before heavy imports; scheduler/bot processes have their own life."""
    executable = os.path.basename(sys.executable).lower()
    if (os.name != "nt" or "--scheduler-worker" in sys.argv[1:]
            or str(os.environ.get("BOSS_TIMER_SCHEDULER_WORKER") or "").strip() == "1"
            or "scheduler" in executable):
        _close_boot_splash()
        return None
    runtime = WindowsStartup()
    try:
        if not runtime.acquire():
            raise SystemExit(0)
        runtime.start_view()
    except BaseException:
        runtime.close()
        _close_boot_splash()
        raise
    return runtime

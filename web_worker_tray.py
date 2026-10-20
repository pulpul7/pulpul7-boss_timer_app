"""Worker-owned Windows notification icon. No Tk, GUI, polling or extra packages."""
import ctypes
from ctypes import wintypes as w
import os
import threading

from web_service_common import diagnostic

CALLBACK = 0x8001
REFRESH = 0x8002
STOP_COMMAND = 1


def _api(dll, name, result, *arguments):
    call = getattr(dll, name)
    call.restype, call.argtypes = result, list(arguments)
    return call


class WebWorkerTray:
    def __init__(self, logger):
        self.logger = logger
        self.stop_requested = threading.Event()
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.thread = None
        self.hwnd = None
        self.post = None
        self.error = None
        self._stop_lock = threading.Lock()

    def start(self):
        if os.name == 'nt' and self.thread is None:
            self.thread = threading.Thread(target=self._run, name='boss-web-tray', daemon=True)
            self.thread.start()

    def request_stop(self):
        with self._stop_lock:
            if self.closed.is_set() or self.stop_requested.is_set():
                return
            self.stop_requested.set()
        self._post(REFRESH)

    def retry_stop(self):
        # If the durable intent could not be saved, allow another menu attempt.
        self.stop_requested.clear()
        self._post(REFRESH)

    def _post(self, message):
        if self.hwnd and self.post:
            self.post(self.hwnd, message, 0, 0)

    def close(self):
        self.closed.set()
        self._post(0x10)  # WM_CLOSE, on the owning UI thread.
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)

    def _run(self):
        try:
            self._message_loop()
        except Exception as exc:
            self.error = type(exc).__name__
            diagnostic(self.logger, 'web_tray_failed', error_type=self.error)
        finally:
            self.ready.set()

    def _message_loop(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        P, I, U = ctypes.c_void_p, ctypes.c_int, w.UINT
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, U, w.WPARAM, w.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [('style', U), ('proc', callback_type), ('class_extra', I), ('window_extra', I),
                        ('instance', w.HINSTANCE), ('icon', w.HICON), ('cursor', w.HANDLE),
                        ('background', w.HBRUSH), ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]

        class Guid(ctypes.Structure):
            _fields_ = [('a', w.DWORD), ('b', w.WORD), ('c', w.WORD), ('d', w.BYTE * 8)]

        class NotifyIcon(ctypes.Structure):
            _fields_ = [('size', w.DWORD), ('hwnd', w.HWND), ('id', U), ('flags', U),
                        ('callback', U), ('icon', w.HICON), ('tip', w.WCHAR * 128),
                        ('state', w.DWORD), ('state_mask', w.DWORD), ('info', w.WCHAR * 256),
                        ('version', U), ('title', w.WCHAR * 64), ('info_flags', w.DWORD),
                        ('guid', Guid), ('balloon_icon', w.HICON)]

        class StockIcon(ctypes.Structure):
            _fields_ = [('size', w.DWORD), ('icon', w.HICON), ('index', I),
                        ('resource', I), ('path', w.WCHAR * 260)]

        notify = _api(shell, 'Shell_NotifyIconW', w.BOOL, w.DWORD, ctypes.POINTER(NotifyIcon))
        create = _api(user, 'CreateWindowExW', w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR,
                      w.DWORD, I, I, I, I, w.HWND, w.HMENU, w.HINSTANCE, P)
        destroy = _api(user, 'DestroyWindow', w.BOOL, w.HWND)
        default = _api(user, 'DefWindowProcW', ctypes.c_ssize_t, w.HWND, U, w.WPARAM, w.LPARAM)
        self.post = _api(user, 'PostMessageW', w.BOOL, w.HWND, U, w.WPARAM, w.LPARAM)
        taskbar_created = _api(user, 'RegisterWindowMessageW', U, w.LPCWSTR)('TaskbarCreated')
        foreground = _api(user, 'SetForegroundWindow', w.BOOL, w.HWND)
        create_menu = _api(user, 'CreatePopupMenu', w.HMENU)
        append = _api(user, 'AppendMenuW', w.BOOL, w.HMENU, U, ctypes.c_size_t, w.LPCWSTR)
        track = _api(user, 'TrackPopupMenu', w.BOOL, w.HMENU, U, I, I, I, w.HWND, P)
        destroy_menu = _api(user, 'DestroyMenu', w.BOOL, w.HMENU)
        cursor = _api(user, 'GetCursorPos', w.BOOL, ctypes.POINTER(w.POINT))
        quit_loop = _api(user, 'PostQuitMessage', None, I)
        icon = StockIcon(size=ctypes.sizeof(StockIcon))
        get_icon = _api(shell, 'SHGetStockIconInfo', ctypes.c_long, U, U, ctypes.POINTER(StockIcon))
        # SIID_WORLD; caller owns the small HICON returned with SHGSI_ICON.
        if get_icon(13, 0x100 | 1, ctypes.byref(icon)) < 0 or not icon.icon:
            raise OSError('web tray icon unavailable')
        data = NotifyIcon(size=ctypes.sizeof(NotifyIcon), id=1, flags=1 | 2 | 4 | 0x80,
                          callback=CALLBACK, icon=icon.icon)

        def icon_update(*, add=False):
            data.tip = ('보스타이머 웹서버 · 종료 중' if self.stop_requested.is_set()
                        else '보스타이머 웹서버 · 우클릭하여 종료')
            if not notify(0 if add else 1, ctypes.byref(data)):
                raise ctypes.WinError(ctypes.get_last_error())
            if add:
                data.version = 4
                notify(4, ctypes.byref(data))

        def menu(hwnd):
            popup = create_menu()
            if not popup:
                return
            try:
                append(popup, 1, 0, '보스타이머 웹서버')  # Disabled heading.
                append(popup, 0x800, 0, None)
                stopping = self.stop_requested.is_set()
                append(popup, 1 if stopping else 0, STOP_COMMAND,
                       '웹서버 종료 중…' if stopping else '웹서버 종료')
                point = w.POINT()
                cursor(ctypes.byref(point))
                foreground(hwnd)
                command = track(popup, 0x100 | 0x2, point.x, point.y, 0, hwnd, None)
                self.post(hwnd, 0, 0, 0)  # Let Explorer dismiss the menu correctly.
                if command == STOP_COMMAND:
                    self.request_stop()
            finally:
                destroy_menu(popup)

        @callback_type
        def procedure(hwnd, message, wp, lp):
            try:
                if message == taskbar_created:
                    icon_update(add=True)
                    return 0
                if message == CALLBACK:
                    if lp & 0xffff in (0x7b, 0x205, 0x400, 0x401):
                        menu(hwnd)
                    return 0
                if message == REFRESH:
                    icon_update()
                    return 0
                if message == 0x10:
                    destroy(hwnd)
                    return 0
                if message == 2:
                    notify(2, ctypes.byref(data))
                    self.hwnd = None
                    quit_loop(0)
                    return 0
            except Exception as exc:
                diagnostic(self.logger, 'web_tray_callback_failed', error_type=type(exc).__name__)
            return default(hwnd, message, wp, lp)

        instance = _api(kernel, 'GetModuleHandleW', w.HINSTANCE, w.LPCWSTR)(None)
        name = 'BossTimerWebTray.' + str(os.getpid())
        window_class = WindowClass(0, procedure, 0, 0, instance, None, None, None, None, name)
        register = _api(user, 'RegisterClassW', w.ATOM, ctypes.POINTER(WindowClass))
        unregister = _api(user, 'UnregisterClassW', w.BOOL, w.LPCWSTR, w.HINSTANCE)
        registered = False
        try:
            if not register(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            # Hidden top-level window receives Explorer's TaskbarCreated broadcast.
            self.hwnd = create(0, name, '보스타이머 웹서버', 0, 0, 0, 0, 0,
                               None, None, instance, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            data.hwnd = self.hwnd
            if self.closed.is_set():
                return
            icon_update(add=True)
            diagnostic(self.logger, 'web_tray_ready')
            self.ready.set()
            message = w.MSG()
            get_message = _api(user, 'GetMessageW', I, ctypes.POINTER(w.MSG), w.HWND, U, U)
            translate = _api(user, 'TranslateMessage', w.BOOL, ctypes.POINTER(w.MSG))
            dispatch = _api(user, 'DispatchMessageW', ctypes.c_ssize_t, ctypes.POINTER(w.MSG))
            while not self.closed.is_set():
                result = get_message(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    break
                translate(ctypes.byref(message))
                dispatch(ctypes.byref(message))
        finally:
            notify(2, ctypes.byref(data))
            if self.hwnd:
                destroy(self.hwnd)
                self.hwnd = None
            if registered:
                unregister(name, instance)
            _api(user, 'DestroyIcon', w.BOOL, w.HICON)(icon.icon)

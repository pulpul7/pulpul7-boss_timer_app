"""API v1 entry point. Import and health checks must be side-effect free."""
import threading

from .notice_management import NoticeStore, prune_all_servers
from .notice_collector import NoticeCollector

MODULE_API = 1


class UiAdapter:
    """Compatibility inside the module, exposing only the narrow host API."""
    def __init__(self, host, collect_now=None):
        self.host = host
        self.root = host.root
        self.collect_notices_now = collect_now

    @property
    def schedule_server_profile_id(self):
        return self.host.get_server()[0]

    @property
    def schedule_server_profile_name(self):
        return self.host.get_server()[1]

    @property
    def schedule_window(self):
        return self.host.get_parent()

    def _show_centered_messagebox(self, method, title, text, **options):
        return self.host.message_box(method, title, text, **options)


class NoticePlugin:
    def __init__(self, host):
        self.host = host
        self.window = None
        self.after_id = None
        self.worker = None
        self.collection_worker = None
        self.collection_after_id = None
        self.stop_event = threading.Event()
        self.collector = NoticeCollector(stop_event=self.stop_event)
        self.stopped = True

    def health_check(self):
        # Import the management implementation before claiming a healthy start.
        # No window, data migration, network, or audio is allowed in this check.
        from .notice_management_ui import NoticeManagementWindow
        return {"ok": callable(NoticeManagementWindow), "api_version": MODULE_API,
                "features": ["notice_management", "validity", "history", "public_notice_collection"]}

    def start(self):
        self.stopped = False
        self.stop_event.clear()
        self.after_id = self.host.call_later(15_000, self._cleanup)
        self.collection_after_id = self.host.call_later(3000, self._collection_tick)

    def _collection_tick(self):
        if self.stopped:
            return
        server_id = self.host.get_server()[0]  # Capture on the GUI thread.
        if server_id:
            self._launch_collection(server_id)
        self.collection_after_id = self.host.call_later(10_000, self._collection_tick)

    def _launch_collection(self, server_id, *, manual=False):
        if self.stopped or not server_id or (self.collection_worker and self.collection_worker.is_alive()):
            return False
        # The store stays bound to this ID even when the user switches servers.
        def work():
            try:
                self.collector.collect_once(NoticeStore(self.host.data_root, server_id), manual=manual)
            except Exception as exc:
                self.host.log(f"notice_collection_failed {exc}")
        self.collection_worker = threading.Thread(target=work, name="notice-module-collector", daemon=True)
        self.collection_worker.start()
        return True

    def request_collection(self, server_id):
        if server_id != self.host.get_server()[0]:
            return False
        return self._launch_collection(server_id, manual=True)

    def _cleanup(self):
        if self.stopped:
            return
        def work():
            for error in prune_all_servers(self.host.data_root):
                self.host.log(f"notice_history_cleanup_failed {error}")
        if self.worker is None or not self.worker.is_alive():
            self.worker = threading.Thread(target=work, name="notice-module-history", daemon=True)
            self.worker.start()
        self.after_id = self.host.call_later(3_600_000, self._cleanup)

    def open_management(self):
        from .notice_management_ui import NoticeManagementWindow
        if self.stopped:
            raise RuntimeError("알림 모듈이 중지되어 있습니다.")
        if self.window is not None and self.window.window.winfo_exists():
            if self.window.server_id == self.host.get_server()[0]:
                self.window.window.lift()
                return
            self.window.window.destroy()
        self.window = NoticeManagementWindow(UiAdapter(self.host, self.request_collection), self.host.data_root)

    def stop(self):
        self.stopped = True
        self.stop_event.set()
        if self.collection_after_id is not None:
            self.host.cancel_later(self.collection_after_id)
            self.collection_after_id = None
        if self.after_id is not None:
            self.host.cancel_later(self.after_id)
            self.after_id = None
        if self.window is not None and self.window.window.winfo_exists():
            self.window.window.destroy()
        self.window = None


def create_plugin(host):
    return NoticePlugin(host)

"""API v1 entry point. Import and health checks must be side-effect free."""
import json
import threading

from .notice_management import NoticeStore, prune_all_servers
from .notice_collector import NoticeCollector
from .notice_changes import subscribe
from .notice_preparation import ChangeDebounce, PreparedSpeechPool
from .notice_management import local_now

MODULE_API = 1


class UiAdapter:
    """Compatibility inside the module, exposing only the narrow host API."""
    def __init__(self, host, collect_now=None):
        self.host = host
        self.root = host.root
        self.collect_notices_now = collect_now
        self.preview_synthesizer = getattr(host, "preview_synthesizer", None)

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
        self.schedule_after_id = None
        self.schedule_worker = None
        self.stop_event = threading.Event()
        self.collector = NoticeCollector(stop_event=self.stop_event)
        self.stopped = True
        self.change_gate = ChangeDebounce()
        self.prepared_audio = PreparedSpeechPool()
        self.observation_lock = threading.RLock()
        self.observed = None
        self.observed_store = None
        self.store_stamp = None
        self.unsubscribe = None
        self.retry = None
        self.retry_count = 0

    def health_check(self):
        # Import the management implementation before claiming a healthy start.
        # No window, data migration, network, or audio is allowed in this check.
        from .notice_management_ui import NoticeManagementWindow
        from .notice_analysis import analyze_notice, notice_events
        from .notice_polling import polling_plan
        from .notice_opportunities import opportunity_allowed
        from .notice_audio import NoticeAudioController
        from .notice_local_transport import LocalNoticeTransport
        from .notice_template_ui import NoticeTemplateWindow
        from .notice_schedule import synchronize_schedule
        from .notice_server_open import server_open_override
        return {"ok": callable(NoticeManagementWindow), "api_version": MODULE_API,
                "features": ["notice_management", "validity", "history", "public_notice_collection", "notice_period_analysis", "extension_polling", "boss_opportunity_policy", "schedule_participation", "relative_date_templates", "speech_preparation"]}

    def start(self):
        self.stopped = False
        self.stop_event.clear()
        self.unsubscribe = subscribe(self._notice_changed)
        self.after_id = self.host.call_later(15_000, self._cleanup)
        self.collection_after_id = self.host.call_later(3000, self._collection_tick)
        if callable(getattr(self.host, 'get_schedule_snapshot', None)):
            self.schedule_after_id = self.host.call_later(0, self._schedule_tick)

    def notify_schedule_changed(self):
        """Thread-safe notification only: no Tk, disk, network or playback."""
        if not self.stopped:
            with self.observation_lock:
                self.change_gate.changed()
                self.prepared_audio.invalidate()
                self.retry = None
                self.retry_count = 0

    @staticmethod
    def _file_stamp(path):
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            return None

    def _notice_changed(self, root, server_id):
        # Called under the store's transaction lock; never reenter the store.
        with self.observation_lock:
            store = self.observed_store
            if not store or store.root != root or store.server_id != server_id:
                return
            self.store_stamp = self._file_stamp(store.path)
            if threading.current_thread() is not self.schedule_worker:
                self.notify_schedule_changed()

    def get_server_open_override(self, server_id, now, reference=None):
        if self.stopped or not server_id or server_id != self.host.get_server()[0]:
            return None
        from .notice_server_open import server_open_override
        # Atomic JSON replacement by collectors makes this a bounded read.
        # Do not migrate/save data or perform a network request while opening UI.
        state = NoticeStore(self.host.data_root, server_id)._load()
        return server_open_override(state, now, reference)

    def _schedule_tick(self):
        if self.stopped:
            return
        try:
            snapshot = self.host.get_schedule_snapshot()  # Tk access only here.
            server_id = self.host.get_server()[0]
            get_profile = getattr(self.host, 'get_preparation_profile', None)
            profile = get_profile() if callable(get_profile) else {}
            usable = bool(snapshot and server_id and snapshot.get('server_id') == server_id)
            observation = (server_id, json.dumps(snapshot, sort_keys=True, ensure_ascii=False),
                           profile.get('signature'), bool(profile.get('enabled')), local_now().date())
            with self.observation_lock:
                if observation != self.observed:
                    self.observed = observation
                    self.observed_store = NoticeStore(self.host.data_root, server_id) if usable else None
                    self.store_stamp = self._file_stamp(self.observed_store.path) if usable else None
                    self.notify_schedule_changed()
                elif usable:
                    stamp = self._file_stamp(self.observed_store.path)
                    if stamp != self.store_stamp:  # External edits/another process.
                        self.store_stamp = stamp
                        self.notify_schedule_changed()
            if usable and (not self.schedule_worker or not self.schedule_worker.is_alive()):
                with self.observation_lock:
                    revision = self.change_gate.take()
                    if revision is None and self.retry and self.change_gate.clock() >= self.retry[1]:
                        revision = self.retry[0] if self.change_gate.current(self.retry[0]) else None
                        self.retry = None
                    epoch = self.prepared_audio.epoch
                if revision is not None:
                    apply = getattr(self.host, 'apply_temporary_maintenance', None)
                    if callable(apply):
                        from .notice_maintenance_import import import_temporary_maintenance
                        import_temporary_maintenance(NoticeStore(self.host.data_root, server_id), snapshot, apply)
                    if not self.change_gate.current(revision):
                        return  # Imported maintenance changed the schedule; settle again.
                    def work():
                        failed = False
                        try:
                            valid = lambda: not self.stop_event.is_set() and self.change_gate.current(revision)
                            if valid():
                                from .notice_schedule import synchronize_schedule
                                store = NoticeStore(self.host.data_root, server_id)
                                synchronize_schedule(store, snapshot)
                                factory = profile.get('factory')
                                requests = store.preparation_candidates() if profile.get('enabled') and callable(factory) else []
                                failed = self.prepared_audio.prepare((server_id, str(snapshot.get('season', ''))),
                                    profile.get('signature', ''), requests, factory, epoch, valid)
                                if valid():
                                    self.host.log(f'notice_preparation server={server_id} {self.prepared_audio.status}')
                        except Exception as exc:
                            failed = True
                            self.host.log(f'notice_schedule_failed {exc}')
                        finally:
                            with self.observation_lock:
                                if failed and not self.stopped and self.change_gate.current(revision) and self.retry_count < 2:
                                    self.retry_count += 1
                                    self.retry = (revision, self.change_gate.clock() + 30.0)
                    self.schedule_worker = threading.Thread(target=work, name='notice-schedule', daemon=True)
                    self.schedule_worker.start()
        except Exception as exc:
            self.notify_schedule_changed()  # Unknown current state must not retain playable bindings.
            self.host.log(f'notice_schedule_snapshot_failed {exc}')
        finally:
            if not self.stopped:
                self.schedule_after_id = self.host.call_later(1000, self._schedule_tick)

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

    def restore_settings(self, payload):
        server_id = self.host.get_server()[0]
        if self.stopped or not server_id:
            raise RuntimeError("알리미를 실행하고 서버를 선택한 뒤 롤백하세요.")
        backup = NoticeStore(self.host.data_root, server_id).restore_preferences(payload)
        if self.window is not None and self.window.window.winfo_exists():
            self.window.window.destroy()  # Do not let stale editor fields overwrite the restore.
        self.window = None
        return backup

    def stop(self):
        self.stopped = True
        self.stop_event.set()
        if self.unsubscribe is not None:
            self.unsubscribe()
            self.unsubscribe = None
        self.prepared_audio.close()
        if self.schedule_after_id is not None:
            self.host.cancel_later(self.schedule_after_id)
            self.schedule_after_id = None
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

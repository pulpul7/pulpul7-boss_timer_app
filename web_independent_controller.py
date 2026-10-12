"""Web publication lifecycle, independent of Discord voice ownership."""
import hashlib
import threading
from web_delegated_hosting import DelegatedHosting
from web_standalone_hosting import StandaloneHosting


class IndependentWebController:
    independent = True

    def __init__(self, app, manager):
        self.app, self.manager = app, manager
        self.lock = threading.RLock()
        self.wake, self.closed = threading.Event(), threading.Event()
        self.context = self.retained_host = None
        self.delegation = manager.delegation = DelegatedHosting(self)
        self.standalone = manager.standalone = StandaloneHosting(self)
        manager.authority_retry = self.retry
        self.after_id = app.root.after(500, self._tick)
        self.thread = threading.Thread(target=self._loop, name='web-independent-controller', daemon=True)
        self.thread.start()

    def _shared_path(self, stamp):
        return 'data/web/' + hashlib.sha256(stamp[0].encode()).hexdigest()[:24] + '.json'

    def retain_web_host(self, *_args):
        return False

    def refresh_context(self):
        if self.closed.is_set() or self.manager.closed.is_set():
            return
        self.standalone.tick()
        if not self.standalone.suppresses_normal():
            self.delegation.tick()  # Preserve an already running legacy host until explicit web handover.

    def _tick(self):
        try:
            self.refresh_context()
        finally:
            if not self.closed.is_set() and not self.manager.closed.is_set():
                self.after_id = self.app.root.after(1000, self._tick)

    def retry(self, *, once=False):
        self.manager.start_web()

    def _loop(self):
        while not self.closed.is_set() and not self.manager.closed.is_set():
            try:
                self.refresh_context()
                self.standalone.publish_shared()
            except Exception as exc:
                self.manager._log('web_independent_controller_failed', error_type=type(exc).__name__)
            self.wake.wait(5)
            self.wake.clear()

    def close(self):
        self.closed.set()
        self.standalone.cancelled.set()
        self.delegation.close()
        self.wake.set()
        try:
            self.manager.set_authority((), message='웹서비스 종료 중')
        except OSError:
            pass
        if self.after_id is not None:
            try:
                self.app.root.after_cancel(self.after_id)
            except Exception:
                pass

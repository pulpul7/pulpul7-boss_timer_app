"""Bounded download/UI handoff for one schedule sync; no network or Tk imports."""
import queue
import time


class ScheduleSyncTask:
    def __init__(self, root, *, progress, failure, close, release, timeout=120,
                 clock=time.monotonic):
        self.root = root
        self.progress = progress
        self.failure = failure
        self.close = close
        self.release = release
        self.clock = clock
        self.deadline = clock() + timeout
        self.events = queue.SimpleQueue()
        self.done = False
        self.phase = 'download'
        self.after_id = root.after(50, self._poll)

    def post_progress(self, message):
        if not self.done:
            self.events.put(('progress', message))

    def post_result(self, callback):
        if not self.done:
            self.events.put(('result', callback))

    def run(self, worker):
        try:
            worker()
        except Exception as exc:
            if not self.done:
                self.events.put(('error', exc))

    def complete(self):
        if self.done:
            return
        self.done = True
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass  # A destroyed root must not skip dialog/control cleanup.
            self.after_id = None
        try:
            self.close()
        finally:
            self.release()

    def _fail(self, kind, exc=None):
        if self.done:
            return
        try:
            self.failure(kind, self.phase, exc)
        finally:
            self.complete()

    def cancel(self):
        # Do not interrupt writes or a user's replace-confirmation dialog.
        if not self.done and self.phase != 'apply':
            self._fail('cancelled')
            return True
        return False

    def apply(self, callback):
        if self.done:
            return
        if self.clock() >= self.deadline:
            self._fail('timeout')
            return
        self.phase = 'apply'
        try:
            callback()
        except Exception as exc:
            self._fail('exception', exc)
        finally:
            self.complete()

    def _poll(self):
        self.after_id = None
        if self.done:
            return
        if self.phase != 'apply' and self.clock() >= self.deadline:
            self._fail('timeout')
            return
        while not self.done:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == 'progress':
                    self.progress(value)
                elif kind == 'result':
                    self.phase = 'prepare'
                    value()
                else:
                    self._fail('exception', value)
            except Exception as exc:
                self._fail('exception', exc)
        if not self.done:
            self.after_id = self.root.after(50, self._poll)

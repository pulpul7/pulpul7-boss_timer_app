"""UI heartbeat diagnostics. The watchdog never calls Tk or application locks."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import sys
import threading
import time
import traceback


class UIResponsivenessMonitor:
    def __init__(self, root, directory, *, threshold=15):
        self.root = root
        self.path = Path(directory) / 'ui_responsiveness.log'
        self.threshold = threshold
        self.ui_thread = threading.get_ident()
        self.closed = threading.Event()
        self.notes = queue.SimpleQueue()
        self.last_pulse = None  # Arm only once the first real Tk callback runs.
        self.reported = False
        self.logger = None
        self.after_id = root.after(1000, self._pulse)
        self.thread = threading.Thread(target=self._watch, name='ui-responsiveness-watch', daemon=True)
        self.thread.start()

    def note(self, event, **fields):
        self.notes.put(dict(event=event, **fields))

    def _pulse(self):
        self.after_id = None
        if self.closed.is_set():
            return
        self.last_pulse = time.monotonic()
        self.after_id = self.root.after(1000, self._pulse)

    def _record(self, data):
        try:
            if self.logger is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.logger = logging.Logger('boss_timer.ui_responsiveness')
                self.logger.setLevel(logging.INFO)
                handler = RotatingFileHandler(self.path, maxBytes=262144, backupCount=1, encoding='utf-8')
                handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
                self.logger.addHandler(handler)
            self.logger.info(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass  # Diagnostics cannot block or terminate the application.

    def _check(self, now):
        if self.last_pulse is None:
            return
        delay = max(0, now - self.last_pulse)
        if delay < self.threshold:
            if self.reported:
                self._record(dict(event='ui_responsive_again'))
            self.reported = False
        elif not self.reported:
            self.reported = True
            frames = sys._current_frames()
            threads = [thread for thread in threading.enumerate()
                       if thread.ident == self.ui_thread or thread.name.startswith(('web-', 'boss-web-', 'discord-'))]
            threads.sort(key=lambda thread: thread.ident != self.ui_thread)
            stacks = {thread.name: ''.join(traceback.format_stack(frames[thread.ident], limit=18))
                      for thread in threads[:10] if thread.ident in frames}
            # Stack locations only: never capture locals, settings or credentials.
            self._record(dict(event='ui_unresponsive', delay_seconds=round(delay, 1), stacks=stacks))

    def _watch(self):
        try:
            while not self.closed.wait(2):
                try:
                    for _ in range(16):
                        try:
                            self._record(self.notes.get_nowait())
                        except queue.Empty:
                            break
                    self._check(time.monotonic())
                except Exception:
                    pass
        finally:
            if self.logger is not None:
                for handler in self.logger.handlers:
                    handler.close()

    def close(self):
        self.closed.set()
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

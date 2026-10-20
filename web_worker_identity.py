"""Validate the actual worker behind a Windows one-file bootloader.

The retained Popen handle still owns the launcher and its stop/wait lifecycle.
Only a verified same-executable direct child may report a different PID.
"""
import os

from discord_bot_process_recovery import _inspect, _kernel, _path, _snapshot


class SpawnedWorkerIdentity:
    def __init__(self, process, executable, *, frozen=False):
        self.process = process
        self.executable = _path(executable)
        self.frozen = frozen
        self.launcher = self.worker = None
        self.reported = None

    def matches(self, pid):
        if type(pid) is not int or pid <= 0:
            return False
        if pid == self.process.pid:
            return True  # Source/onedir runs and reattached workers.
        if not self.frozen or os.name != 'nt':
            return False
        try:
            if self.process.poll() is not None:
                return False
            kernel = _kernel()
            launcher = _inspect(kernel, self.process.pid)
            if launcher is None or _path(launcher.path) != self.executable:
                return False
            if self.launcher and launcher.created != self.launcher.created:
                return False
            if self.worker:
                if pid != self.worker.pid:
                    return False
                worker = _inspect(kernel, pid, launcher.pid)
                if worker is None or worker.created != self.worker.created:
                    return False
            else:
                # Do not trust a parent PID supplied in the status document.
                candidate = _inspect(kernel, pid)
                if candidate is None:
                    return False
                parent = _snapshot(kernel).get(pid, (None, ''))[0]
                if parent != launcher.pid:
                    return False
                worker = _inspect(kernel, pid, parent)
                if (worker is None or worker.created != candidate.created
                        or _path(worker.path) != _path(candidate.path)):
                    return False  # PID reuse during the ancestry lookup.
            if (worker is None or _path(worker.path) != self.executable
                    or worker.created < launcher.created or self.process.poll() is not None):
                return False
            self.launcher, self.worker = launcher, worker
            return True
        except OSError:
            return False  # Missing process/inspection rights cannot prove ownership.

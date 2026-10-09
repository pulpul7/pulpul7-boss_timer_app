"""Keep a guild's leave acknowledgement ahead of its next voice join."""
import asyncio


class VoiceLeaveBarrier:
    def __init__(self):
        self.ready = asyncio.Event()
        self.ready.set()
        self.pending = False
        self.sending = False
        self.acknowledged = False

    def begin(self):
        if self.pending:
            return False
        self.pending = self.sending = True
        self.acknowledged = False
        self.ready.clear()
        return True

    def finish_send(self):
        self.sending = False
        self._finish_if_acknowledged()

    def confirm(self):
        if not self.pending:
            return False
        self.acknowledged = True
        self._finish_if_acknowledged()
        return True

    def _finish_if_acknowledged(self):
        if self.acknowledged and not self.sending:
            self.pending = False
            self.ready.set()

    async def wait(self, timeout=3.0):
        if not self.pending:
            return True
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=timeout)
        except TimeoutError:
            # A timeout does not prove that the delayed leave event is gone.
            return False
        return True

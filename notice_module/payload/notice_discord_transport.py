"""Prepared-only Discord transport. No synthesis, boss playback or reconnect."""
import uuid


class DiscordNoticeTransport:
    notice_audio_protocol = 1

    def __init__(self, request):
        self.request = request

    def prepare_prepared(self, lease):
        handle = dict(id=uuid.uuid4().hex, lease=lease, state='preparing')
        try:
            result = self.request(dict(id=handle['id'], action='prepare', path=str(lease.path)))
            handle['state'] = result['state'] if result.get('accepted') else 'failed'
        except Exception:
            # Keep ownership even after an uncertain timeout, so stop must be
            # confirmed before the prepared file can be released.
            handle['state'] = 'failed'
        return handle

    def status(self, handle):
        if handle['state'] == 'failed':
            return 'failed'
        result = self.request(dict(id=handle['id'], action='status'))
        handle['state'] = result['state']
        return handle['state']

    def _action(self, handle, action):
        result = self.request(dict(id=handle['id'], action=action))
        handle['state'] = result['state']
        return result.get('accepted') is True

    def play(self, handle):
        return self._action(handle, 'play')

    def resume(self, handle):
        return self._action(handle, 'resume')

    def pause(self, handle):
        return self._action(handle, 'pause')

    def stop(self, handle):
        if self._action(handle, 'stop'):
            handle['lease'].stop()
            return True
        return False

"""Silent, bounded speech pre-generation; no player, Discord or delivery ledger."""
import threading
import time
from pathlib import Path

SETTLE_SECONDS = 5.0
MAX_PREPARED_TEXTS = 64


class ChangeDebounce:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.revision = 0
        self.due = None

    def changed(self):
        with self.lock:
            self.revision += 1
            self.due = self.clock() + SETTLE_SECONDS
            return self.revision

    def take(self):
        with self.lock:
            if self.due is None or self.clock() < self.due:
                return None
            self.due = None
            return self.revision

    def current(self, revision):
        with self.lock:
            return self.revision == revision


class PreparedLease:
    def __init__(self, pool, entry):
        self.pool, self.entry = pool, entry
        self.path = entry['path']
        self.released = False

    def stop(self):
        with self.pool.lock:
            if self.released:
                return
            self.released = True
            self.entry['leases'] -= 1
        self.pool.prune()


class PreparedSpeechPool:
    def __init__(self):
        self.lock = threading.RLock()
        self.cache = {}
        self.bindings = {}
        self.wanted = set()
        self.epoch = 0
        self.closed = False
        self.status = '사전 음성 준비 대기'

    @staticmethod
    def _cleanup(synth):
        try:
            synth.stop()  # Only the owned temporary synth; never the boss cache.
        except Exception:
            pass

    def invalidate(self):
        with self.lock:
            self.epoch += 1
            self.bindings.clear()
            self.status = '변경 감지 · 5초 안정화 대기'
            return self.epoch

    def prune(self):
        with self.lock:
            stale = [key for key, entry in self.cache.items() if key not in self.wanted and not entry['leases']]
            entries = [self.cache.pop(key) for key in stale]
        for entry in entries:
            self._cleanup(entry['synth'])

    def prepare(self, scope, profile, requests, factory, epoch, valid):
        """Run on the single preparation worker, retaining only current results."""
        desired = {}
        for request in requests:
            key = (scope, profile, request['tts_text'])
            if key not in desired and len(desired) >= MAX_PREPARED_TEXTS:
                continue
            desired.setdefault(key, []).append(request)
        with self.lock:
            if self.closed or epoch != self.epoch or not valid():
                return
            self.wanted = set(desired)
            self.status = f'사전 음성 준비 중 · {len(desired)}문장'
        self.prune()
        failed = 0
        for key, items in desired.items():
            if not valid():
                return
            with self.lock:
                if self.closed or epoch != self.epoch:
                    return
                entry = self.cache.get(key)
                missing = entry if entry and not Path(entry['path']).is_file() and not entry['leases'] else None
                if missing:
                    self.cache.pop(key)
                    entry = None
            if missing:
                self._cleanup(missing['synth'])
            if entry is None:
                synth = None
                try:
                    synth = factory()
                    path = synth.synthesize(key[2])
                    if not path or not Path(path).is_file():
                        raise RuntimeError('TTS 파일 생성 실패')
                    with self.lock:
                        if self.closed or epoch != self.epoch or not valid():
                            return
                        entry = dict(path=path, synth=synth, leases=0)
                        self.cache[key] = entry
                        synth = None
                except Exception:
                    failed += 1
                    continue
                finally:
                    if synth is not None:
                        self._cleanup(synth)
            with self.lock:
                if self.closed or epoch != self.epoch or not valid():
                    return
                for request in items:
                    self.bindings[(scope[0], request['id'])] = (tuple(request['stamp']), key)
        with self.lock:
            if epoch == self.epoch and not self.closed:
                self.status = f'사전 음성 준비 완료 · {len(desired) - failed}문장 / 실패 {failed}'
        return failed

    def current(self, server_id, request):
        with self.lock:
            binding = self.bindings.get((server_id, request['id']))
            return bool(not self.closed and binding and binding[0] == tuple(request['token'][2:4])
                        and binding[1][2] == request['tts_text'])

    def acquire(self, server_id, request):
        """Playback still needs a current delivery token; this grants no speech."""
        with self.lock:
            binding = self.bindings.get((server_id, request['id']))
            if self.closed or not binding or binding[0] != tuple(request['token'][2:4]):
                return None
            entry = self.cache.get(binding[1])
            if not entry or not Path(entry['path']).is_file() or binding[1][2] != request['tts_text']:
                return None
            entry['leases'] += 1
            return PreparedLease(self, entry)

    def close(self):
        with self.lock:
            self.closed = True
            self.epoch += 1
            self.bindings.clear()
            self.wanted.clear()
        # Never block the GUI on file cleanup or on an in-flight synthesis.
        threading.Thread(target=self.prune, name='notice-preparation-cleanup', daemon=True).start()

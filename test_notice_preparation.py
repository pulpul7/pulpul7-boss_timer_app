"""Offline fixtures only: no TTS service, player, Discord or user data."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from notice_module.payload.main import NoticePlugin
from notice_module.payload.notice_management import NoticeStore, KST
from notice_module.payload.notice_preparation import ChangeDebounce, PreparedSpeechPool


class PreparationTests(unittest.TestCase):
    def setUp(self):
        # Neutral baseline for algorithm tests; packaged defaults are tested separately.
        defaults = patch('notice_module.payload.notice_defaults.bundled_preferences', return_value=None)
        defaults.start()
        self.addCleanup(defaults.stop)
        temp = tempfile.TemporaryDirectory(prefix='notice-preparation-unit-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.pool = PreparedSpeechPool()
        self.synths = []
        self.factory = Mock(side_effect=self.make_synth)
        self.addCleanup(self.cleanup_pool)

    def cleanup_pool(self):
        self.pool.wanted.clear()
        self.pool.prune()

    def make_synth(self):
        path = self.root / f'fake-{len(self.synths)}.mp3'
        def synthesize(text):
            path.write_bytes(b'not audio')
            return str(path)
        synth = Mock(synthesize=Mock(side_effect=synthesize), stop=Mock(side_effect=lambda: path.unlink(missing_ok=True)))
        self.synths.append(synth)
        return synth

    def request(self, key='a', text='안내', revision=1):
        return dict(id=key, tts_text=text, stamp=(revision, 0), token=('odin9', key, revision, 0))

    def prepare(self, requests, scope=('odin9', '18'), profile='voice'):
        self.pool.prepare(scope, profile, requests, self.factory, self.pool.epoch, lambda: True)

    def test_debounce_five_seconds_after_last_change(self):
        now = [0.0]
        gate = ChangeDebounce(lambda: now[0])
        old = gate.changed()
        now[0] = 4.0
        latest = gate.changed()
        now[0] = 8.9
        self.assertIsNone(gate.take())
        now[0] = 9.0
        self.assertEqual(gate.take(), latest)
        self.assertIsNone(gate.take())
        self.assertFalse(gate.current(old))

    def test_same_text_shared_and_revision_rebound_without_synthesis(self):
        a, b = self.request(), self.request('b')
        self.prepare([a, b])
        self.assertEqual(self.factory.call_count, 1)
        lease = self.pool.acquire('odin9', b)
        self.assertTrue(Path(lease.path).is_file())
        lease.stop()
        self.pool.invalidate()
        self.assertIsNone(self.pool.acquire('odin9', a))
        new = self.request(revision=2)
        self.prepare([new])
        self.assertEqual(self.factory.call_count, 1)
        self.assertIsNone(self.pool.acquire('odin9', a))
        self.pool.acquire('odin9', new).stop()

    def test_late_synthesis_is_discarded_after_change(self):
        synth = self.make_synth()
        original = synth.synthesize.side_effect
        def synthesize(text):
            path = original(text)
            self.pool.invalidate()
            return path
        synth.synthesize.side_effect = synthesize
        self.factory.side_effect = None
        self.factory.return_value = synth
        self.prepare([self.request()])
        self.assertFalse(self.pool.bindings)
        self.assertFalse(self.pool.cache)
        synth.stop.assert_called_once()

    def test_active_lease_survives_pruning_until_released(self):
        self.prepare([self.request()])
        lease = self.pool.acquire('odin9', self.request())
        self.pool.invalidate()
        self.prepare([], scope=('odin8', '18'))
        self.assertTrue(Path(lease.path).is_file())
        lease.stop()
        lease.stop()  # Idempotent cleanup.
        self.assertFalse(Path(lease.path).exists())
        self.synths[0].stop.assert_called_once()

    def test_voice_and_season_changes_never_reuse_other_profile(self):
        for scope, voice in [(('odin9', '18'), 'a'), (('odin9', '18'), 'b'), (('odin9', '19'), 'b')]:
            self.pool.invalidate()
            self.prepare([self.request()], scope, voice)
        self.assertEqual(self.factory.call_count, 3)
        self.assertEqual(len(self.pool.cache), 1)
        self.assertIsNone(self.pool.acquire('odin8', self.request()))

    def test_synthesis_failure_is_silent_and_does_not_bind(self):
        self.factory.side_effect = RuntimeError('offline')
        self.prepare([self.request()])
        self.assertFalse(self.pool.bindings)
        self.assertIn('실패 1', self.pool.status)

    def test_missing_cache_file_is_recreated_on_next_preparation(self):
        request = self.request()
        self.prepare([request])
        lease = self.pool.acquire('odin9', request)
        path = Path(lease.path)
        lease.stop()
        path.unlink()
        self.pool.invalidate()
        self.prepare([request])
        self.assertEqual(self.factory.call_count, 2)
        self.pool.acquire('odin9', request).stop()

    def test_store_future_candidates_do_not_mark_delivery_and_obey_switches(self):
        now = datetime.now(KST)
        store = NoticeStore(self.root / 'store', 'odin9', clock=lambda: now)
        store.register(dict(id='future', title='미래 안내', tts_text='미리 준비',
            valid_from=now + timedelta(hours=2), valid_until=now + timedelta(hours=3)))
        self.assertEqual(store.preparation_candidates(), [])
        settings = store.snapshot()['settings']
        settings['output_enabled'] = True
        store.configure(settings)
        self.assertEqual([r['id'] for r in store.preparation_candidates()], ['future'])
        self.assertIsNone(store.delivery_token('future'))
        self.assertIsNone(store.snapshot()['events']['future']['last_delivery'])
        settings['categories']['general'] = False
        store.configure(settings)
        self.assertEqual(store.preparation_candidates(), [])

    def plugin(self):
        snapshot = dict(server_id='odin9', season='18', events=[])
        host = SimpleNamespace(data_root=self.root / 'store',
            get_schedule_snapshot=Mock(return_value=snapshot), get_server=lambda: ('odin9', '오9'),
            get_preparation_profile=lambda: dict(enabled=True, signature='voice', factory=self.factory),
            call_later=Mock(return_value='timer'), cancel_later=Mock(), log=Mock())
        plugin = NoticePlugin(host)
        self.addCleanup(plugin.stop)
        plugin.prepared_audio = self.pool
        now = [0.0]
        plugin.change_gate.clock = lambda: now[0]
        plugin.start()
        store = NoticeStore(host.data_root, 'odin9')
        wall = datetime.now(KST)
        store.register(dict(id='notice', title='안내', tts_text='준비',
            valid_from=wall, valid_until=wall + timedelta(hours=1)))
        settings = store.snapshot()['settings']
        settings['output_enabled'] = True
        store.configure(settings)
        return plugin, host, store, now

    def test_plugin_debounces_edits_and_prepares_on_worker_only(self):
        plugin, host, store, now = self.plugin()
        gui_thread = threading.get_ident()
        original = self.factory.side_effect
        def factory():
            self.assertNotEqual(threading.get_ident(), gui_thread)
            return original()
        self.factory.side_effect = factory
        plugin._schedule_tick()
        now[0] = 4
        plugin.notify_schedule_changed()
        now[0] = 5
        plugin._schedule_tick()
        self.assertIsNone(plugin.schedule_worker)
        now[0] = 9
        plugin._schedule_tick()
        plugin.schedule_worker.join(timeout=3)
        self.assertFalse(plugin.schedule_worker.is_alive())
        self.assertEqual(self.factory.call_count, 1)
        self.assertTrue(self.pool.bindings)
        now[0] = 30
        plugin._schedule_tick()  # Unchanged polling/self writes must not retrigger.
        self.assertIsNone(plugin.change_gate.due)
        settings = store.snapshot()['settings']
        settings['output_enabled'] = False
        store.configure(settings)
        self.assertFalse(self.pool.bindings)  # Invalidate immediately, not 5s later.
        now[0] = 35
        plugin._schedule_tick()
        plugin.schedule_worker.join(timeout=3)
        self.assertFalse(self.pool.cache)
        self.assertEqual(self.factory.call_count, 1)

    def test_handover_suppresses_generation_until_snapshot_available(self):
        plugin, host, _, now = self.plugin()
        host.get_schedule_snapshot.return_value = None
        plugin._schedule_tick()
        now[0] = 10
        plugin._schedule_tick()
        self.assertIsNone(plugin.schedule_worker)
        host.get_schedule_snapshot.return_value = dict(server_id='odin9', season='18', events=[])
        plugin._schedule_tick()
        self.assertIsNone(plugin.schedule_worker)
        now[0] = 15
        plugin._schedule_tick()
        plugin.schedule_worker.join(timeout=3)
        self.assertEqual(self.factory.call_count, 1)

    def test_failed_generation_retries_only_twice_offline(self):
        plugin, _, _, now = self.plugin()
        self.factory.side_effect = RuntimeError('offline')
        plugin._schedule_tick()
        for at in (5, 35, 65):
            now[0] = at
            plugin._schedule_tick()
            plugin.schedule_worker.join(timeout=3)
        self.assertEqual(self.factory.call_count, 3)
        self.assertIsNone(plugin.retry)
        now[0] = 100
        plugin._schedule_tick()
        self.assertEqual(self.factory.call_count, 3)

    def test_snapshot_error_revokes_old_prepared_bindings(self):
        plugin, host, _, now = self.plugin()
        plugin._schedule_tick()
        now[0] = 5
        plugin._schedule_tick()
        plugin.schedule_worker.join(timeout=3)
        self.assertTrue(self.pool.bindings)
        host.get_schedule_snapshot.side_effect = RuntimeError('snapshot unavailable')
        plugin._schedule_tick()
        self.assertFalse(self.pool.bindings)
        host.get_schedule_snapshot.side_effect = None
        now[0] = 9
        plugin._schedule_tick()
        self.assertFalse(self.pool.bindings)
        now[0] = 10
        plugin._schedule_tick()
        plugin.schedule_worker.join(timeout=3)
        self.assertTrue(self.pool.bindings)
        self.assertEqual(self.factory.call_count, 1)

    def test_change_while_worker_running_rejects_old_text(self):
        plugin, host, _, now = self.plugin()
        entered, release = threading.Event(), threading.Event()
        original = self.factory.side_effect
        def factory():
            synth = original()
            generate = synth.synthesize.side_effect
            def blocked(text):
                entered.set()
                release.wait(timeout=3)
                return generate(text)
            synth.synthesize.side_effect = blocked
            return synth
        self.factory.side_effect = factory
        plugin._schedule_tick()
        now[0] = 5
        plugin._schedule_tick()
        try:
            self.assertTrue(entered.wait(timeout=2))
            host.get_schedule_snapshot.return_value = None  # Handover began.
            plugin._schedule_tick()
        finally:
            release.set()
            plugin.schedule_worker.join(timeout=3)
        self.assertFalse(self.pool.bindings)
        self.assertFalse(self.pool.cache)


if __name__ == '__main__':
    unittest.main()

"""Automatic collector scheduling: fake callbacks, no Tk/network/files/audio."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from notice_module.payload.main import NoticePlugin


class CollectionTickTests(unittest.TestCase):
    def plugin(self):
        plugin = object.__new__(NoticePlugin)
        plugin.stopped = False
        plugin.collection_after_id = 'consumed'
        plugin.host = NS(get_server=Mock(return_value=('8', 'server')),
                         call_later=Mock(return_value='next'), log=Mock())
        plugin._launch_collection = Mock(return_value=True)
        return plugin

    def test_launch_error_does_not_kill_future_automatic_collection(self):
        plugin = self.plugin()
        plugin._launch_collection.side_effect = [RuntimeError('thread start failure'), True]
        plugin._collection_tick()
        plugin.host.log.assert_called_once_with('notice_collection_tick_failed RuntimeError')
        delay, callback = plugin.host.call_later.call_args.args
        self.assertEqual(delay, 10_000)
        callback()
        self.assertEqual(plugin._launch_collection.call_count, 2)
        self.assertEqual(plugin.collection_after_id, 'next')

    def test_server_capture_error_still_rearms(self):
        plugin = self.plugin();plugin.host.get_server.side_effect = RuntimeError('temporary unavailable')
        plugin._collection_tick()
        plugin._launch_collection.assert_not_called()
        plugin.host.call_later.assert_called_once()

    def test_missing_server_keeps_polling_without_launching(self):
        plugin = self.plugin();plugin.host.get_server.return_value = ('', '')
        plugin._collection_tick()
        plugin._launch_collection.assert_not_called()
        plugin.host.call_later.assert_called_once()

    def test_busy_worker_still_rearms(self):
        plugin = self.plugin();plugin._launch_collection.return_value = False
        plugin._collection_tick()
        plugin.host.call_later.assert_called_once()
        plugin.host.log.assert_not_called()

    def test_stopped_plugin_cannot_restart_collection(self):
        plugin = self.plugin();plugin.stopped = True
        plugin._collection_tick()
        plugin.host.get_server.assert_not_called()
        plugin.host.call_later.assert_not_called()

    def test_stop_during_launch_does_not_schedule_late_callback(self):
        plugin = self.plugin()
        def stop(_):plugin.stopped = True
        plugin._launch_collection.side_effect = stop
        plugin._collection_tick()
        plugin.host.call_later.assert_not_called()


if __name__ == '__main__':
    unittest.main()

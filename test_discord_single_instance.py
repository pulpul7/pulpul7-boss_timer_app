"""Ephemeral loopback sockets only; no Discord login or application status port."""
import os
import socket
import unittest
from unittest.mock import patch

import boss_timer_discord_bot as bot


class SingleListenerTests(unittest.TestCase):
    def test_second_listener_is_rejected_and_port_reopens_after_close(self):
        first = bot.ReusableThreadingHTTPServer(('127.0.0.1', 0), bot.StatusHandler)
        address = first.server_address
        try:
            with self.assertRaises(OSError):
                duplicate = bot.ReusableThreadingHTTPServer(address, bot.StatusHandler)
                duplicate.server_close()
            if os.name == 'nt':
                self.assertEqual(first.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
                # Even an older bot using SO_REUSEADDR cannot steal this listener.
                with socket.socket() as legacy:
                    legacy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    with self.assertRaises(OSError):
                        legacy.bind(address)
        finally:
            first.server_close()
        replacement = bot.ReusableThreadingHTTPServer(address, bot.StatusHandler)
        replacement.server_close()

    def test_busy_status_port_exits_before_constructing_discord_client(self):
        with patch.object(bot, 'start_status_server', return_value=None), \
             patch.object(bot, 'DiscordScheduleBot') as client:
            self.assertEqual(bot.main(), 1)
            client.assert_not_called()


if __name__ == '__main__':
    unittest.main()

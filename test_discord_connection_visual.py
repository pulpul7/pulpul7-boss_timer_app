import unittest
from pathlib import Path
import struct
from discord_connection_visual import visual_state,CONNECTING_COLORS
from boss_timer_gui import BossTimerApp


class Widget:
    def __init__(self): self.options={}
    def configure(self,**values): self.options.update(values)


class Root:
    def __init__(self): self.callbacks={}; self.counter=0
    def after(self,delay,callback):
        self.counter+=1; self.callbacks[self.counter]=callback; return self.counter
    def after_cancel(self,handle): self.callbacks.pop(handle,None)


class ConnectionVisualTests(unittest.TestCase):
    def app(self):
        app=object.__new__(BossTimerApp)
        app.root=Root(); app.schedule_window=Widget(); app.schedule_window_open=True
        app.discord_bot_status_label=Widget(); app.discord_bot_toggle_button=Widget()
        app.discord_bot_connection_frames=['off','half','almost','connected']
        app.discord_bot_last_status_payload={}; app.discord_bot_status_blink_after_id=None
        app._widget_available=lambda widget:widget is not None
        app._get_discord_bot_status_kind=lambda:'pending'
        app._is_discord_bot_process_alive=lambda:True
        return app

    def test_pending_cycles_three_colors_but_never_connected_image(self):
        states=[visual_state('pending',tick) for tick in range(18)]
        self.assertEqual({s[1] for s in states},set(CONNECTING_COLORS))
        self.assertNotIn(3,[s[0] for s in states])
        self.assertTrue(all(s[3] for s in states))

    def test_online_and_shutdown_are_stable(self):
        self.assertEqual(visual_state('online')[0],3)
        self.assertFalse(visual_state('online')[3])
        self.assertFalse(visual_state('pending',stopping=True)[3])

    def test_refresh_does_not_duplicate_timers_and_online_cancels(self):
        app=self.app()
        app._apply_discord_bot_status_label_style('pending')
        app._apply_discord_bot_status_label_style('pending')
        self.assertEqual(len(app.root.callbacks),1)
        app._apply_discord_bot_status_label_style('online')
        self.assertFalse(app.root.callbacks)
        self.assertEqual(app.discord_bot_status_label.options['image'],'connected')
        self.assertEqual(app.discord_bot_toggle_button.options['bg'],'#15803d')

    def test_closed_window_has_no_animation_timer(self):
        app=self.app(); app._apply_discord_bot_status_label_style('pending')
        app.schedule_window_open=False
        app._apply_discord_bot_status_label_style('pending')
        self.assertFalse(app.root.callbacks)

    def test_error_color_and_packaged_png(self):
        app=self.app(); app._apply_discord_bot_status_label_style('error')
        self.assertEqual(app.discord_bot_toggle_button.options['bg'],'#b91c1c')
        data=(Path(__file__).parent/'assets/discord_plug_connection.png').read_bytes()
        self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
        width,height=struct.unpack('!II',data[16:24])
        self.assertEqual(width%2,0); self.assertEqual(height%2,0)


if __name__=='__main__': unittest.main()

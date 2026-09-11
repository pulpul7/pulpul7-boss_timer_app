import copy
from datetime import datetime
import queue
import threading
import unittest
from unittest.mock import Mock,patch

from precision_capture_ui import retry_targets_for,merge_retry,apply_measurements,start
from precision_capture_session import PrecisionCaptureSession,TITLE_GUARD,TAB_GUARD
from precision_time_tracker import PrecisionConfig,Rect
from test_precision_capture import FakeApp,FakeCapture,SLOTS,MINUTE


class RetryTests(unittest.TestCase):
    def test_retry_prompt_is_opt_in_and_keeps_parent_and_successes(self):
        for answer in (False,True):
            with self.subTest(answer=answer):
                result,report=self.initial()
                report.update(t0=100.,ended_at=140.,max_capture_interval=.2,max_jitter=.01)
                app=Mock()
                app._precision_session=None
                app.schedule_input_ocr_worker_active=False
                app.schedule_input_ocr_addon_busy=False
                app.precision_capture_rate=5
                app._get_preferred_odin_window_handle.return_value=1
                app._get_odin_client_screen_rect.return_value={'left':0,'top':0,'width':1600,'height':900}
                app._get_schedule_server_profile_dir.return_value='profile'
                app.schedule_input_ocr_mode_results={}
                target=Mock(); text=['original']
                target.get.side_effect=lambda *args:text[0]
                app._get_schedule_input_ocr_text_widget_for_mode.return_value=target
                app._render_schedule_input_ocr_text.side_effect=lambda *args:text.__setitem__(0,'rendered')
                app._show_centered_messagebox.return_value=answer
                callbacks=[]
                app.root.after.side_effect=lambda delay,callback:callbacks.append(callback)
                session=Mock(config=PrecisionConfig(),wall0=datetime(2026,9,11),debug_logging=False)
                session.events=queue.Queue(); session.cancel=threading.Event()
                session.start.side_effect=lambda:session.events.put(('done',(result,report)))
                with patch('precision_capture_ui.PrecisionCaptureSession',return_value=session), \
                     patch('precision_capture_widgets.CaptureProgress'), \
                     patch('precision_capture_ui.start') as restart:
                    start(app,SLOTS)
                    for _ in range(8):
                        if not callbacks: break
                        callbacks.pop(0)()
                    self.assertEqual(app._show_centered_messagebox.call_count,1)
                    ask=app._show_centered_messagebox.call_args
                    self.assertIs(ask.kwargs['parent'],app.schedule_input_window)
                    self.assertIn('• 실패',ask.args[2]); self.assertIn('• 깨짐',ask.args[2])
                    self.assertNotIn('• 정상',ask.args[2]); self.assertNotIn('• 하루',ask.args[2])
                    self.assertEqual(restart.call_count,int(answer))
                    if answer:
                        context=restart.call_args.kwargs['retry_context']
                        self.assertEqual(context[1]['results'],report['results'])

    def initial(self):
        result={'area':'test','slot_results':[
            dict(boss_name='정상',slot_index=1,state='TIMED',remaining_seconds=5000),
            dict(boss_name='실패',slot_index=2,state='TIMED',remaining_seconds=4000),
            dict(boss_name='깨짐',slot_index=3,state='UNKNOWN',severity='error'),
            dict(boss_name='하루',slot_index=4,state='TIMED',remaining_seconds=86400),
            dict(boss_name='출현',slot_index=5,state='ACTIVE',remaining_seconds=0)]}
        measured=dict(boss_name='정상',target_datetime='2026-09-12T01:02:03.456789',uncertainty_seconds=.2)
        return result,dict(total=5,results=[measured],excluded=4)

    def test_only_failed_or_broken_bosses_are_offered(self):
        self.assertEqual(retry_targets_for(*self.initial()),{'실패':2,'깨짐':3})

    def test_merge_preserves_exact_success_and_original_objects(self):
        old,report=self.initial(); before=copy.deepcopy((old,report))
        retry={'area':'test','slot_results':[dict(boss_name='실패',slot_index=2,state='TIMED',remaining_seconds=3940),
                                           dict(boss_name='정상',slot_index=1,state='UNKNOWN',severity='error')]}
        measured=dict(boss_name='실패',target_datetime='2026-09-12T02:03:04.123456',uncertainty_seconds=.15)
        merged,combined=merge_retry(old,report,retry,{'results':[measured]},retry_targets_for(old,report))
        self.assertEqual(combined['total'],5)
        self.assertEqual(combined['excluded'],3)
        self.assertEqual(combined['results'][0],report['results'][0])
        self.assertEqual(merged['slot_results'][0],old['slot_results'][0])
        self.assertEqual((old,report),before)
        rendered=apply_measurements(merged,combined)
        self.assertEqual(rendered['slot_results'][0]['rendered_text'],'01:02:03.456789 정상')
        self.assertEqual(rendered['slot_results'][1]['rendered_text'],'02:03:04.123456 실패')
        self.assertEqual(retry_targets_for(rendered,combined),{'깨짐':3})

    def test_other_chapter_cannot_merge(self):
        old,report=self.initial()
        with self.assertRaisesRegex(ValueError,'챕터'):
            merge_retry(old,report,{'area':'other'},{'results':[]},{'실패':2})

    def test_failed_retry_keeps_existing_fallback(self):
        old,report=self.initial()
        failed={'area':'test','slot_results':[dict(boss_name='실패',slot_index=2,state='UNKNOWN',severity='error')]}
        merged,combined=merge_retry(old,report,failed,{'results':[]},{'실패':2})
        self.assertEqual(merged,old)
        self.assertEqual(combined['results'],report['results'])

    def test_session_captures_only_requested_boss_and_clock_after_t0(self):
        app=FakeApp(); capture=FakeCapture(0)
        session=PrecisionCaptureSession(app,0,SLOTS,config=PrecisionConfig(interval=.2,duration=7),
                                        capture_factory=lambda _:capture,retry_targets={'second':2},expected_area='test')
        with patch('precision_capture_session.png_data',side_effect=lambda pixels:{'pixels':pixels}):
            session.run()
        events=[]
        while not session.events.empty(): events.append(session.events.get_nowait())
        self.assertFalse([data for kind,data in events if kind=='error'])
        report=next(data[1] for kind,data in events if kind=='done')
        self.assertEqual(report['total'],1)
        self.assertEqual([p['boss_name'] for p in report['results']],['second'])
        self.assertFalse(any(rect.contains(MINUTE) for rect,at in capture.samples if rect.width!=1600))

    def test_session_rejects_wrong_chapter(self):
        app=FakeApp(); capture=FakeCapture(0)
        session=PrecisionCaptureSession(app,0,{'other':SLOTS['test']},config=PrecisionConfig(duration=6),
                                        capture_factory=lambda _:capture,retry_targets={'second':2},expected_area='other')
        with patch('precision_capture_session.png_data',side_effect=lambda pixels:{'pixels':pixels}):
            session.run()
        events=[]
        while not session.events.empty(): events.append(session.events.get_nowait())
        self.assertTrue(any(kind=='error' and '챕터' in value for kind,value in events))
        self.assertFalse(any(kind=='done' for kind,value in events))


if __name__=='__main__': unittest.main()

"""Synthetic boundary, replay and safety tests; no game/OCR service required."""
from datetime import datetime, timedelta
import base64
import math
import queue
import struct
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from precision_time_tracker import (PrecisionConfig,Rect,Pixels,Sample,Tick,TickBuffer,
                                    RoiChangeDetector,BossTimeTracker,GameClockTracker,precision_result)
from precision_screen_capture import png_data
from precision_capture_session import PrecisionCaptureSession,numeric_roi,duration_seconds,CANDIDATES,timer_bounds
from precision_capture_ui import apply_measurements


class TrackerTests(unittest.TestCase):
    def test_change_midpoint_preserves_fraction_and_capture_window(self):
        detector=RoiChangeDetector(PrecisionConfig())
        rect=Rect(0,0,3,3)
        self.assertIsNone(detector.feed(Sample(10.237,10.239,Pixels(rect,b'\0'*27))))
        tick=detector.feed(Sample(10.741,10.744,Pixels(rect,b'\xff'*27)))
        self.assertAlmostEqual(tick.at,10.4905)
        self.assertAlmostEqual(tick.uncertainty,.2535)

    def test_gap_rejected_even_if_pixels_unchanged(self):
        d=RoiChangeDetector(PrecisionConfig())
        p=Pixels(Rect(0,0,3,3),b'\0'*27)
        d.feed(Sample(0,0,p))
        with self.assertRaisesRegex(ValueError,'gap'):
            d.feed(Sample(1.1,1.1,p))

    def test_ring_is_bounded_and_reports_lost_initial_history(self):
        b=TickBuffer(3)
        for i in range(4): b.append(i)
        self.assertEqual(list(b.samples),[1,2,3])
        self.assertTrue(b.overflow)

    def test_minute_floor_boundary_and_late_clock_phase(self):
        b=BossTimeTracker('minute',15480,'minute',PrecisionConfig())
        b.add(Tick(59.25,59.75))
        c=GameClockTracker(datetime(2026,9,10,12,0,0),[Tick(i-.25,i+.25) for i in (1,2,3,4)])
        r=precision_result(b,c)
        self.assertEqual(r['target_datetime'],'2026-09-10T16:18:59.500000')
        self.assertAlmostEqual(r['uncertainty_seconds'],.5)
        self.assertLessEqual(abs(r['clock_offsets_seconds'][0]),.5)

    def test_second_countdown_candidates_and_midnight(self):
        b=BossTimeTracker('seconds',60,'second',PrecisionConfig())
        for i in range(4): b.add(Tick(100.3+i,100.8+i))
        self.assertTrue(b.enough)
        self.assertEqual(len(set(b.candidates)),1)
        self.assertTrue(b.verify_remaining(56))
        c=GameClockTracker(datetime(2026,9,10,23,59,59),[Tick(100.1+i,100.6+i) for i in range(4)])
        r=precision_result(b,c)
        self.assertEqual(datetime.fromisoformat(r['target_datetime']).date(),datetime(2026,9,11).date())

    def test_missed_or_animation_tick_rejected(self):
        b=BossTimeTracker('bad',100,'second',PrecisionConfig())
        b.add(Tick(0,.5)); b.add(Tick(2,2.5))
        self.assertTrue(b.error)

    def test_small_roi_png_roundtrip(self):
        raw=bytes(range(18))
        data=base64.b64decode(png_data(Pixels(Rect(40,50,43,52),raw))['image_data'])
        self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('!II',data[16:24]),(3,2))
        offset=8
        while offset<len(data):
            length=struct.unpack('!I',data[offset:offset+4])[0]
            if data[offset+4:offset+8]==b'IDAT':
                self.assertEqual(zlib.decompress(data[offset+8:offset+8+length]),b'\0'+raw[:9]+b'\0'+raw[9:])
            offset+=length+12

    def test_roi_with_joined_and_split_units(self):
        bounds=Rect(0,0,200,40)
        joined=[dict(text='18분',left=40,top=10,width=30,height=20)]
        self.assertEqual(numeric_roi(joined,bounds,'분'),Rect(38,8,62,32))
        split=[dict(text='18',left=40,top=10,width=20,height=20),dict(text='분',left=63,top=10,width=10,height=20)]
        self.assertEqual(numeric_roi(split,bounds,'분'),Rect(38,8,62,32))
        self.assertEqual(duration_seconds('4시간 18분'),15480)
        self.assertIsNone(duration_seconds('unknown'))
        self.assertIsNone(duration_seconds('18분 17분'))
        clock=[dict(text=t,left=10+i*20,top=10,width=16,height=16)
               for i,t in enumerate(('12',':','34',':','56'))]
        self.assertEqual(numeric_roi(clock,bounds),Rect(88,8,108,28))

    def test_fixed_candidates_cover_every_existing_slot_grid(self):
        import boss_timer_gui as gui
        app=object.__new__(gui.BossTimerApp)
        board=dict(left=176,top=190,right=1410,bottom=709)
        for area in gui.SCHEDULE_OCR_SLOT_GRID:
            for r in app._get_schedule_ocr_slot_rects(area,1600,900,window_rect=board):
                timer=timer_bounds(r)
                self.assertTrue(any(c.contains(timer) for c in CANDIDATES),(area,timer))

    def test_split_digits_and_unit_joined_to_suffix(self):
        words=[dict(text=t,left=x,top=10,width=width,height=16)
               for t,x,width in [('1',40,8),('8',50,8),('분 남음',60,40)]]
        roi=numeric_roi(words,Rect(0,0,200,40),'분')
        self.assertEqual(roi,Rect(38,8,60,28))

    def test_odd_chapter_omits_empty_lower_slots(self):
        import boss_timer_gui as gui
        app=object.__new__(gui.BossTimerApp)
        slots=app._get_precision_capture_slots()
        for area,top,bottom in [('니플하임',3,2),('바나하임',3,2),('무스펠하임',4,3)]:
            self.assertEqual(len(slots[area]),top+bottom)
            self.assertEqual(sum(s['timer_top']<600 for s in slots[area]),top)

    def test_layout_insets_and_boss_list_updates(self):
        from precision_capture_layout import chapter_slots,GAME_CLOCK_BOUNDS,CHAPTER_OBSERVATION_RECTS
        self.assertEqual(GAME_CLOCK_BOUNDS,Rect(190,256,420,306))
        self.assertEqual(CHAPTER_OBSERVATION_RECTS['요툰하임'][0],Rect(463,462,635,492))
        changed=chapter_slots({'무스펠하임':['boss']*5})['무스펠하임']
        self.assertEqual(len(changed),5)
        self.assertEqual(sum(r['timer_top']<600 for r in changed),3)
        with self.assertRaisesRegex(ValueError,'8칸'):
            chapter_slots({'new':['boss']*9})

    def test_unconfirmed_input_stays_original_and_warns(self):
        original=dict(slot_results=[dict(boss_name='boss',state='TIMED',remaining_seconds=4000,rendered_text='12:10 boss')])
        slot=apply_measurements(original,{'results':[]})['slot_results'][0]
        self.assertEqual(slot['rendered_text'],'12:10 boss')
        self.assertEqual(slot['severity'],'warn')

    def test_output_and_datetime_preserve_every_captured_digit(self):
        original=dict(slot_results=[dict(boss_name='boss',state='TIMED',remaining_seconds=4000)])
        measured=dict(boss_name='boss',target_datetime='2026-09-10T23:59:59.765432',uncertainty_seconds=.251)
        output=apply_measurements(original,{'results':[measured]})['slot_results'][0]
        self.assertEqual(output['rendered_text'],'23:59:59.765432 boss')
        self.assertEqual(output['target_datetime'],datetime(2026,9,10,23,59,59,765432))
        self.assertEqual(output['precision_measurement']['target_datetime'],measured['target_datetime'])
        self.assertNotIn('precision_measurement',original['slot_results'][0])


CLOCK=Rect(300,270,380,290)
MINUTE=Rect(510,470,540,490)
SECOND=Rect(750,470,780,490)
SLOTS={'test':[dict(slot_index=1,timer_left=420,timer_top=460,timer_right=658,timer_bottom=489),
               dict(slot_index=2,timer_left=665,timer_top=460,timer_right=903,timer_bottom=489)]}


class FakeCapture:
    def __init__(self,hwnd):
        self.started=time.perf_counter()
        self.samples=[]

    def grab(self,rect,check_visible=True):
        start=time.perf_counter()
        elapsed=start-self.started
        clock=max(0,math.floor(elapsed-.2)+1)
        second=max(0,math.floor(elapsed-.7)+1)
        minute=int(elapsed>=.7)
        raw=bytearray(rect.width*rect.height*3)
        for region,value in ((CLOCK,clock),(MINUTE,minute),(SECOND,second)):
            left,top=max(rect.left,region.left),max(rect.top,region.top)
            right,bottom=min(rect.right,region.right),min(rect.bottom,region.bottom)
            if right<=left or bottom<=top: continue
            for y in range(top,bottom):
                offset=((y-rect.top)*rect.width+left-rect.left)*3
                raw[offset:offset+(right-left)*3]=bytes([40*(value%6)])*((right-left)*3)
        sample=Sample(start,time.perf_counter(),Pixels(rect,bytes(raw)))
        self.samples.append((rect,sample.at))
        return sample


class FakeApp:
    def __init__(self):
        self.calls=[]
        self.started=threading.Event()
        self.completed=threading.Event()

    def _run_schedule_windows_ocr(self,item,scale):
        pixels=item['pixels']
        self.calls.append((pixels.rect,time.perf_counter()))
        if pixels.rect.width==1600:
            return {'words':[dict(text='12:00:00',left=300,top=270,width=80,height=20),
                             dict(text='18분',left=510,top=470,width=30,height=20),
                             dict(text='03초',left=750,top=470,width=30,height=20)]}
        if pixels.rect==CANDIDATES[0]:
            n=pixels.crop(CLOCK).rgb[0]//40
            return {'text':f'12:00:{n:02d}'}
        if pixels.rect==timer_bounds(SLOTS['test'][0]):
            n=pixels.crop(Rect(520,475,521,476)).rgb[0]//40
            return {'text':f'4시간 {18-n:02d}분'}
        n=pixels.crop(Rect(760,475,761,476)).rgb[0]//40
        remaining=123-n
        return {'text':f'{remaining//60:02d}분 {remaining%60:02d}초'}

    def _build_schedule_input_ocr_result_for_item(self,item,index):
        self.started.set()
        self._run_schedule_windows_ocr(item,1)
        time.sleep(1.3)
        self.completed.set()
        return {'area':'test','base_datetime':datetime.now().replace(hour=12,minute=0,second=0,microsecond=0),
                'slot_results':[dict(boss_name='minute',slot_index=1,state='TIMED',remaining_seconds=15480,severity='normal'),
                                dict(boss_name='second',slot_index=2,state='TIMED',remaining_seconds=123,severity='normal'),
                                dict(boss_name='day',slot_index=3,state='TIMED',remaining_seconds=86400,severity='normal')]}


class SessionTests(unittest.TestCase):
    def test_verbose_logs_are_opt_in_without_losing_in_memory_trace(self):
        app=FakeApp()
        session=PrecisionCaptureSession(app,0,SLOTS)
        session.log('roi_capture example')
        self.assertTrue(session.events.empty())
        self.assertIn('precision roi_capture example',session.trace)
        session.log('error example')
        self.assertEqual(session.events.get_nowait(),('log','precision error example'))
        app.precision_debug_logging=True
        session=PrecisionCaptureSession(app,0,SLOTS)
        session.log('roi_capture example')
        self.assertEqual(session.events.get_nowait(),('log','precision roi_capture example'))

    def test_ten_hz_keeps_early_history_until_delayed_ocr_finishes(self):
        app=FakeApp(); capture=FakeCapture(0)
        s=PrecisionCaptureSession(app,0,SLOTS,config=PrecisionConfig.from_rate(10),capture_factory=lambda hwnd:capture)
        events=[]
        with patch('precision_capture_session.png_data',side_effect=lambda pixels:{'pixels':pixels}):
            s.start()
            while True:
                event=s.events.get(timeout=10); events.append(event)
                if event[0]=='stopped': break
        self.assertFalse([data for kind,data in events if kind=='error'])
        report=next(data[1] for kind,data in events if kind=='done')
        self.assertEqual({r['boss_name'] for r in report['results']},{'minute','second'})
        self.assertGreater(sum(report['t0']<at<report['t0']+1.2 for r,at in capture.samples),20)
        self.assertTrue(any('ocr1_start' in line for line in report['milestones']))
        classification = next(line for line in report['milestones'] if 'classification_start elapsed=' in line)
        self.assertGreaterEqual(float(classification.split('elapsed=')[1]), 4.0)

    def test_delayed_ocr_parallel_sampling_early_rollover_and_early_stop(self):
        app=FakeApp()
        capture=FakeCapture(0)
        s=PrecisionCaptureSession(app,0,SLOTS,config=PrecisionConfig(interval=.25,duration=7),capture_factory=lambda hwnd:capture)
        events=[]
        with patch('precision_capture_session.png_data',side_effect=lambda pixels:{'pixels':pixels}):
            s.start()
            while True:
                event=s.events.get(timeout=10)
                events.append(event)
                if event[0]=='stopped': break
        self.assertFalse([e for e in events if e[0]=='error'],events)
        result,report=next(data for kind,data in events if kind=='done')
        self.assertEqual({r['boss_name'] for r in report['results']},{'minute','second'},events)
        self.assertLess(report['ended_at']-report['t0'],6)
        initial=[r for r,at in capture.samples if r.width==1600]
        self.assertEqual(len(initial),1)
        self.assertGreater(sum(report['t0']<at<report['t0']+1.2 for r,at in capture.samples),5)
        minute=next(r for r in report['results'] if r['boss_name']=='minute')
        self.assertLess(minute['ticks'][0]['midpoint']-report['t0'],1.2)
        self.assertEqual(len(app.calls),4)  # one full OCR plus one confirmation per target and clock
        self.assertTrue(report['clock_verified'])
        self.assertEqual(report['total'],3)
        self.assertEqual(report['completed'],3)
        self.assertEqual(report['excluded'],1)
        for kind,data in events:
            if kind=='progress' and data[1] is not None:
                _,total,remaining,completed=data
                self.assertEqual(total,remaining+completed)

    def test_missing_numeric_roi_retries_only_initial_small_crop(self):
        class RetryApp(FakeApp):
            def _run_schedule_windows_ocr(self,item,scale):
                result=super()._run_schedule_windows_ocr(item,scale)
                pixels=item['pixels']
                if pixels.rect.width==1600:
                    result['words']=[w for w in result['words'] if w['text']!='18분']
                elif pixels.rect==timer_bounds(SLOTS['test'][0]) and result['text']=='4시간 18분':
                    result['words']=[dict(text='18분',left=510-pixels.rect.left,
                                          top=470-pixels.rect.top,width=30,height=16)]
                return result
        app=RetryApp(); capture=FakeCapture(0)
        s=PrecisionCaptureSession(app,0,SLOTS,config=PrecisionConfig(interval=.25,duration=7),capture_factory=lambda hwnd:capture)
        with patch('precision_capture_session.png_data',side_effect=lambda pixels:{'pixels':pixels}):
            s.run()
        events=[]
        while not s.events.empty(): events.append(s.events.get_nowait())
        self.assertFalse([data for kind,data in events if kind=='error'])
        report=next(data[1] for kind,data in events if kind=='done')
        self.assertEqual({r['boss_name'] for r in report['results']},{'minute','second'})
        self.assertTrue(any('roi_retry name=minute ok=True' in line for line in report['milestones']))
        self.assertEqual(sum(rect.width==1600 for rect,at in app.calls),1)

    def test_cancel_during_ocr_keeps_no_results(self):
        app=FakeApp()
        capture=FakeCapture(0)
        s=PrecisionCaptureSession(app,0,SLOTS,capture_factory=lambda hwnd:capture)
        events=[]
        with patch('precision_capture_session.png_data',side_effect=lambda pixels:{'pixels':pixels}):
            s.start()
            self.assertTrue(app.started.wait(2))
            s.cancel.set()
            while True:
                event=s.events.get(timeout=3); events.append(event)
                if event[0]=='stopped': break
            self.assertTrue(app.completed.wait(3))
        self.assertFalse(any(kind=='done' for kind,data in events))
        self.assertTrue(any(kind=='cancelled' for kind,data in events))

    def test_only_day_bosses_stop_after_four_second_collection(self):
        class DayApp(FakeApp):
            def _build_schedule_input_ocr_result_for_item(self,item,index):
                self._run_schedule_windows_ocr(item,1)
                return dict(area='test',base_datetime=datetime.now(),slot_results=[
                    dict(boss_name='day',state='TIMED',remaining_seconds=86400)])
        app=DayApp(); capture=FakeCapture(0)
        s=PrecisionCaptureSession(app,0,SLOTS,config=PrecisionConfig(interval=.25),capture_factory=lambda hwnd:capture)
        with patch('precision_capture_session.png_data',side_effect=lambda pixels:{'pixels':pixels}):
            s.run()
        events=[]
        while not s.events.empty(): events.append(s.events.get_nowait())
        report=next(data[1] for kind,data in events if kind=='done')
        self.assertEqual(report['results'],[])
        self.assertGreaterEqual(report['ended_at']-report['t0'],4)
        self.assertLess(report['ended_at']-report['t0'],6)
        self.assertEqual(len(app.calls),1)

    def test_timeout_never_labels_missing_ticks_as_precise(self):
        app=FakeApp(); capture=FakeCapture(0)
        s=PrecisionCaptureSession(app,0,SLOTS,config=PrecisionConfig(interval=.25,duration=2),capture_factory=lambda hwnd:capture)
        with patch('precision_capture_session.png_data',side_effect=lambda pixels:{'pixels':pixels}):
            s.run()
        events=[]
        while not s.events.empty(): events.append(s.events.get_nowait())
        report=next(data[1] for kind,data in events if kind=='done')
        self.assertFalse(report['clock_verified'])
        self.assertEqual(report['results'],[])


if __name__=='__main__':
    unittest.main()

"""Join one existing OCR1 analysis with an independent ROI capture worker."""
from __future__ import annotations

import copy
from collections import deque
from dataclasses import asdict
from datetime import datetime, timedelta
import queue
import re
import threading
import time

from precision_screen_capture import ScreenCapture, png_data
from precision_capture_layout import CANDIDATES, timer_bounds
from precision_time_tracker import (PrecisionConfig, Rect, Sample, TickBuffer,
                                    RoiChangeDetector, StableRoiChangeDetector,
                                    BossTimeTracker, GameClockTracker, precision_result)

# Client coordinates for the existing 1600x900 Odin timetable.  These contain
# all 3/4-column timer candidates BEFORE OCR discovers the selected region.
TITLE_GUARD = Rect(730, 202, 900, 227)
TAB_GUARD = Rect(450, 235, 1370, 266)


def word_rect(word):
    return Rect(int(word['left']), int(word['top']), int(word['left']+word['width']), int(word['top']+word['height']))


def numeric_roi(words, bounds, unit=None):
    """Narrow a fixed timer candidate to the numeric glyphs of its last unit.

Coordinates are selected once from T0 OCR, never re-estimated per frame.
Unrecognized units are rejected rather than measuring a whole card.
"""
    candidates = [w for w in words if bounds.contains(word_rect(w))]
    for word in candidates:
        rect = word_rect(word)
        if not bounds.contains(rect):
            continue
        text = str(word.get('text', ''))
        match = re.search(r'(\d{1,2})\s*' + re.escape(unit), text) if unit else re.search(r'\d{2}:\d{2}:(\d{2})', text)
        if not match:
            # Windows OCR sometimes separates the number and Korean unit.
            if unit and text.strip() == unit:
                previous = [w for w in candidates if re.fullmatch(r'\d{1,2}', str(w.get('text','')).strip())
                            and 0 <= rect.left-word_rect(w).right <= 16
                            and abs(word_rect(w).top-rect.top) <= 8]
                if previous:
                    number = word_rect(max(previous, key=lambda w: word_rect(w).right))
                    return Rect(max(bounds.left,number.left-2),max(bounds.top,number.top-2),
                                min(bounds.right,number.right+2),min(bounds.bottom,number.bottom+2))
            continue
        start, end = match.span(1)
        left = max(bounds.left, int(rect.left+rect.width*start/len(text))-2)
        right = min(bounds.right, int(rect.left+rect.width*end/len(text))+2)
        return Rect(left, max(bounds.top, rect.top-2), right, min(bounds.bottom, rect.bottom+2))
    if unit:
        # OCR can split '18분' into '1', '8', '분 남음', or join the
        # number to '시간'. Reconstruct adjacent text with glyph positions.
        unique = {(str(w.get('text','')).strip(), word_rect(w)): w for w in candidates}
        for first in unique.values():
            row = sorted((w for w in unique.values() if abs(word_rect(w).top-word_rect(first).top)<=5),
                         key=lambda w: word_rect(w).left)
            positions = []
            previous = None
            for w in row:
                text = str(w.get('text','')).strip(); r = word_rect(w)
                if previous is not None and (r.left < previous.right-2 or r.left-previous.right>18):
                    positions.append(('|',0,0,r))
                positions.extend((ch,r.left+r.width*i/max(1,len(text)),r.left+r.width*(i+1)/max(1,len(text)),r)
                                 for i,ch in enumerate(text) if not ch.isspace())
                previous = r
            match = re.search(r'(?<!\d)(\d{1,2})'+re.escape(unit), ''.join(p[0] for p in positions))
            if match:
                selected = positions[match.start(1):match.end(1)]
                return Rect(max(bounds.left,int(selected[0][1])-2),max(bounds.top,min(p[3].top for p in selected)-2),
                            min(bounds.right,int(selected[-1][2])+2),min(bounds.bottom,max(p[3].bottom for p in selected)+2))
    if not unit:
        # Handle an OCR line split as HH / : / MM / : / SS as well.
        unique = {(str(w.get('text','')).strip(),word_rect(w)):w for w in candidates}
        ordered = sorted(unique.values(),key=lambda w:(word_rect(w).top//8,word_rect(w).left))
        for first in ordered:
            row = sorted((w for w in ordered if abs(word_rect(w).top-word_rect(first).top)<=5),
                         key=lambda w:word_rect(w).left)
            positions=[]
            for w in row:
                text=str(w.get('text','')).strip(); r=word_rect(w)
                positions.extend((ch,r.left+r.width*i/max(1,len(text)),r.left+r.width*(i+1)/max(1,len(text)),r)
                                 for i,ch in enumerate(text))
            match=re.search(r'\d{2}:\d{2}:(\d{2})',''.join(p[0] for p in positions))
            if match:
                selected=positions[match.start(1):match.end(1)]
                return Rect(max(bounds.left,int(selected[0][1])-2),max(bounds.top,min(p[3].top for p in selected)-2),
                            min(bounds.right,int(selected[-1][2])+2),min(bounds.bottom,max(p[3].bottom for p in selected)+2))
    return None


def duration_seconds(text):
    parts = re.findall(r'(\d+)\s*(일|시간|분|초)', text)
    if not parts or len({u for n,u in parts})!=len(parts):
        return None
    return sum(int(n)*{'일':86400, '시간':3600, '분':60, '초':1}[u] for n, u in parts)


class PrecisionCaptureSession:
    def __init__(self, app, hwnd, slots, *, config=None, capture_factory=ScreenCapture,
                 retry_targets=None, expected_area=None):
        self.config = config or PrecisionConfig()
        self.hwnd, self.slots, self.capture_factory = hwnd, slots, capture_factory
        self.retry_targets = dict(retry_targets) if retry_targets is not None else None
        self.expected_area = expected_area
        self.candidates = CANDIDATES
        if self.retry_targets is not None:
            selected = [timer_bounds(slot) for slot in slots.get(expected_area, [])
                        if slot['slot_index'] in self.retry_targets.values()]
            if not selected or len(selected) != len(set(self.retry_targets.values())):
                raise ValueError('재시도할 보스의 감지영역을 찾지 못했습니다.')
            self.candidates = (CANDIDATES[0], *selected)
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.ocr_results = queue.Queue()
        self.verify_jobs, self.verify_results = queue.Queue(), queue.Queue()
        self.overlay_handles = set()
        self.debug_logging = bool(getattr(app, 'precision_debug_logging', False))
        # Isolate per-run OCR attributes.  Do not monkey-patch the live GUI or
        # change either OCR1 or OCR2.  UI integration locks competing OCR jobs.
        self.ocr = copy.copy(app)
        # The GUI owns the persistent PowerShell process and its shared lock.
        # A shallow copy must not create an orphaned host of its own. Only OCR
        # requests share this service; native ROI capture never takes its lock.
        if hasattr(app, '_run_schedule_ocr_powershell'):
            self.ocr._run_schedule_ocr_powershell = app._run_schedule_ocr_powershell
        self.ocr._append_debug_log = self.log
        self.raw_words = []
        self.trace = deque(maxlen=4096)
        self.milestones = deque(maxlen=512)
        self.capture_stats = {}
        self.capture_times = {}
        self.max_interval = self.max_jitter = 0.0
        original = self.ocr._run_schedule_windows_ocr
        def record(*args, **kwargs):
            if self.cancel.is_set():
                raise RuntimeError('정밀 OCR 취소됨')
            result = original(*args, **kwargs)
            self.raw_words.extend(result.get('words') or [])
            return result
        self.ocr._run_schedule_windows_ocr = record
        self.raw_ocr = original

    def log(self, message):
        line = f'precision {message}'
        self.trace.append(line)
        if not message.startswith('roi_capture'):
            self.milestones.append(line)
        verbose = message.startswith(('roi_capture', 'tick key=', 'candidate ', 'roi_unresolved'))
        if self.debug_logging or not verbose:
            self.events.put(('log', line))

    def start(self):
        threading.Thread(target=self.run, name='PrecisionCapture', daemon=True).start()

    def measured_grab(self, capture, rect):
        sample = capture.grab(rect)
        previous = self.capture_times.get(rect)
        if previous is not None:
            interval = sample.at-previous
            jitter = interval-self.config.interval
            self.max_interval = max(self.max_interval,interval)
            self.max_jitter = max(self.max_jitter,abs(jitter))
            stats = self.capture_stats.setdefault(str(rect),dict(interval_count=0,total_interval=0.0,max_interval=0.0))
            stats['interval_count'] += 1
            stats['total_interval'] += interval
            stats['max_interval'] = max(stats['max_interval'],interval)
            self.log(f'roi_capture rect={rect} start={sample.start:.6f} end={sample.end:.6f} interval={interval:.6f} jitter={jitter:+.6f}')
        else:
            self.log(f'roi_capture rect={rect} start={sample.start:.6f} end={sample.end:.6f}')
        self.capture_times[rect] = sample.at
        return sample

    def analyse(self, first):
        started = time.perf_counter()
        self.log(f'ocr1_start at={started:.6f}')
        try:
            item = png_data(first.pixels)
            item.update(id='precision', captured_at=self.wall0)
            # Anchor OCR date selection to the initial capture even if OCR
            # finishes after midnight.  OCR values still refer to T0.
            self.ocr._get_schedule_reference_datetime = lambda: self.wall0
            result = self.ocr._build_schedule_input_ocr_result_for_item(item, 0)
            self.log(f'ocr1_analysis_done duration={time.perf_counter()-started:.6f}')
            # Retry unresolved numeric locations once, using ONLY T0's small
            # timer crop. Capture continues independently while this runs.
            for slot in result.get('slot_results', []):
                if self.retry_targets is not None and slot.get('boss_name') not in self.retry_targets:
                    continue
                remaining = slot.get('remaining_seconds')
                if (self.cancel.is_set() or slot.get('state')!='TIMED' or slot.get('severity')=='error'
                        or not isinstance(remaining,int) or not 0<remaining<86400):
                    continue
                rect = next((r for r in self.slots.get(result.get('area'),[])
                             if r['slot_index']==slot.get('slot_index')), None)
                if rect is None:
                    continue
                bounds = timer_bounds(rect)
                unit = '분' if remaining>=3600 else '초'
                if numeric_roi(self.raw_words,bounds,unit) is not None:
                    continue
                nearby = [w for w in self.raw_words if bounds.contains(word_rect(w))]
                self.log(f'roi_unresolved name={slot.get("boss_name")} bounds={bounds} words={nearby}')
                try:
                    retry = self.raw_ocr(png_data(first.pixels.crop(bounds)),3.0)
                    text = str(retry.get('text') or '')
                    adjusted = [dict(w,left=float(w['left'])+bounds.left,top=float(w['top'])+bounds.top)
                                for w in retry.get('words',[])]
                    recovered = numeric_roi(adjusted,bounds,unit)
                    ok = duration_seconds(text)==remaining and recovered is not None
                    if ok:
                        self.raw_words.extend(adjusted)
                    self.log(f'roi_retry name={slot.get("boss_name")} ok={ok} text={text!r} roi={recovered}')
                except Exception as exc:
                    self.log(f'roi_retry name={slot.get("boss_name")} error={exc}')
            self.ocr_results.put((result, list(self.raw_words)))
        except Exception as exc:
            self.ocr_results.put(exc)
        finally:
            self.raw_words.clear()
            self.log(f'ocr1_done at={time.perf_counter():.6f} duration={time.perf_counter()-started:.6f}')

    def verify_worker(self):
        while not self.cancel.is_set():
            try:
                key, sample, expected, clock = self.verify_jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                result = self.raw_ocr(png_data(sample.pixels), 3.0)
                text = str(result.get('text') or '')
                if clock:
                    tokens = re.findall(r'(\d{2})\s*:\s*(\d{2})\s*:\s*(\d{2})', text.replace('：',':'))
                    value = sum(int(n)*s for n, s in zip(tokens[0], (3600,60,1))) if len(tokens) == 1 else None
                else:
                    value = duration_seconds(text)
                self.verify_results.put((key, value == expected, text))
            except Exception as exc:
                self.verify_results.put((key, False, str(exc)))

    def run(self):
        started = time.perf_counter()
        self.log(f'tracker_start at={started:.6f} config={asdict(self.config)}')
        try:
            self._run()
        except Exception as exc:
            self.events.put(('error', str(exc)))
            self.log(f'error {type(exc).__name__}: {exc}')
        finally:
            self.cancel.set()
            self.log(f'tracker_end at={time.perf_counter():.6f} duration={time.perf_counter()-started:.6f}')
            self.events.put(('stopped', None))

    def _run(self):
        if self.cancel.is_set():
            self.events.put(('cancelled', None))
            return
        capture = self.capture_factory(self.hwnd)
        capture.overlay_handles = self.overlay_handles
        first = capture.grab(Rect(0,0,1600,900), check_visible=False)
        self.t0 = first.at
        self.wall0 = datetime.now() - timedelta(seconds=time.perf_counter()-self.t0)
        self.log(f'capture_t0 start={first.start:.6f} end={first.end:.6f} midpoint={self.t0:.6f}')
        buffers = {rect: TickBuffer(self.config.buffer_samples) for rect in self.candidates}
        for rect, buffer in buffers.items():
            buffer.append(Sample(first.start, first.end, first.pixels.crop(rect)))
        guards = [first.pixels.crop(r) for r in (TITLE_GUARD,TAB_GUARD)]
        guard_changes = {r.rect: 0 for r in guards}
        self.capture_times.update({r:self.t0 for r in self.candidates})
        # Start OCR only AFTER the T0 ROI samples exist.  This thread continues
        # sampling immediately; no OCR result/join is awaited here.
        threading.Thread(target=self.analyse, args=(first,), name='PrecisionOCR1', daemon=True).start()
        del first
        self.events.put(('regions', list(self.candidates)))
        threading.Thread(target=self.verify_worker, name='PrecisionVerify', daemon=True).start()
        observations, result, clock = {}, None, None
        next_at = self.t0 + self.config.interval
        verified = {}
        total = None
        visible_regions = None
        while not self.cancel.is_set() and time.perf_counter()-self.t0 <= self.config.duration:
            if self.cancel.wait(max(0, next_at-time.perf_counter())):
                break
            for initial_guard in guards:
                guard = self.measured_grab(capture,initial_guard.rect)
                changed = sum(abs(a-b)>self.config.pixel_delta for a,b in zip(initial_guard.rgb,guard.pixels.rgb))
                guard_changes[initial_guard.rect] = guard_changes[initial_guard.rect]+1 if changed>120 else 0
                if guard_changes[initial_guard.rect]>=2:
                    raise RuntimeError('시간표 제목/지역이 변경됐습니다. 정밀 스캔을 다시 시작해주세요.')
            if result is None:
                for rect, buffer in buffers.items():
                    buffer.append(self.measured_grab(capture,rect))
                # Keep ALL fixed candidate bands from T0 for at least four
                # seconds. OCR may finish earlier, but cannot exclude a slot
                # or stop a detector before this history has been collected.
                output = None
                if time.perf_counter()-self.t0 >= self.config.warmup_seconds:
                    try:
                        output = self.ocr_results.get_nowait()
                    except queue.Empty:
                        pass
                if isinstance(output, Exception):
                    raise output
                if output:
                    if any(b.overflow for b in buffers.values()):
                        raise RuntimeError('최초 OCR 대기 버퍼 초과: 정밀 측정을 확정하지 않았습니다.')
                    result, words = output
                    if self.expected_area is not None and result.get('area') != self.expected_area:
                        raise RuntimeError('재시도할 챕터와 현재 화면이 다릅니다. 기존 결과를 유지합니다.')
                    selected_slots = [s for s in result.get('slot_results', [])
                                      if self.retry_targets is None or s.get('boss_name') in self.retry_targets]
                    total = (len(self.retry_targets) if self.retry_targets is not None else
                             max(len(self.slots.get(result.get('area'),[])),len(selected_slots)))
                    self.log(f'classification_start elapsed={time.perf_counter()-self.t0:.6f}')
                    base = result.get('base_datetime')
                    if not isinstance(base, datetime):
                        raise RuntimeError('최초 게임시계를 읽지 못했습니다.')
                    # Choose nearest day to T0; an OCR return after midnight
                    # must not turn a 23:59:59 capture into tomorrow's schedule.
                    base = min((base+timedelta(days=d) for d in (-1,0,1)), key=lambda dt: abs((dt-self.wall0).total_seconds()))
                    self.log(f'game_clock_initial={base.isoformat()} t0={self.t0:.6f}')
                    eligible = [s for s in selected_slots if s.get('state')=='TIMED'
                                and isinstance(s.get('remaining_seconds'),int)
                                and 0<s['remaining_seconds']<86400 and s.get('severity')!='error']
                    if not eligible:
                        self.log(f'no_trackable_bosses initial_slots={result.get("slot_results",[])}')
                        break
                    clock = GameClockTracker(base)
                    clock_roi = numeric_roi(words, CANDIDATES[0])
                    if clock_roi is None:
                        raise RuntimeError('게임시계 초 숫자의 ROI를 확정하지 못했습니다.')
                    observations['clock'] = dict(roi=clock_roi, bounds=CANDIDATES[0], tracker=None,
                                                 detector=RoiChangeDetector(self.config), pending=False, error='')
                    for slot in selected_slots:
                        name, remaining = str(slot.get('boss_name')), slot.get('remaining_seconds')
                        mode = ('excluded_day' if isinstance(remaining,int) and remaining>=86400 else
                                'minute' if isinstance(remaining,int) and remaining>=3600 else 'second')
                        self.log(f'boss_ocr name={name} remaining={remaining} mode={mode} state={slot.get("state")} severity={slot.get("severity")}')
                        if mode == 'excluded_day' or slot.get('state') != 'TIMED' or not isinstance(remaining,int) or remaining<=0 or slot.get('severity') == 'error':
                            continue
                        rect = next((r for r in self.slots.get(result.get('area'),[]) if r['slot_index']==slot.get('slot_index')), None)
                        if rect is None:
                            continue
                        bounds = timer_bounds(rect)
                        roi = numeric_roi(words, bounds, '분' if mode=='minute' else '초')
                        container = next((r for r in self.candidates if roi and r.contains(roi)), None)
                        if not roi or container is None or remaining % (60 if mode=='minute' else 1):
                            self.log(f'boss_excluded name={name} reason=numeric_roi_or_units_unresolved')
                            continue
                        observations[name] = dict(roi=roi, bounds=bounds, tracker=BossTimeTracker(name,remaining,mode,self.config),
                                                  detector=StableRoiChangeDetector(self.config), pending=False, error='')
                        self.log(f'roi name={name} numeric={roi} timer={bounds}')
                    if len(observations) == 1:
                        break
                    # Replay only tiny T0-time ROI records, including changes
                    # while OCR1 was busy.  No initial change is discarded.
                    for key, obs in observations.items():
                        rect = next(r for r in self.candidates if r.contains(obs['roi']))
                        for sample in buffers[rect].samples:
                            self.feed(key,obs,Sample(sample.start,sample.end,sample.pixels.crop(obs['roi'])),clock,
                                      sample if sample.pixels.rect.contains(obs['bounds']) else None)
                    buffers.clear()
            else:
                for key, obs in observations.items():
                    if obs['pending'] or obs['error']:
                        continue
                    # Bosses only need numeric pixels. The game clock retains
                    # its existing one-time OCR anchor verification.
                    sample = self.measured_grab(capture,obs['roi'])
                    self.feed(key,obs,sample,clock,None,capture)
            while True:
                try:
                    key, ok, text = self.verify_results.get_nowait()
                except queue.Empty:
                    break
                verified[key] = ok
                if not ok:
                    observations[key]['error'] = 'game_clock_ocr_verification_failed' if key=='clock' else 'verification_failed'
                self.log(f'verify key={key} ok={ok} text={text!r} completed_at={time.perf_counter():.6f}')
            targets = [key for key in observations if key!='clock']
            if result is not None:
                active_regions = tuple(obs['roi'] for obs in observations.values()
                                       if not obs['pending'] and not obs['error'])
                if active_regions != visible_regions:
                    self.events.put(('regions', list(active_regions)))
                    visible_regions = active_regions
            completed = sum(key in verified or bool(observations[key]['error']) for key in targets)
            remaining_count = len(targets)-completed
            self.events.put(('progress', (time.perf_counter()-self.t0,total,remaining_count,
                                         total-remaining_count if total is not None else 0)))
            if targets and completed==len(targets) and ('clock' in verified or observations['clock']['error']):
                break
            next_at += self.config.interval
            if next_at < time.perf_counter():
                next_at = time.perf_counter()  # no catch-up burst or invented timestamps
        if self.cancel.is_set():
            self.events.put(('cancelled', None))
            return
        precise = []
        if clock and verified.get('clock'):
            for key, obs in observations.items():
                if key != 'clock' and verified.get(key) and not obs['error']:
                    measured = precision_result(obs['tracker'],clock)
                    measured['verification_method'] = 'initial_ocr_and_stable_roi'
                    precise.append(measured)
                    self.log(f'final {measured}')
        for key, obs in observations.items():
            if key!='clock' and not any(p['boss_name']==key for p in precise):
                self.log(f'unconfirmed name={key} reason={obs["error"] or "timeout_or_clock_verification"}')
        report = dict(t0=self.t0, ended_at=time.perf_counter(), config=asdict(self.config),
                      total=total, completed=total, excluded=(total-len(precise)) if total is not None else 0,
                      max_capture_interval=self.max_interval, max_jitter=self.max_jitter, results=precise,
                      game_clock_initial=clock.initial.isoformat() if clock else None,
                      game_clock_ticks=[asdict(t) for t in clock.ticks] if clock else [],
                      clock_verified=bool(verified.get('clock')), trace=list(self.trace),
                      milestones=list(self.milestones), capture_stats=self.capture_stats)
        self.events.put(('progress',(time.perf_counter()-self.t0,total,0,total or 0)))
        self.events.put(('done',(result,report)))

    def feed(self,key,obs,sample,clock,buffered,capture=None):
        if obs['pending'] or obs['error']:
            return
        try:
            tick = obs['detector'].feed(sample)
            if tick is None:
                return
            self.log(f'tick key={key} lower={tick.lower:.6f} upper={tick.upper:.6f} midpoint={tick.at:.6f}')
            if key == 'clock':
                clock.ticks.append(tick)
                anchors = clock.anchors()
                if max(anchors)-min(anchors)>self.config.candidate_spread:
                    raise ValueError('inconsistent game clock ticks')
                enough = len(clock.ticks)>=self.config.second_ticks
                expected_time = clock.initial+timedelta(seconds=len(clock.ticks))
                expected = expected_time.hour*3600+expected_time.minute*60+expected_time.second
            else:
                tracker = obs['tracker']
                candidate = tracker.add(tick)
                self.log(f'candidate name={key} monotonic={candidate:.6f}')
                if tracker.error:
                    raise ValueError(tracker.error)
                if tracker.enough:
                    # The stable detector already confirmed the pixel change.
                    # Trust T0 OCR; never discard a valid boundary because a
                    # second OCR misreads the changed digit/unit (e.g. '4=').
                    obs['pending'] = True
                    self.verify_results.put((key,True,'initial_ocr_and_stable_roi'))
                    self.log(f'tracking_done name={key} at={sample.at:.6f} '
                             f'method=stable_roi tick_at={tick.at:.6f} boss_verify_ocr=0')
                return
            if enough:
                if buffered is not None:
                    verification = Sample(buffered.start,buffered.end,buffered.pixels.crop(obs['bounds']))
                elif capture is not None:
                    verification = capture.grab(obs['bounds'])
                    if verification.end - sample.start > 0.15:
                        raise ValueError('verification capture too late')
                else:
                    raise ValueError('initial tick verification crop unavailable')
                self.verify_jobs.put((key,verification,expected,key=='clock'))
                obs['pending'] = True
                self.log(f'tracking_done name={key} at={time.perf_counter():.6f} verification_pending=1')
        except ValueError as exc:
            obs['error'] = str(exc)
            self.log(f'tracking_done name={key} reason={exc} at={time.perf_counter():.6f}')

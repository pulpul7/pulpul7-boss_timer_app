"""1600x900 precision-only layout. OCR1/OCR2 geometry is not modified.

Edit column/row coordinates here when Odin's UI changes. Chapter occupancy
follows RECORD_BOOK_BOSS_ORDER, not arbitrary user-created schedule bosses.
"""
from precision_time_tracker import Rect

GAME_CLOCK_BOUNDS = Rect(190, 256, 420, 306)  # left +12; top/bottom inset 10
BOSS_LEFT_TRIM = 20
TIMER_COLUMNS = {
    3: ((418, 664), (670, 916), (922, 1168)),
    4: ((420, 658), (665, 903), (911, 1149), (1156, 1394)),
}
TIMER_ROWS = ((460, 489), (672, 701))
CHAPTER_BOSS_COUNTS = {
    '요툰하임': 8, '니다벨리르': 8, '알브하임': 6, '무스펠하임': 7,
    '아스가르드': 6, '니플하임': 5, '바나하임': 5, '던전': 6,
}


def timer_bounds(slot):
    inset = max(18, int((slot['timer_right']-slot['timer_left'])*0.10))
    return Rect(slot['timer_left']+inset+BOSS_LEFT_TRIM, slot['timer_top']+2,
                slot['timer_right']-inset, slot['timer_bottom']+3)


def chapter_slots(boss_orders=None):
    counts = dict(CHAPTER_BOSS_COUNTS)
    if boss_orders is not None:
        counts.update({area: len(names) for area,names in boss_orders.items()})
    result = {}
    for area, count in counts.items():
        if not 0 <= count <= 8:
            raise ValueError(f'{area}: {count}개 보스는 8칸 범위를 초과합니다. 정밀 캡처 좌표를 갱신해주세요.')
        top = (count+1)//2
        columns = TIMER_COLUMNS[4 if count>6 else 3]
        slots = []
        for row, size in enumerate((top, count//2)):
            for column in range(size):
                left,right = columns[column]
                y1,y2 = TIMER_ROWS[row]
                slots.append(dict(slot_index=len(slots)+1,timer_left=left,timer_right=right,
                                  timer_top=y1,timer_bottom=y2))
        result[area] = slots
    return result


CHAPTER_OBSERVATION_RECTS = {
    area: tuple(timer_bounds(slot) for slot in slots)
    for area,slots in chapter_slots().items()
}
# Before OCR identifies the chapter, retain all candidate timer positions.
# Build the two bands from the same constants used for per-boss preview.
CANDIDATES = (GAME_CLOCK_BOUNDS,) + tuple(
    Rect(min(r.left for r in row),min(r.top for r in row),
         max(r.right for r in row),max(r.bottom for r in row))
    for row in (
        [r for slots in CHAPTER_OBSERVATION_RECTS.values() for r in slots if r.top<600],
        [r for slots in CHAPTER_OBSERVATION_RECTS.values() for r in slots if r.top>=600],
    )
)

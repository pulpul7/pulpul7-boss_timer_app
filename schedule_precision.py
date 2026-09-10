"""Lossless clock text helpers; display rounding must never mutate a datetime."""
import re

FRACTIONAL_INPUT = re.compile(
    r'(?P<prefix>(?:\d+\s*일\s+)?)(?P<clock>\d{6}|\d{1,2}:\d{2}:\d{2})'
    r'\.(?P<fraction>\d{1,6})(?P<suffix>\s+.+)')


def split_fractional_input(text):
    match = FRACTIONAL_INPUT.fullmatch(text)
    if not match:
        return None
    return (match['prefix']+match['clock']+match['suffix'],
            int(match['fraction'].ljust(6,'0')))


def clock_text(value, *, compact=False):
    text = value.strftime('%H%M%S' if compact else '%H:%M:%S')
    if value.microsecond:
        # At least centiseconds, but never throw away finer captured data when
        # producing editable/round-trippable text.
        text += '.'+f'{value.microsecond:06d}'.rstrip('0').ljust(2,'0')
    return text


def normalize_capture_rate(value):
    try:
        return min(12,max(2,int(value)))
    except (TypeError,ValueError):
        return 2

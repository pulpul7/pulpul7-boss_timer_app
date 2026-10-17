"""Persistent tie order shared by schedule input, normalization and output."""
from datetime import datetime


def input_order(item):
    value = item.get('input_order')
    return value if type(value) is int and 0 < value <= 2**53 - 1 else 0


def event_sort_key(item):
    when = item.get('scheduled_at')
    return (when if isinstance(when, datetime) else datetime.max, input_order(item))


def row_sort_priority(kind):
    return {'fixed': 1, 'break': 9}.get(str(kind or '').strip().lower(), 0)


def assign_input_order(items, existing):
    """Copy parser rows; internal rebuilds carrying an order keep their identity."""
    last = max((input_order(item) for item in existing), default=0)
    last = max(last, max((input_order(item) for item in items), default=0))
    result = []
    for item in items:
        row = dict(item)
        if row.get('state') != 'control' and not input_order(row):
            last += 1
            if last > 2**53 - 1:
                raise ValueError('스케줄 입력 순서 범위를 초과했습니다.')
            row['input_order'] = last
        result.append(row)
    return result

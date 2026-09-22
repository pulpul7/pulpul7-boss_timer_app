"""Only editable boss times/names, never image-copy decoration rows."""
from datetime import datetime
import re
from schedule_precision import clock_text


def render_share_text(rows, reference):
    lines = []
    for row in rows:
        kind = row.get('kind', 'event')
        if kind != 'event' or row.get('is_fixed'):
            continue
        when = row.get('scheduled_at')
        if not isinstance(when, datetime):
            continue
        offset = (when.date()-reference.date()).days
        if offset < 0:
            continue
        # Keep minute-only sources uncertain; fractional originals never pass
        # through the image-copy HH:MM:SS display field.
        input_clock = str(row.get('input_clock_text') or clock_text(when))
        name = str(row.get('boss_text') or '').strip()
        if name:
            # TXT includes every occurrence for editing, in the supplied order.
            # Shared display names already include this prefix. Normalize at
            # the text boundary so both bare names and decorated rows round-trip.
            is_invasion = bool(row.get('is_invasion')) or name.startswith('침공')
            if is_invasion:
                name = re.sub(r'^(?:침공[\s_:：-]*)+', '', name).strip()
            invasion = '침공 ' if is_invasion else ''
            lines.append(f'{input_clock} {invasion}{name}')
    return '\n'.join(lines)

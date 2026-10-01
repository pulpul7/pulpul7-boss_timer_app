"""Match confirmed one-minute audio completion to the current schedule only."""
from datetime import timedelta
from .notice_management import parse_time
from .notice_schedule import CHAPTERS
from .notice_participation import important


def completed_opportunities(receipts, snapshot, now):
    if not snapshot:
        return []
    result = []
    for receipt in receipts[-32:]:
        try:
            at = parse_time(receipt.get('at'))
            target = parse_time(receipt.get('scheduled_at'))
            created = parse_time(receipt.get('created_at'))
            phase = receipt.get('phase')
            if (not at or not target or not created or created > at
                    or not timedelta(0) <= now - at <= timedelta(seconds=120)
                    or phase not in {'PRE_ALERT', 'PRE_ALERT_SEQUENCE', 'FIXED_PRE_ALERT', 'FIXED_PRE_ALERT_SEQUENCE'}):
                continue
            fixed = phase.startswith('FIXED_')
            for row in snapshot.get('events', []):
                name = str(row.get('boss_name') or '')
                if (not name or parse_time(row.get('scheduled_at')) != target
                        or bool(row.get('fixed')) != fixed or row.get('cut_applied')
                        or row.get('state') in {'deleted', 'elapsed', 'control'}
                        or row.get('is_invasion')):
                    continue
                # Do not infer which of simultaneous bosses was announced.
                # Display aliases are accepted only as supplied by the host.
                names = {name, str(row.get('display_name') or name)}
                if not any(n and n in receipt.get('text', '') for n in names):
                    continue
                result.append(dict(id=str(receipt['id']), server_id=snapshot['server_id'],
                    at=at.isoformat(), scheduled_at=target.isoformat(), phase='one_minute_complete',
                    kind='fixed' if fixed else 'boss', boss_name=name, chapter=CHAPTERS.get(row.get('area')),
                    major=important(row), star=bool(row.get('star')), absolute=bool(row.get('absolute')),
                    fixed_kind='world_boss' if '월드보스' in name.replace(' ', '') else
                               'valhalla' if '발할라' in name else ''))
        except (ValueError, TypeError, KeyError):
            continue
    return result

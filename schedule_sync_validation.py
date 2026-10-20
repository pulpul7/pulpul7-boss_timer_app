"""Read-only guards for normal sync; explicit history restore is separate."""
from datetime import datetime


def download_problem(raw, *, label, index_version, local_version, index_hash,
                     content_hash, compare_versions):
    actual = str(raw.get('dataVersion') or '').strip() if isinstance(raw, dict) else ''
    if index_version and actual != index_version:
        return (f'서버 목록과 {label} 파일의 버전이 다릅니다. '
                f'목록={index_version}, 파일={actual or "버전 없음"}. 기존 자료를 유지합니다.')
    if local_version and actual and compare_versions(actual, local_version) < 0:
        return f'{label} 서버 파일이 이 PC보다 구버전입니다. 동기화하지 않고 현재 자료를 유지합니다.'
    if index_hash and content_hash(raw) != index_hash:
        return f'서버 목록과 {label} 파일의 내용이 일치하지 않습니다. 기존 자료를 유지합니다.'
    return ''


def schedule_content_problem(incoming, current, now):
    def events(payload):
        return [item for key in ('schedule_events', 'schedule_active_entries', 'schedule_control_events')
                for item in (payload.get(key) if isinstance(payload.get(key), list) else [])
                if isinstance(item, dict)]

    def has_future(items):
        for item in items:
            value = item.get('scheduled_at')
            if isinstance(value, str):
                try:
                    value = datetime.fromisoformat(value)
                except ValueError:
                    continue
            if isinstance(value, datetime):
                local = value.astimezone().replace(tzinfo=None) if value.tzinfo else value
                reference = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
                if local >= reference:
                    return True
        return False

    downloaded = events(incoming)
    if not downloaded:
        return '서버 스케쥴이 비어 있어 적용하지 않았습니다. 현재 자료를 유지합니다.'
    if has_future(events(current)) and not has_future(downloaded):
        return '서버 스케쥴에는 지난 일정만 있습니다. 앞으로의 일정이 있는 현재 자료를 유지합니다.'
    return ''

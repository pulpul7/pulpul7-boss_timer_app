"""Resolve archive paths without allowing management to escape its data root."""
from pathlib import Path
import json


def season_directory(root, label):
    root = Path(root).resolve()
    if not label or Path(label).name != label or label in {'.', '..'}:
        raise ValueError('올바른 시즌 폴더명이 아닙니다.')
    candidate = root / label
    resolved = candidate.resolve()
    if resolved.parent != root or candidate.is_symlink() or (
            hasattr(candidate, 'is_junction') and candidate.is_junction()):
        raise ValueError('보관 폴더 밖의 경로는 관리할 수 없습니다.')
    return str(resolved)


def relocate_legacy_log_path(value, app_root, data_root):
    """Remap only known legacy roots; preserve intentionally chosen folders."""
    if not value:
        return value
    path = Path(value).resolve()
    origins = [Path(app_root)]
    try:
        marker = json.loads((Path(data_root) / 'portable_data_migration_v1.json').read_text(encoding='utf-8'))
        if isinstance(marker, dict) and marker.get('source') and Path(marker['source']).is_absolute():
            origins.append(Path(marker['source']))
    except (OSError, ValueError, TypeError):
        pass
    for origin in origins:
        for folder in ('archive_logs', 'season_prestart_logs'):
            old = (origin / folder).resolve()
            if path.is_relative_to(old):
                return str(Path(data_root) / folder / path.relative_to(old))
    return value

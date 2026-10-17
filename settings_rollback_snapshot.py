"""Refresh the active profile's rollback baseline without changing live settings."""
import configparser
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import uuid

from runtime_storage import temporary_data_directory
from web_service_common import file_transaction


def refresh_baseline(baseline, groups, settings_path, key_filter, *, server_id, season_key, notice=None):
    baseline = Path(baseline)
    root = baseline.parent.resolve()
    if baseline.is_symlink() or baseline.resolve().parent != root:
        raise ValueError('롤백 기준점 경로를 확인하세요.')
    root.mkdir(parents=True, exist_ok=True)
    with file_transaction(baseline / 'manifest.json'):
        inputs, snapshots = {}, {}
        manifest = dict(schema_version=1, server_id=str(server_id), season_key=str(season_key),
                        source_type='current_settings', refreshed_at=datetime.now().astimezone().isoformat(),
                        groups={}, config_snapshot='boss_timer_settings.ini')

        def read(path):
            path = Path(path)
            raw = path.read_bytes()
            inputs[path] = raw
            return raw

        for group, entries in groups.items():
            manifest['groups'][group] = []
            for source, relative in entries:
                relative = Path(relative)
                if relative.is_absolute() or '..' in relative.parts or relative in snapshots:
                    raise ValueError('롤백 파일 경로가 올바르지 않습니다.')
                snapshots[relative] = read(source)
                manifest['groups'][group].append(dict(source=str(source), snapshot=relative.as_posix(), exists=True))
        current = configparser.ConfigParser(interpolation=None)
        current.read_string(read(settings_path).decode('utf-8-sig'))
        saved = configparser.ConfigParser(interpolation=None)
        saved['settings'] = {key: value for key, value in current['settings'].items()
                             if any(key_filter(group, key) for group in groups)}
        output = StringIO()
        saved.write(output)
        snapshots[Path('boss_timer_settings.ini')] = output.getvalue().encode('utf-8')
        if notice is not None:
            from distribution_publish import public_payload
            snapshots[Path('notice_settings.json')] = public_payload('notice', notice, read(notice), runtime=True)
        snapshots[Path('manifest.json')] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
        backup = root / ('before_refresh_' + datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8])
        with temporary_data_directory(root, prefix='.baseline-refresh-') as temporary:
            stage = Path(temporary) / 'baseline'
            stage.mkdir()
            for relative, raw in snapshots.items():
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            if any(path.read_bytes() != raw for path, raw in inputs.items()):
                raise RuntimeError('저장 중 설정이 변경되었습니다. 다시 갱신해주세요.')
            existed = baseline.exists()
            if existed:
                baseline.rename(backup)
            try:
                stage.rename(baseline)
            except OSError:
                if existed:
                    backup.rename(baseline)
                raise
        return dict(groups=list(groups), backup=str(backup) if existed else '', baseline=str(baseline))

"""Tk-side reuse of detached public schedule data, separate from its heartbeat.

Only source values and semantic time boundaries invalidate the snapshot. Never
move the GUI collector or Tk variable reads to a worker thread. Keep this input
contract aligned with web_schedule_snapshot and the GUI share-row helpers.
"""
from datetime import date, datetime
import hashlib
import json

from web_service_common import timestamp


# Saved schedule/configuration inputs, not countdown text, view selection,
# polling state, or network/authority settings. No files are read for this key.
DATA_FIELDS = (
    'schedule_events', 'schedule_active_entries', 'schedule_control_events',
    'schedule_boss_definitions', 'schedule_area_definitions', 'fixed_boss_entries',
    'schedule_break_entries', 'schedule_boss_metrics', 'schedule_last_import_meta',
    'schedule_invasion_last_text_color', 'schedule_invasion_last_bg_color',
    'schedule_delete_active_cutoff_datetime', 'schedule_second_precision_offsets',
    'boss_name_lookup_map', 'boss_display_alias_map', 'record_book_catalog',
    'current_season_no', 'current_season_started_at', 'schedule_server_profile_id',
    'schedule_avg_cut_cache_revision', 'schedule_boss_metric_source_mode_default',
    'schedule_boss_metric_duration_enabled_default',
    'schedule_second_precision_expire_hours_default',
    'log_stats_exclude_extremes_default', 'log_stats_include_unconfirmed_default',
    'log_stats_enabled_saved_map',
    'schedule_alarm_voice_test_active',  # Also controls invasion visibility.
)
VAR_FIELDS = (
    'schedule_break_rows_enabled_var', 'fixed_boss_color_data_enabled_var',
    'schedule_boss_metric_duration_enabled_var',
    'schedule_maintenance_weekday_var', 'schedule_maintenance_hour_var',
    'schedule_maintenance_minute_var', 'schedule_invasion_weekday_var',
    'schedule_second_precision_expire_hours_var',
    'log_stats_exclude_extremes_var', 'log_stats_include_unconfirmed_var',
)


def _json_date(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError('unsupported snapshot input type')


def _var(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return value.get()


def source_key(app, *, boss_orders=None, slot_grid=None):
    now = app._get_schedule_reference_datetime()
    values = {name: getattr(app, name, None) for name in DATA_FIELDS}
    values.update({name: _var(getattr(app, name, None)) for name in VAR_FIELDS})
    values['log_boss_enabled'] = {name: _var(var) for name, var in
                                  getattr(app, 'log_stats_boss_enabled', {}).items()}
    values['fixed_days'] = {name: _var(var) for name, var in
                            getattr(app, 'fixed_boss_day_vars', {}).items()}
    season = str(getattr(app, 'current_season_no', '') or '')
    values['season_context'] = getattr(app, 'season_history_map', {}).get(season, {})
    values['server_name'] = app._get_schedule_share_server_name()
    values['boss_orders'], values['slot_grid'] = boss_orders, slot_grid
    values['day'] = now.date()
    values['utc_offset'] = str(now.astimezone().utcoffset())
    # Crossing a control event changes visibility even without editing its row.
    values['control_boundaries'] = [
        row['scheduled_at'] <= now for row in getattr(app, 'schedule_control_events', [])
        if isinstance(row, dict) and isinstance(row.get('scheduled_at'), datetime)
        and not row.get('historical_only')]
    for name in ('_get_schedule_visible_cutoff_datetime',
                 '_get_schedule_previous_maintenance_datetime',
                 '_get_schedule_maintenance_cutoff_datetime',
                 '_get_schedule_next_temporary_maintenance_datetime'):
        method = getattr(app, name, None)
        values[name] = (method() if name == '_get_schedule_visible_cutoff_datetime'
                        else method(now)) if callable(method) else None
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                     default=_json_date).encode('utf-8')
    return hashlib.sha256(raw).digest()


class WebSnapshotMemo:
    def __init__(self):
        self.key = None
        self.payload = None
        self.reused = False

    def capture(self, app, collector, *, boss_orders=None, slot_grid=None):
        try:
            key = source_key(app, boss_orders=boss_orders, slot_grid=slot_grid)
        except (TypeError, ValueError):
            # Unknown source types must never hide an edit behind a cached key.
            key = None
        self.reused = key is not None and key == self.key and self.payload is not None
        if not self.reused:
            payload = collector(app, boss_orders=boss_orders, slot_grid=slot_grid)
            self.payload, self.key = payload, key  # A failed build never advances the key.
        # New envelope, same immutable nested content: maintain liveness without
        # changing published revisions or mutating a previously offered envelope.
        return dict(self.payload, captured_at=timestamp())

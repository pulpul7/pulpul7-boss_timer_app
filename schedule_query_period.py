"""Shared end-date rule for Discord schedule queries and local schedule copies."""
from datetime import datetime, timedelta


def evening_extension_days(reference_datetime: datetime) -> int:
    return int(reference_datetime.hour >= 17)


def schedule_query_end(reference_datetime: datetime, extra_days: int = 0) -> datetime:
    if type(extra_days) is not int or not 0 <= extra_days <= 3:
        raise ValueError("schedule extension must be between zero and three days")
    return (reference_datetime + timedelta(days=1 + evening_extension_days(reference_datetime) + extra_days)).replace(
        hour=8, minute=0, second=0, microsecond=0)

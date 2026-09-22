"""Pure, server-local polling policy. Never changes the configured timetable."""
from datetime import timedelta

from .notice_management import collection_interval, parse_time, encode_time

EXTENSION_CHECK_MINUTES = 5
EXTENSION_CHECK_DURATION_MINUTES = 60


def polling_plan(state, now):
    now = parse_time(now)
    settings = state["settings"]
    interval = collection_interval(settings, now)
    normal = {"mode": "normal", "minutes": interval, "boundary": None,
              "message": f"설정 시간표 · {interval}분 간격" if interval else "설정 시간표상 자동 조회 쉬는 시간"}
    if interval is None or not settings["categories"].get("maintenance"):
        return normal
    # Only currently pinned, explicitly dated notices can suspend polling.
    # Ambiguity must increase observation, never create an inferred quiet period.
    candidates = []
    for article in state.get("articles", {}).values():
        title = article.get("title", "")
        published = article.get("published_date")
        if (article.get("pinned") and article.get("category") == "maintenance"
                and published and published <= now.date().isoformat()
                and any([word in title for word in ("연장", "완료", "취소")])):
            candidates.append(article)
    if not candidates:
        return normal
    latest_date = max(article["published_date"] for article in candidates)
    latest = [article for article in candidates if article["published_date"] == latest_date]
    if any(["완료" in article["title"] or "취소" in article["title"] for article in latest]):
        return dict(normal, message="점검 완료/취소 공지 확인 · " + normal["message"])
    ends = set()
    for article in latest:
        windows = article.get("analysis", {}).get("windows", [])
        if article.get("body_error") or len(windows) != 1:
            return dict(normal, message="연장 기간 재확인 필요 · " + normal["message"])
        fact = windows[0]
        start, end = parse_time(fact.get("start")), parse_time(fact.get("end"))
        if fact.get("kind") != "maintenance" or fact.get("issue") or not start or not end or end <= start:
            return dict(normal, message="연장 기간 미확정 · " + normal["message"])
        if now < start:
            return normal
        ends.add(end)
    if len(ends) != 1:
        return dict(normal, message="연장 공지의 종료 시각이 서로 다름 · " + normal["message"])
    end = ends.pop()
    if now < end:
        return {"mode": "extension_wait", "minutes": None, "boundary": encode_time(end),
                "message": f"점검 연장 · {end:%Y-%m-%d %H:%M}까지 자동 조회 대기 (수동 조회 가능)"}
    until = end + timedelta(minutes=EXTENSION_CHECK_DURATION_MINUTES)
    if now < until:
        return {"mode": "extension_followup", "minutes": EXTENSION_CHECK_MINUTES,
                "boundary": encode_time(end),
                "message": f"재연장 확인 · {until:%Y-%m-%d %H:%M}까지 {EXTENSION_CHECK_MINUTES}분 간격"}
    return normal

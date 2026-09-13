"""Serial, bounded, read-only collection. This module never enqueues speech."""
import threading

from .cafe_source import LIST_URL, fetch_page, parse_pinned, parse_article


class NoticeCollector:
    def __init__(self, fetch=fetch_page, *, stop_event=None):
        self.fetch = fetch
        self.stop_event = stop_event or threading.Event()

    def collect_once(self, store, *, manual=False):
        if self.stop_event.is_set():
            return False
        claimed = store.claim_collection(manual=manual)
        if claimed is None:
            return False
        token, state = claimed
        try:
            page = self.fetch(LIST_URL)
            listing = parse_pinned(page.text, store.clock())
            fetched, errors = {}, {}
            for row in listing:
                if self.stop_event.is_set():
                    return store.finish_collection(token, listing, fetched, errors, cancelled=True)
                if row["category"] is None or not state["settings"]["categories"].get(row["category"], False):
                    continue
                old = state.get("articles", {}).get(row["id"], {})
                try:
                    cached = old if old.get("content_hash") and old.get("title") == row["title"] else None
                    page = self.fetch(row["url"], cached)
                    if page.not_modified:
                        if not old.get("content_hash"):
                            raise ValueError("304 응답에 대응하는 기존 본문이 없습니다.")
                        body = {key: old[key] for key in ("title", "body", "published_date", "images", "content_hash", "review_note", "etag", "modified")}
                    else:
                        body = parse_article(page.text, row, store.clock())
                        body.update(etag=page.etag, modified=page.modified)
                    fetched[row["id"]] = body
                except Exception as exc:
                    errors[row["id"]] = str(exc)[:1000]
            return store.finish_collection(token, listing, fetched, errors, cancelled=self.stop_event.is_set())
        except Exception as exc:
            store.fail_collection(token, str(exc))
            return False

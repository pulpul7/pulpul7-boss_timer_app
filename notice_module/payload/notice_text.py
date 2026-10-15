"""Shared normalization for public notice text, never its stored original."""
import re
import unicodedata


def normalized(value):
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[\u200b\u200c\u200d\u2060\ufeff]", "", value)


def compact(value):
    return re.sub(r"\s+", "", normalized(value))


def maintenance_extended(article):
    """Recognize actual extensions, including edits under an unchanged title.

    The common '연장될 수 있습니다' disclaimer is never an extension signal.
    Completion/cancellation titles take precedence over historic body text.
    """
    title = compact(article.get("title", ""))
    if any(word in title for word in ("완료", "취소")):
        return False
    if "연장" in title or article.get("maintenance_extension") is True:
        return True
    body = compact(article.get("body", ""))
    return bool(re.search(r"(?:\d+(?:시간|분))+(?:추가|더)?연장(?!될수|가능)|연장(?:점검|안내|공지|되었습니다|됩니다|합니다)", body))


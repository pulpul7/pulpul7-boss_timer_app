"""Presentation labels only. Never feed these labels back into boss identity."""
import re


def strip_last_hit_marker(value):
    """Ignore the reserved view marker at input/lookup boundaries."""
    return re.sub(r"\s*\(\s*막\s*\)", "", str(value or "")).strip()


def boss_label(name, last_hit=False):
    text = str(name or "")
    return text + " (막)" if text and last_hit and not text.endswith(" (막)") else text


def row_label(row, key="name"):
    return boss_label(row.get(key), row.get("last_hit") is True)


def notice_label(message, definitions):
    """Decorate complete boss names in visible notices, never voice payloads.

    Include unmarked names in the matcher so a short alias cannot decorate part
    of a different, longer boss name.
    """
    names = {str(name): bool(item.get("last_hit")) for name, item in definitions.items() if name}
    if not any(names.values()):
        return str(message or "")
    pattern = r"(?<![\w])(?:" + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)) + r")(?=\s|$|[^\w]|(?:이|가|은|는|을|를|의)(?:\s|$))"
    return re.sub(pattern, lambda match: boss_label(match[0], names[match[0]])
                  if not match.string[match.end():].startswith(" (막)") else match[0], str(message or ""))

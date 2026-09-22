"""Expand a shared clock into individual boss lines without losing precision."""
import re


def expand_schedule_boss_line(line: str) -> list[str]:
    match = re.fullmatch(
        r"\s*(?P<clock>(?:\d+\s*일\s+)?(?:\d{6}|\d{4}|\d+:\d{2}(?::\d{2})?)(?:\.\d{1,6})?)\s+(?P<names>.+?)\s*",
        line,
    )
    if match is None or not re.search(r"[,，]", match["names"]):
        return [line]
    names = match["names"]
    suffix = ""
    if names.endswith(" 컷"):
        names = names[:-2].rstrip()
        suffix = " 컷"
    # Preserve duplicates so the input UI can warn about them.
    return [f"{match['clock']} {name.strip()}{suffix}" for name in re.split(r"[,，]", names) if name.strip()]

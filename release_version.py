"""Resolve the host release from a reachable program tag, never a module tag."""
from pathlib import Path
import os
import re
import subprocess


PROGRAM_VERSION = re.compile(r"v\d+\.\d+\.\d+(?:[.-][A-Za-z0-9]+)*\Z")


def resolve_release_version(fallback: str, root: Path) -> str:
    # Explicit packaging version takes priority without creating/moving Git tags.
    override = os.environ.get("BOSS_TIMER_BUILD_VERSION")
    if override is not None:
        if not PROGRAM_VERSION.fullmatch(override):
            raise ValueError("BOSS_TIMER_BUILD_VERSION must be a version such as v6.3.1")
        return override
    root = Path(root)
    if not (root / ".git").exists():
        return fallback
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*.[0-9]*.[0-9]*"],
            cwd=root, capture_output=True, text=True, encoding="utf-8", check=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return fallback
    version = result.stdout.strip()
    return version if PROGRAM_VERSION.fullmatch(version) else fallback

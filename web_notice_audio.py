"""Allowlisted bundled notice recordings; personal audio stays in the browser."""
from functools import lru_cache
import hashlib
from pathlib import Path
import sys

SOUNDS = {
    '/sounds/air-raid.mp3': ('air-raid.mp3', '002a6f0185c06e9698d91e3a8a9b2cff69911c54e9ab5ded678b21074ebc153a'),
    '/sounds/nuclear.mp3': ('nuclear.mp3', '8d7be0e06bb3bb3b3a236c93e0b45ddb1b2e1d94082c3159c329d0bf31c149e9'),
}


def bundle_folder():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'web_assets' / 'notice_sounds'


@lru_cache(maxsize=2)
def _checked(path, modified, size, expected):
    file = Path(path)
    if file.is_symlink() or not 0 < size <= 256 * 1024:
        raise ValueError('invalid notice sound')
    raw = file.read_bytes()
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('notice sound hash mismatch')
    return raw


def sound_asset(path):
    entry = SOUNDS.get(path)
    if entry is None:
        return None
    file = bundle_folder() / entry[0]
    stat = file.stat()
    return _checked(str(file), stat.st_mtime_ns, stat.st_size, entry[1]), 'audio/mpeg'

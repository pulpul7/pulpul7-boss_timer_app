"""One immutable, authenticated local sprite atlas for the limited spring theme."""
from functools import lru_cache
import hashlib
from pathlib import Path
import sys

ASSET_PATH = '/themes/limited-sakura-atlas-v1.png'
ATLAS_SHA256 = '00341d7141cc4c82eb41f598b2b60c9166b1af1e5d25f698fce6ce185eda0338'


def bundle_folder():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'web_assets' / 'limited_sakura'


@lru_cache(maxsize=1)
def _checked(path, modified, size):
    file = Path(path)
    if file.is_symlink() or not 0 < size <= 4 * 1024 * 1024:
        raise ValueError('invalid blossom atlas')
    raw = file.read_bytes()
    if len(raw) != size or not raw.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(raw).hexdigest() != ATLAS_SHA256:
        raise ValueError('blossom atlas integrity mismatch')
    return raw


def theme_asset(path):
    if path != ASSET_PATH:
        return None
    file = bundle_folder() / 'botanical-atlas-v1.png'
    stat = file.stat()
    return _checked(str(file), stat.st_mtime_ns, stat.st_size), 'image/png'

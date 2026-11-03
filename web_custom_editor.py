"""Small shared editor companion; no settings, network or schedule mutations."""
from functools import lru_cache
from pathlib import Path
import sys


@lru_cache(maxsize=1)
def editor_asset():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    raw = (root / 'web_assets' / 'custom-editor.js').read_bytes()
    if not 0 < len(raw) <= 65536:
        raise ValueError('invalid custom editor script')
    raw.decode('utf-8')
    return raw, 'text/javascript; charset=utf-8'

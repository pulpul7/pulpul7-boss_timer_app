"""Read the policy shipped with this program, never an older AppData copy."""
from pathlib import Path
import sys


def policy_text():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return (root / 'UPDATE_POLICY.md').read_text(encoding='utf-8-sig')

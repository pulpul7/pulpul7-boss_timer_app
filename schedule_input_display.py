"""Text tags hide precision visually; the Text buffer is always lossless."""
import re

FRACTION_PATTERN = re.compile(
    r'(?m)^\s*(?:\d+일\s+)?(?:\d{1,2}:\d{2}:\d{2}|\d{6})(\.\d{1,6})(?=\s|$)')
FRACTION_TAG = 'schedule_hidden_fraction'


def apply_fraction_visibility(widget, hidden=True):
    raw = widget.get('1.0', 'end-1c')
    widget.tag_configure(FRACTION_TAG, elide=bool(hidden))
    # Re-tag even identical content: OCR may delete/reinsert the same string,
    # which removes old ranges without changing the raw text.
    widget.tag_remove(FRACTION_TAG, '1.0', 'end')
    for match in FRACTION_PATTERN.finditer(raw):
        start, end = match.span(1)
        widget.tag_add(FRACTION_TAG, f'1.0+{start}c', f'1.0+{end}c')

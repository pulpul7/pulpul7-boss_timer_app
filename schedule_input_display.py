"""Text tags hide precision visually; the Text buffer is always lossless."""
import re

FRACTION_PATTERN = re.compile(
    r'(?m)^\s*(?:\d+일\s+)?(?:\d{1,2}:\d{2}:\d{2}|\d{6})(\.\d{1,6})(?=\s|$)')
FRACTION_TAG = 'schedule_hidden_fraction'
DUPLICATE_TAG_PREFIX = 'schedule_duplicate_boss_'
DUPLICATE_COLORS = (
    ('#fee2e2', '#991b1b'), ('#dbeafe', '#1e40af'),
    ('#fef3c7', '#92400e'), ('#ede9fe', '#5b21b6'),
    ('#cffafe', '#155e75'), ('#fce7f3', '#9d174d'),
)


def find_duplicate_boss_lines(raw_text, parse_line):
    """Inspect original lines before the batch parser collapses equal raw_keys."""
    groups = {}
    for line_no, line in enumerate(raw_text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        parsed = parse_line(line)
        if not parsed or parsed.get('state') == 'control':
            continue
        key = str(parsed.get('raw_key') or '').strip()
        if not key:
            continue
        group = groups.setdefault(key, {
            'name': str(parsed.get('display_name') or parsed.get('boss_name') or key),
            'lines': [],
        })
        group['lines'].append(line_no)
    return [group for group in groups.values() if len(group['lines']) > 1]


def clear_duplicate_highlights(widget):
    for tag in widget.tag_names():
        if str(tag).startswith(DUPLICATE_TAG_PREFIX):
            widget.tag_delete(tag)


def highlight_duplicate_boss_lines(widget, groups):
    clear_duplicate_highlights(widget)
    for index, group in enumerate(groups):
        tag = f'{DUPLICATE_TAG_PREFIX}{index}'
        background, foreground = DUPLICATE_COLORS[index % len(DUPLICATE_COLORS)]
        widget.tag_configure(tag, background=background, foreground=foreground)
        for line_no in group['lines']:
            widget.tag_add(tag, f'{line_no}.0', f'{line_no}.end')
        widget.tag_raise(tag)
    if groups:
        # Put the caret on the earliest repeated occurrence, not the first seed.
        target = f"{min(group['lines'][1] for group in groups)}.0"
        widget.tag_remove('sel', '1.0', 'end')
        widget.mark_set('insert', target)
        widget.see(target)
        widget.focus_set()


def apply_fraction_visibility(widget, hidden=True):
    raw = widget.get('1.0', 'end-1c')
    widget.tag_configure(FRACTION_TAG, elide=bool(hidden))
    # Re-tag even identical content: OCR may delete/reinsert the same string,
    # which removes old ranges without changing the raw text.
    widget.tag_remove(FRACTION_TAG, '1.0', 'end')
    for match in FRACTION_PATTERN.finditer(raw):
        start, end = match.span(1)
        widget.tag_add(FRACTION_TAG, f'1.0+{start}c', f'1.0+{end}c')

"""Embed local Endgame art and scoped HUD CSS in the existing stylesheet. No build."""
from pathlib import Path
from base64 import b64encode
from urllib.parse import quote


def embed(root):
    root = Path(root)
    folder = root / 'web_assets/avengers'
    images = {}
    for name, filename, suffix in [('portals', 'portals', 'jpg'), ('heroes', 'ironman', 'jpg')]:
        mime = 'image/webp' if suffix == 'webp' else 'image/jpeg'
        images[name + '-image'] = 'data:' + mime + ';base64,' + b64encode((folder / (filename + '.' + suffix)).read_bytes()).decode('ascii')
    for name in ('frame', 'gold-frame', 'reactor', 'emblem', 'outer-frame', 'card-frame', 'selected-frame'):
        images[name] = 'data:image/svg+xml,' + quote((folder / (name + '.svg')).read_text(encoding='utf-8'), safe='')
    images['major-image'] = 'data:image/jpeg;base64,' + b64encode((root / '보스이미지/아이언맨1.jpg').read_bytes()).decode('ascii')
    # Keep encoded bytes inert until JS creates CSP-approved blob URLs.
    declaration = 'html[data-theme="avengers"]{' + ''.join('--avengers-source-' + key + ':"' + value + '";' for key, value in images.items()) + '}\n'
    css = declaration + (root / 'web_avengers_theme.css').read_text(encoding='utf-8')
    path = root / 'web_assets/style.css'
    source = path.read_text(encoding='utf-8')
    begin, end = '/* BEGIN WEB AVENGERS THEME */', '/* END WEB AVENGERS THEME */'
    if begin in source:
        start, stop = source.index(begin), source.index(end) + len(end)
        source = source[:start] + begin + '\n' + css + end + source[stop:]
    else:
        source = source.rstrip() + '\n\n' + begin + '\n' + css + end + '\n'
    if len(source.encode('utf-8')) > 1024 * 1024:
        raise ValueError('style.css exceeds the existing 1 MiB asset limit')
    path.write_text(source, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    embed(Path(__file__).resolve().parent)

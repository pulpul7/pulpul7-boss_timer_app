"""Embed the readable blossom module compactly; does not build or run the app."""
from pathlib import Path
import re
import hashlib


def compact_module(source):
    """Whitespace/comment compaction for this semicolon-delimited, regex-free module.

    Strings and template literals stay byte-for-byte intact. Only explicitly
    listed module-local bindings are shortened, never DOM/API fields.
    Functional checks cover readable and embedded versions.
    """
    private = ('active loaded requested canvas ctx last spawnAt geometryDirty '
               'scanned targets byNode petals petalSequence '
               'scanDue attachments butterfly rests nodeSequence '
               'nodeIds wingURL warmFlowers makeTextures butterflyArt moveButterfly '
               'fixedFlower candidates horizontal before textures centers pixels '
               'triangle alpha squash finish points frames surface sprite texture '
               'schedule resize seed compact paintPerch index button context '
               'layoutKey warming warmed headMoment').split()
    alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
    names = {name: '$' + alphabet[i] for i, name in enumerate(private)}
    # Tokenize identifiers together so replacement cannot touch string contents.
    tokens = re.findall(
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`'
        r'|//[^\n]*|/\*[\s\S]*?\*/|\s+|[A-Za-z_$][\w$]*|.', source)
    output = []
    pending = False
    for token in tokens:
        if token.isspace() or token.startswith(('//', '/*')):
            pending = True
            continue
        token = names.get(token, token)
        if pending and output:
            before, after = output[-1][-1], token[0]
            word = lambda c: c.isalnum() or c in '_$'
            if (word(before) and word(after) or before == after and before in '+-'
                    or before == '/' and after in '/*' or word(before) and after == '.'):
                output.append(' ')
        output.append(token)
        pending = False
    return ''.join(output)


def compact_embedded_indent(source):
    """Trim code indentation in existing modules without changing literal data.

    Readable module files stay intact. A multiline template/continued string
    makes the whole block ineligible, rather than risking its visible content.
    """
    for name in ('FONT', 'NOTICE SOUND', 'BOSS SPECTRUM'):
        begin, end = '// BEGIN WEB ' + name + ' MODULE', '// END WEB ' + name + ' MODULE'
        if begin not in source or end not in source:
            continue
        a, b = source.index(begin) + len(begin), source.index(end)
        body = source[a:b]
        lines = body.splitlines(keepends=True)
        if any(line.rstrip('\r\n').endswith('\\') or line.count('`') % 2 for line in lines):
            continue
        source = source[:a] + ''.join(line.lstrip(' \t') for line in lines) + source[b:]
    return source


def embed(root):
    module = (root / 'web_limited_sakura.js').read_text(encoding='utf-8')
    script = root / 'web_assets' / 'schedule.js'
    source = script.read_text(encoding='utf-8')
    begin, end = '// BEGIN WEB LIMITED SAKURA MODULE', '// END WEB LIMITED SAKURA MODULE'
    a, b = source.index(begin) + len(begin), source.index(end)
    source = source[:a] + '\n' + compact_module(module) + '\n' + source[b:]
    source = compact_embedded_indent(source)
    if len(source.encode('utf-8')) > 1024 * 1024:
        raise ValueError('schedule.js exceeds the existing 1 MiB asset limit')
    script.write_text(source, encoding='utf-8', newline='\n')
    (root / 'web_schedule_frontend.py').write_text(
        '"""Bundled read-only schedule script, mirrored from web_assets."""\n\n'
        'SCRIPT = r"""' + source + '""".encode(\'utf-8\')\n', encoding='utf-8', newline='\n')
    html = (root / 'web_assets/index.html').read_text(encoding='utf-8')
    css = (root / 'web_assets/style.css').read_text(encoding='utf-8')
    (root / 'web_service_page.py').write_text(
        '"""Bundled read-only schedule page fallback, mirrored from web_assets."""\n'
        'from web_schedule_frontend import SCRIPT\n\nPAGE = r"""' + html +
        '""".encode(\'utf-8\')\n\nSTYLE = r"""' + css + '""".encode(\'utf-8\')\n',
        encoding='utf-8', newline='\n')
    butterfly = (root / 'web_assets/limited_sakura/butterfly.js').read_text(encoding='utf-8').encode('utf-8')
    server = root / 'web_limited_sakura.py'
    checked, count = re.subn(r"BUTTERFLY_SHA256 = '[0-9a-f]{64}'",
                             "BUTTERFLY_SHA256 = '" + hashlib.sha256(butterfly).hexdigest() + "'",
                             server.read_text(encoding='utf-8'))
    if count != 1:
        raise ValueError('missing butterfly integrity declaration')
    server.write_text(checked, encoding='utf-8', newline='\n')
    return source


if __name__ == '__main__':
    embed(Path(__file__).resolve().parent)

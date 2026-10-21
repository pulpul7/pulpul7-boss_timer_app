"""Embed readable web modules compactly; does not build or run the app."""
from pathlib import Path
import re
import hashlib


def compact_module(source, private=None, prefix='$'):
    """Whitespace/comment compaction for this semicolon-delimited, regex-free module.

    Strings and template literals stay byte-for-byte intact. Only explicitly
    listed module-local bindings are shortened, never DOM/API fields.
    Functional checks cover readable and embedded versions.
    """
    private = (private or 'active loaded requested canvas ctx last spawnAt geometryDirty '
               'scanned targets byNode petals petalSequence '
               'scanDue attachments butterfly rests nodeSequence '
               'nodeIds wingURL warmFlowers makeTextures butterflyArt moveButterfly '
               'fixedFlower candidates horizontal before textures centers pixels '
               'triangle alpha squash finish points frames surface sprite texture '
               'schedule resize seed compact paintPerch index button context '
               'layoutKey warming warmed headMoment').split()
    alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
    names = {name: prefix + alphabet[i] for i, name in enumerate(private)}
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
    """Trim indentation and standalone comments without changing literal data.

    Readable module files stay intact. A multiline template/continued string
    makes the whole block ineligible, rather than risking its visible content.
    """
    ranges = [('// BEGIN WEB ' + name + ' MODULE', '// END WEB ' + name + ' MODULE')
              for name in ('FONT', 'NOTICE SOUND', 'BOSS SPECTRUM', 'HIDDEN HELP', 'NOTICE')]
    ranges += [('function deviceProfile()', 'function clock('),
               ('function readCustomSelection(', 'function captureDefaultView()'),
               ('function captureDefaultView()', '// BEGIN WEB MENU IDLE'),
               ('function readCopyOptions()', 'function sharingEvents(')]
    for begin, end in ranges:
        if begin not in source or end not in source:
            continue
        a, b = source.index(begin) + len(begin), source.index(end)
        body = source[a:b]
        lines = body.splitlines(keepends=True)
        if any(line.rstrip('\r\n').endswith('\\') or line.count('`') % 2 for line in lines):
            continue
        source = source[:a] + ''.join(line.lstrip(' \t') for line in lines
                                    if not line.lstrip().startswith('//') or
                                    line.lstrip().startswith(('// BEGIN WEB ', '// END WEB '))) + source[b:]
    # These blocks use only whitespace-free regex literals; keep their public
    # names and string/template contents intact while reducing the bundle size.
    for name in ('NOTICE MODULE', 'NOTICE SOUND MODULE', 'NAVIGATION GUARD', 'PAGE REFRESH', 'SCHEDULE TXT'):
        begin, end = '// BEGIN WEB ' + name, '// END WEB ' + name
        if begin not in source or end not in source:
            continue
        a, b = source.index(begin) + len(begin), source.index(end)
        private = ('planEvents planPrefs testUntil testTimer windowMs loadSeen occurrence drawSettings'
                   if name == 'NOTICE MODULE' else 'acceptedAt interval shared wait message reload last'
                   if name == 'NAVIGATION GUARD' else
                   'pending navigating timer token version run next asset raw saved root key'
                   if name == 'PAGE REFRESH' else
                   'audioSupported resetBuffer openDb loadBuffer validateFile signalKey MAX_FILE MAX_TOTAL MAX_FILES'
                   if name == 'NOTICE SOUND MODULE' else
                   'timer generation current now groups eligible bosses last row rows deadline group end text box payload stamp madeAt width key'
                   if name == 'SCHEDULE TXT' else '__unused')
        source = source[:a] + '\n' + compact_module(source[a:b], private, prefix='$' if name == 'SCHEDULE TXT' else '$n') + '\n' + source[b:]
    for begin, end in (('function shareText(', '// BEGIN WEB COPY TEXT' if '// BEGIN WEB COPY TEXT' in source else 'async function copyText('),
                       ('function bossRank(', 'function updateConnection('),
                       ('function refreshHiddenMenuFold(', 'const winterSnow='),
                       ('function customDefaultSelected(', 'function readRestPreferences('),
                       ('function readCopyOptions()', 'function colorOf('),
                       ('function selectFilter(', 'async function loginExpired(')):
        if begin in source and end in source:
            a, b = source.index(begin), source.index(end)
            source = source[:a] + compact_module(source[a:b], '__unused') + '\n' + source[b:]
    # Only these regex-free or whitespace-free-regex sections are compacted.
    for begin, end in (('function themeChoices()', 'function renderSize()'),
                       ('function renderSize()', 'function note('),
                       ('function chooseSaved()', 'function family('),
                       ('async function loadFace(', 'async function refreshScope('),
                       ('function toast(', 'function legacyTxtIncluded('),
                       ('function restoreTabOrder()', 'function updateTabDrag('),
                       ('function localDate(', 'function contextTitle('),
                       ('function deviceProfile()', 'function clock('),
                       ('function fitHeader()', 'function restDuration('),
                       ('function tick()', 'function renderActive('),
                       ('function renderActive(', 'function applyMajorUpdates(')):
        a = source.find(begin)
        b = source.find(end, a + len(begin)) if a >= 0 else -1
        if b < 0:
            continue
        private = 'result raw valid choices pair personal old value data' if begin == 'function themeChoices()' else '__unused'
        source = source[:a] + compact_module(source[a:b], private, prefix='$') + '\n' + source[b:]
    return source


def embed(root):
    module = (root / 'web_limited_sakura.js').read_text(encoding='utf-8')
    script = root / 'web_assets' / 'schedule.js'
    source = script.read_text(encoding='utf-8')
    from web_theme_bootstrap import THEME_PREFERENCE
    begin, end = '// BEGIN WEB THEME PREFERENCE', '// END WEB THEME PREFERENCE'
    if begin in source:
        a, b = source.index(begin) + len(begin), source.index(end)
        source = source[:a] + '\n' + compact_module(THEME_PREFERENCE, '__unused') + '\n' + source[b:]
    begin, end = '// BEGIN WEB CURRENT TAB', '// END WEB CURRENT TAB'
    a, b = source.index(begin) + len(begin), source.index(end)
    source = source[:a] + '\n' + compact_module((root / 'web_current_tab.js').read_text(encoding='utf-8'), 'key saved value', prefix='$tab') + '\n' + source[b:]
    begin, end = '// BEGIN WEB COPY TEXT', '// END WEB COPY TEXT'
    a, b = source.index(begin) + len(begin), source.index(end)
    source = source[:a] + '\n' + compact_module((root / 'web_copy_text.js').read_text(encoding='utf-8'), 'period end now time gap area cooling event button', prefix='$') + '\n' + source[b:]
    begin, end = '// BEGIN WEB SCHEDULE TXT', '// END WEB SCHEDULE TXT'
    a, b = source.index(begin) + len(begin), source.index(end)
    source = source[:a] + '\n' + (root / 'web_schedule_txt.js').read_text(encoding='utf-8') + '\n' + source[b:]
    begin, end = '// BEGIN WEB FONT MODULE', '// END WEB FONT MODULE'
    a, b = source.index(begin) + len(begin), source.index(end)
    source = source[:a] + '\n' + (root / 'web_font_frontend.js').read_text(encoding='utf-8') + '\n' + source[b:]
    begin, end = '// BEGIN WEB LIMITED SAKURA MODULE', '// END WEB LIMITED SAKURA MODULE'
    a, b = source.index(begin) + len(begin), source.index(end)
    source = source[:a] + '\n' + compact_module(module) + '\n' + source[b:]
    menu = (root / 'web_menu_idle.js').read_text(encoding='utf-8')
    begin, end = '// BEGIN WEB MENU IDLE', '// END WEB MENU IDLE'
    a, b = source.index(begin) + len(begin), source.index(end)
    private = ('root timer pressed y mouseOutside wheelAt touch stop blocked check '
               'pointerActivity wheelInput touchInput event point dy next wheelHold pin '
               'position desktop input key hover phone edge delayInput delayKey delayValue '
               'delayOptions touchStop hero box stamp reset')
    source = source[:a] + '\n' + compact_module(menu, private, prefix='') + '\n' + source[b:]
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

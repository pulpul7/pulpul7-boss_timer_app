"""Small, hash-approved theme restore before CSS/first paint. No new HTTP requests."""
from __future__ import annotations

import base64
import hashlib
import re

SCRIPT = r"""(()=>{'use strict';
  try {
    const root=document.documentElement;
    const name=(localStorage.getItem('bt_nickname') || 'guest').replace(/[\u0000-\u001f\u007f]/g,'').trim().slice(0,40) || 'guest';
    const egg=name==='수비수'?'BossTimer.webEgg.defender.v1':'BossTimer.webEgg.theme.v1:'+name;
    const eligible=root.getAttribute('data-theme-master')==='true' || name==='나츠' ||
      localStorage.getItem('BossTimer.hiddenMenu.unlocked.v1:'+name)==='true' ||
      (['수비수','설기','백설기'].includes(name) && localStorage.getItem(egg)==='true');
    const value=localStorage.getItem('BossTimer.webTheme.v1:'+name);
    const solids=['sky','wood','green','blue','magenta','ivory','canary','forsythia','purple','gray'];
    const theme=eligible && ['sakura','spring','winter','rain',...solids].includes(value) &&
      (value!=='spring' || ['나츠','수비수','설기','백설기'].includes(name))?value:'default';
    if(eligible) root.setAttribute('data-theme-user','enabled');
    if(theme!=='default') {
      root.setAttribute('data-theme',theme);root.setAttribute('data-theme-pink','true');
      if(theme==='spring') root.setAttribute('data-theme-photo','true');
      if(['spring','winter','rain'].includes(theme)) root.setAttribute('data-theme-image','true');
      if(solids.includes(theme)) root.setAttribute('data-theme-solid','true');
    }
    const meta=document.querySelector('meta[name="theme-color"]');
    const colors={sky:'#f2faff',wood:'#faf5ed',green:'#f4faf1',blue:'#f1f5ff',magenta:'#fff1fb',ivory:'#fffdf3',canary:'#fffaf0',forsythia:'#fffbe8',purple:'#f7f2ff',gray:'#f5f6f7'};
    if(meta) meta.setAttribute('content',colors[theme] || (theme==='rain'?'#edf3ed':theme==='winter'?'#fff7e8':theme==='spring'?'#fff0f6':theme==='sakura'?'#fff4f9':'#151b24'));
  } catch(_) { /* Storage blocked: retain the normal default, never block entry. */ }
})();""".encode('utf-8')

SCRIPT_SOURCE = "'sha256-" + base64.b64encode(hashlib.sha256(SCRIPT).digest()).decode('ascii') + "'"


def render_theme_page(page, master=False):
    """Insert only our fixed script; private permission is supplied by the server."""
    root = re.search(rb'<html\b[^>]*>', page, flags=re.I)
    if root is None:
        return page
    tag = re.sub(rb'\sdata-theme-master\s*=\s*(?:"[^"]*"|\x27[^\x27]*\x27|[^\s>]+)', b'', root.group(), flags=re.I)
    tag = tag[:-1] + b' data-theme-master="' + (b'true' if master else b'false') + b'">'
    page = page[:root.start()] + tag + page[root.end():]
    stylesheet = re.search(rb'<link\b[^>]*\brel\s*=\s*["\x27]stylesheet["\x27][^>]*>', page, flags=re.I)
    if stylesheet is None:
        return page
    script = b'<script id="bt-theme-bootstrap">' + SCRIPT + b'</script>\n'
    return page[:stylesheet.start()] + script + page[stylesheet.start():]

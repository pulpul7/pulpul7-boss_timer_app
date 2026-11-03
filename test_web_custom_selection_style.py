"""Opt-in computed picker styles in isolated headless Chrome; no app/server."""
import html
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


CHROME = next((path for path in (
    Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
    Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
) if path.is_file()), None)


@unittest.skipUnless(CHROME and os.environ.get('BOSS_TIMER_BROWSER_STYLE_CHECK') == '1',
                     'set BOSS_TIMER_BROWSER_STYLE_CHECK=1 for isolated Chromium style checks')
class PickerSelectionStyleTests(unittest.TestCase):
    def test_existing_new_and_future_bosses_share_selection_and_release_styles(self):
        stylesheet = (Path(__file__).resolve().parent/'web_assets/style.css').as_uri()
        script = r"""
window.addEventListener('load',()=>{
const failures=[];let checks=0;
const check=(ok,label)=>{checks++;if(!ok)failures.push(label);};
const root=document.documentElement;
const solids=['sky','wood','green','blue','magenta','ivory','canary','forsythia','purple','gray'];
const themes=['default','sakura','spring','winter','rain','avengers',...solids];
const cases=[['existing spectrum',true],['existing plain',false],['월드던전 1차',false],['월드던전 2차',false],['future fixed boss',false],['future colored boss',true]];
for(const theme of themes){
 for(const attr of ['data-theme','data-theme-pink','data-theme-photo','data-theme-image','data-theme-solid'])root.removeAttribute(attr);
 if(theme!=='default')root.dataset.theme=theme;
 if(!['default','avengers'].includes(theme))root.dataset.themePink='true';
 if(theme==='spring')root.dataset.themePhoto='true';
 if(solids.includes(theme))root.dataset.themeSolid='true';
 for(const panel of ['selection','color'])for(const [name,spectrum] of cases){
  const host=document.getElementById('custom-'+panel+'-host');
  // Create each node after changing themes, as for a newly received catalog.
  const button=document.createElement('button');button.className='custom-choice'+(spectrum?' spectrum-choice':'');
  button.setAttribute('aria-pressed','false');button.dataset.choice=name;
  button.innerHTML='<span></span><small>주요보스</small><span class="custom-choice-star">✦</span>';
  button.firstElementChild.textContent=name;
  button.style.setProperty('--boss-spectrum-bg','#6049aa');
  button.style.setProperty('--boss-spectrum-fill','linear-gradient(100deg,#6049aa,#9271bf)');
  button.style.setProperty('--boss-spectrum-ink','#ffffff');host.append(button);
  const appearance=()=>{const style=getComputedStyle(button);return[style.backgroundImage,style.backgroundColor,style.color].join('|');};
  const before=appearance(),key=theme+'/'+panel+'/'+name;
  button.setAttribute('aria-pressed','true');
  const selected=getComputedStyle(button);
  check(selected.backgroundImage.includes('rgb(231, 255, 210)')&&selected.backgroundImage.includes('rgb(191, 238, 146)'),key+' green fill');
  check(selected.color==='rgb(37, 75, 22)',key+' selection ink');
  check(selected.borderTopColor==='rgb(118, 182, 80)',key+' selection border');
  check([...button.children].every(node=>getComputedStyle(node).color==='rgb(37, 75, 22)'),key+' label/star ink');
  button.setAttribute('aria-pressed','false');
  check(appearance()===before,key+' released theme/spectrum restored');
  button.remove();
 }
}
document.getElementById('result').textContent=JSON.stringify({checks,failures});
});
"""
        page = ('<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="'+stylesheet+'">'
                '<style>*{transition:none!important;animation:none!important}</style></head><body>'
                '<main class="schedule-app"><section id="custom-editor"><div id="custom-selection-host"></div>'
                '<div id="custom-color-host"></div></section></main><pre id="result">pending</pre><script>'+script+'</script></body></html>')
        with tempfile.TemporaryDirectory(prefix='web-picker-style-unit-', ignore_cleanup_errors=True) as folder:
            folder = Path(folder)
            fixture = folder/'fixture.html'
            fixture.write_text(page, encoding='utf-8')
            result = subprocess.run([str(CHROME),'--headless=new','--disable-gpu','--no-first-run',
                '--no-default-browser-check','--disable-background-networking','--disable-extensions',
                '--user-data-dir='+str(folder/'profile'),'--allow-file-access-from-files',
                '--dump-dom','--virtual-time-budget=1500',fixture.as_uri()],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        output = result.stdout.decode('utf-8',errors='replace')
        match = re.search(r'<pre id="result">(.*?)</pre>',output,re.S)
        self.assertIsNotNone(match,'Chrome did not produce a style report: '+result.stderr.decode('utf-8',errors='replace')[-1500:])
        report=json.loads(html.unescape(match[1]))
        self.assertEqual(report['checks'], 16*2*6*5)
        self.assertEqual(report['failures'], [],str(len(report['failures']))+' style failures: '+repr(report['failures'][:15]))


if __name__=='__main__':unittest.main()

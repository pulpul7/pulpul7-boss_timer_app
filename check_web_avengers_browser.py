"""Opt-in Chrome check with production CSP; loopback only, no app/user data.

Run this fixture server, then use check_web_avengers_browser.ps1 with an
isolated Chrome debugging port and the printed URL. No test deployment.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from web_asset_store import FILES, validate_assets
from web_https_app import HTTPSApplication
from web_theme_bootstrap import render_theme_page


def check():
    root = Path(__file__).resolve().parent
    files = {name: (root / 'web_assets' / name).read_bytes() for name in FILES}
    validate_assets(files)
    # Read the actual response policy, without accessing operational state.
    app = HTTPSApplication('fixture.invalid', 443, None, None, None, assets=files)
    headers = {}
    app({'REMOTE_ADDR': '127.0.0.1', 'HTTP_HOST': 'wrong.invalid'},
        lambda status, values: headers.update(values))
    policy = headers['Content-Security-Policy']
    assert "img-src 'self' blob:;" in policy and 'data:' not in policy
    source = files['schedule.js'].decode('utf-8')
    prepare = source[source.index('function prepareAvengersScene()'):source.index('function prepareSpringScene()')]
    script = """const violations=[];
document.addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective+':'+e.blockedURI));
const element=id=>document.getElementById(id);
""" + prepare + """
window.addEventListener('load',async()=>{
 try {
  prepareAvengersScene();
  const names=['portals-image','heroes-image','frame','gold-frame','reactor','emblem','outer-frame','card-frame','selected-frame','major-image'];
  const root=document.documentElement;
  const urls=names.map(n=>root.style.getPropertyValue('--avengers-'+n).match(/^url\\("(blob:[^"]+)"\\)$/)?.[1]);
  const loaded=await Promise.all(urls.map(async url=>{if(!url)return false;const img=new Image();img.src=url;await img.decode();return img.naturalWidth>0;}));
  const frame=new Image();frame.src=urls[names.indexOf('selected-frame')];await frame.decode();
  const canvas=document.createElement('canvas');canvas.width=canvas.height=40;
  const pen=canvas.getContext('2d');pen.drawImage(frame,0,0,40,40);
  const interiorClear=[[20,9],[20,11],[20,29],[20,31],[9,20],[11,20],[29,20],[31,20]].every(([x,y])=>pen.getImageData(x,y,1,1).data[3]===0);
  prepareAvengersScene();
  const same=names.every((n,i)=>root.style.getPropertyValue('--avengers-'+n).includes(urls[i]));
  await new Promise(r=>setTimeout(r,100));
  document.getElementById('result').textContent=JSON.stringify({loaded,same,violations,interiorClear,
   scene:element('avengers-scene').naturalWidth,
   frame:getComputedStyle(document.querySelector('.hero')).borderImageSource.startsWith('url("blob:'),
   button:getComputedStyle(element('copy-open')).borderImageSource.includes('blob:')});
 }catch(e){document.getElementById('result').textContent=JSON.stringify({error:String(e)});}
});"""
    page = b'''<!doctype html><html data-theme="avengers"><head><meta charset="utf-8">
<link rel="stylesheet" href="/style.css"><script src="/fixture.js"></script></head><body>
<div class="avengers-art"><img id="avengers-scene" alt=""></div>
<main class="schedule-app"><button id="copy-open">Copy</button><div class="hero">HUD</div></main>
<pre id="result">pending</pre></body></html>'''
    preview = render_theme_page(files['index.html'].replace(b'<head>', b'<head><script src="/preview-fixture.js"></script>', 1))
    preview_fixture = r"""(()=>{
 const previewFont15=new URLSearchParams(location.search).get('font')==='15';
 localStorage.setItem('bt_nickname','\uB098\uCE20');localStorage.setItem('BossTimer.webTheme.v1:\uB098\uCE20','avengers');
 localStorage.setItem('BossTimer.webFont.v1:\uB098\uCE20',previewFont15?'cafe24-dongdong':'system');
 const errors=[];window.addEventListener('error',e=>errors.push(e.message));window.addEventListener('securitypolicyviolation',e=>errors.push(e.violatedDirective+':'+e.blockedURI));
 const stamp=Date.now(),iso=n=>new Date(stamp+n*60000).toISOString();
 const names=['\uBBF8\uBBF8\uB974','\uBC1C\uD560\uB77C \uB300\uC804','\uD669\uAE08\uACE0\uBE14\uB9B0','\uD734\uC2DD','\uC2A4\uBC14\uB974\uD2B8','\uB2C8\uB4DC\uD638\uADF8','\uD5E4\uB974\uBAA8\uB4DC','\uBE44\uC694\uB978','\uC544\uB860','\uD30C\uB974\uBC14','\uB77C\uD0C0\uD1A0\uC2A4\uD06C','\uD398\uD2F0'];
 const minutes=[-30,-15,4,5,65,66,67,68,69,70,72,76];
 const events=names.map((name,i)=>({id:String(i).padStart(24,'0'),catalog_id:String(i).padStart(24,'0'),name,kind:i===3?'break':'boss',important:i===0||i===2,last_hit:i>3,scheduled_at:iso(minutes[i]),end_at:iso(minutes[i]+(i===3?60:1)),text_color:'#fff',bg_color:'#142c48',row_scale:1,input_order:i}));
 const boss_catalog=events.filter(e=>e.kind==='boss').map(e=>({id:e.catalog_id,name:e.name,kind:e.kind,group:'Preview',important:e.important}));
 events.push({...events[2],id:'f'.repeat(24),scheduled_at:iso(1444),end_at:iso(1445)});
 for(const id of ['d','e'])events.push({...events[1],id:id.repeat(24),name:'Simultaneous '+id,scheduled_at:iso(-15),end_at:iso(-14)});
 localStorage.setItem('BossTimer.webCustom.v1:\uC6249:\uB538\uAE30',JSON.stringify({schema:1,selected:boss_catalog.map(e=>e.id)}));
 const transfers=window.previewMovieTransfers={active:0,max:0,requests:0,catalogues:0};
 const realFetch=window.fetch;window.fetch=async(input,init)=>{
  const path=String(input);
  if(path==='/cinematic/catalog.json')++transfers.catalogues;
  if(path.startsWith('/api/'))return new Response(JSON.stringify({server_time:new Date().toISOString(),schedule_ready:true,schedule_state:'ready',schedule_stale:false,schedule_version:'preview',major_version:'preview',catalog_version:'preview',server_name:'\uC6249',guild_name:'\uB538\uAE30',season:'18',window_start:new Date(stamp-86400000).toISOString(),window_end:new Date(stamp+172800000).toISOString(),events,active:[],boss_catalog,numbers:{},available:false,configured:false,authenticated:true,allowed:false}),{headers:{'Content-Type':'application/json'}});
  if(path==='/fonts/catalog.json')return new Response(JSON.stringify({schema:1,fonts:previewFont15?[{id:'cafe24-dongdong',number:15,name:'\uCE74\uD39824 \uB3D9\uB3D9',url:'/fonts/cafe24-dongdong.woff2?v=ae571e9f0059aaf3'}]:[],default:previewFont15?'cafe24-dongdong':'system',can_save_default:false}),{headers:{'Content-Type':'application/json'}});
  if(/^\/cinematic\/[0-9a-f]+\.mp4$/.test(path)){
   ++transfers.requests;++transfers.active;transfers.max=Math.max(transfers.max,transfers.active);
   try{const response=await realFetch(input,init),blob=response.blob.bind(response);response.blob=()=>blob().finally(()=>--transfers.active);return response;}
   catch(error){--transfers.active;throw error;}
  }
  return realFetch(input,init);
 };
 window.previewErrors=errors;
})();"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = self.path.split('?')[0]
            if path.startswith('/cinematic/'):
                from web_cinematic import cinematic_response
                code, raw, mime, extra = cinematic_response(path, self.headers.get('Range', ''))
                self.send_response(code)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Security-Policy', policy)
                self.send_header('Content-Length', str(len(raw)))
                for key, value in extra:
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(raw)
                return
            raw, mime = {'/': (page, 'text/html'), '/preview': (preview, 'text/html'),
                         '/old-transport.css': (b'''html[data-theme="avengers"] .schedule-app .ironman-transport{display:block;padding:16px 12px;margin-top:5px;border:8px solid gold;background:repeating-linear-gradient(90deg,#330900,#330900 20px,#47210c 21px);box-shadow:0 0 8px orange}
html[data-theme="avengers"] .schedule-app .ironman-transport button{width:30px;height:44px;padding:6px;border:5px solid gold;background:#18140c}
html[data-theme="avengers"] .schedule-app .ironman-transport-status{position:static;width:auto;height:auto;overflow:visible;clip-path:none;margin:4px}''', 'text/css'),
                         '/cinematic-check.js': ((root/'check_web_cinematic.js').read_bytes(), 'text/javascript'),
                         '/preview-fixture.js': (preview_fixture.encode(), 'text/javascript'),
                         '/schedule.js': (files['schedule.js'], 'text/javascript'), '/style.css': (files['style.css'], 'text/css'),
                         '/fonts/cafe24-dongdong.woff2': ((root/'web_assets/fonts/cafe24-dongdong.woff2').read_bytes(), 'font/woff2'),
                         '/fixture.js': (script.encode(), 'text/javascript')}.get(self.path.split('?')[0], (b'', 'text/plain'))
            self.send_response(200)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Security-Policy', policy)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        print('http://127.0.0.1:' + str(server.server_port) + '/', flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    check()

'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const script=fs.readFileSync('web_assets/pwa/install.js','utf8');
function fixture({android=true,standalone=false,apple=false,appleApp=false,tablet=false,loads=1,related=async()=>[]}={}){
  const listeners={},elements=new Map(),inserted=[];
  function element(){return {hidden:false,children:[],attributes:{},listeners:{},append(...items){this.children.push(...items);},
    setAttribute(k,v){this.attributes[k]=v;},addEventListener(k,f){this.listeners[k]=f;}};}
  const document={readyState:'complete',hidden:false,createElement:element,addEventListener(k,f){listeners[k]=f;},
    body:{prepend(el){elements.set(el.id,el);inserted.push(el);}}};
  const mode={matches:standalone,addEventListener(k,f){listeners['mode-'+k]=f;}};
  const context={document,navigator:{userAgent:apple?'iPhone Safari/604.1':tablet?'Macintosh Safari/604.1':android?'Android Chrome/155.0':'Windows Chrome/155.0',platform:tablet?'MacIntel':'',maxTouchPoints:tablet?5:0,standalone:appleApp,getInstalledRelatedApps:related},
    location:{href:'https://boss.example/'},URL,matchMedia:()=>mode,isSecureContext:true,addEventListener(k,f){listeners[k]=f;}};
  context.window=context;
  const sandbox=vm.createContext(context);
  for(let i=0;i<loads;i++) vm.runInContext(script,sandbox);
  return {bar:elements.get('bt-pwa-install'),listeners,mode,inserted};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const f=fixture();await flush();
  assert.equal(fixture({loads:2}).inserted.length,1,'repeated script load must mount only one install entry');
  assert.equal(f.bar.hidden,false);
  const button=f.bar.children[1],help=f.bar.children[2];
  await button.listeners.click();assert.equal(help.hidden,false);assert.equal(f.bar.hidden,false);
  let calls=0;
  const offer=()=>f.listeners.beforeinstallprompt({preventDefault(){},prompt:async()=>{calls++;},userChoice:Promise.resolve({outcome:'dismissed'})});
  offer();await button.listeners.click();assert.equal(calls,1);assert.equal(f.bar.hidden,false);
  await button.listeners.click();assert.equal(calls,1,'cannot reuse a consumed event');
  offer();await button.listeners.click();assert.equal(calls,2,'new event can prompt after cancellation');
  f.listeners.appinstalled();assert.equal(f.bar.hidden,true);
  const app=fixture({standalone:true});assert.equal(app.bar.hidden,true);
  assert.equal(fixture({android:false}).bar,undefined);
  const ios=fixture({apple:true});await ios.bar.children[1].listeners.click();
  assert.match(ios.bar.children[2].textContent,/Safari/);assert.equal(ios.bar.hidden,false);
  assert.equal(fixture({apple:true,appleApp:true}).bar.hidden,true);
  assert.ok(fixture({tablet:true}).bar);
  const existing=fixture({related:async()=>[{platform:'webapp',url:'/manifest.webmanifest'}]});await flush();
  assert.equal(existing.bar.hidden,true);
  const uninstalled=fixture();await flush();assert.equal(uninstalled.bar.hidden,false,'no permanent installed cookie');
  const failed=fixture({related:async()=>{throw Error('API unavailable');}});await flush();assert.equal(failed.bar.hidden,false);
  let resolve;
  const race=fixture({related:()=>new Promise(done=>{resolve=done;})});
  race.listeners.appinstalled();resolve([]);await flush();assert.equal(race.bar.hidden,true);
  const newer=fixture({related:()=>new Promise(done=>{resolve=done;})});
  newer.listeners.beforeinstallprompt({preventDefault(){},prompt:async()=>{},userChoice:Promise.resolve({outcome:'dismissed'})});
  resolve([{platform:'webapp',url:'/manifest.webmanifest'}]);await flush();assert.equal(newer.bar.hidden,false);
  const broken=fixture();await flush();broken.listeners.beforeinstallprompt({preventDefault(){},prompt:async()=>{throw Error('suppressed');}});
  await broken.bar.children[1].listeners.click();assert.equal(broken.bar.hidden,false);assert.equal(broken.bar.children[2].hidden,false);
  console.log('PWA install entry: cancellation/retry, manual fallback, installed detection, race and platform checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

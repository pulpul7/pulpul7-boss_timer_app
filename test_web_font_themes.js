/* Full browser font module with local storage and deterministic FontFace loads. */
const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
function fixture(initial={},suit=false){
 const values=new Map(Object.entries(initial)),nodes=new Map(),props=new Map(),requests=[],deferred=new Map();
 let fail=false;
 const el=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,disabled:false,textContent:'',listeners:{},
  addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(){},removeAttribute(){},appendChild(){},replaceChildren(){},
  async fire(type='click'){return this.listeners[type]?.();}});return nodes.get(id);};
 const document={getElementById:el,createElement:()=>({}),documentElement:{style:{setProperty:(k,v)=>props.set(k,v),getPropertyValue:k=>props.get(k)||''},classList:{toggle(){}}},fonts:{add(){},delete(){}}};
 class Face{constructor(name){this.name=name;}load(){return deferred.has(this.name)?new Promise(resolve=>deferred.set(this.name,()=>resolve(this))):Promise.resolve(this);}}
 const api=new Function('document','window','localStorage','FontFace','fetch','AbortController','Event','setTimeout','clearTimeout',source+';return WebFonts;')(
  document,{dispatchEvent(){}},{getItem:k=>values.get(k)??null,setItem(k,v){if(fail)throw Error('denied');values.set(k,v);}},Face,
  async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({schema:1,default:'font-a',can_save_default:false,size_defaults:{'font-a':1,'font-b':-1},fonts:['font-a','font-b',...(suit?['suit']:[])].map((id,i)=>({id,number:i+1,name:id,url:'/fonts/'+id+'.woff2?v='+'1'.repeat(16)}))})};},
  AbortController,class{},()=>1,()=>{});
 return {api,values,el,requests,deferred,fail:v=>fail=v,size:()=>props.get('--web-font-size-offset'),
  font:()=>el('web-font-select').value,async pick(id){el('web-font-select').value=id;await el('web-font-select').fire('change');}};
}
const legacy='BossTimer.webFont.v1:alice',sizes='BossTimer.webFont.size.v2:alice',key='BossTimer.webFont.themes.v1:alice';
const f=fixture({[legacy]:'font-b',[sizes]:JSON.stringify({schema:1,overrides:{'font-b':3}})});
await f.api.refreshScope('alice',true,'default');
check(f.font()==='font-b'&&f.size()==='3px','legacy browser font and per-font size remain the initial fallback');
check(!f.el('web-font-size-save').hidden&&!f.el('web-font-size-save').disabled&&!f.el('web-font-all-save').disabled,'personal theme buttons work for ordinary users');
await f.el('web-font-size-plus').fire();
check(f.size()==='4px'&&!f.values.has(key)&&JSON.parse(f.values.get(sizes)).overrides['font-b']===3,'size changes preview without overwriting stored legacy or theme preferences');
await f.el('web-font-size-save').fire();
check(JSON.parse(f.values.get(key)).themes.default.font==='font-b'&&JSON.parse(f.values.get(key)).themes.default.size===4,'theme save persists font and size together');
await f.api.refreshScope('alice',true,'avengers');await f.pick('font-a');await f.el('web-font-size-minus').fire();await f.el('web-font-size-save').fire();
check(JSON.parse(f.values.get(key)).themes.avengers.font==='font-a'&&JSON.parse(f.values.get(key)).themes.avengers.size===0,'another theme stores its independent pair');
await f.api.refreshScope('alice',true,'default');check(f.font()==='font-b'&&f.size()==='4px','switch back restores original theme pair');
await f.api.refreshScope('alice',true,'avengers');check(f.font()==='font-a'&&f.size()==='0px','switch again restores Iron Man pair');
await f.pick('system');await f.el('web-font-size-plus').fire();await f.el('web-font-all-save').fire();
const all=JSON.parse(f.values.get(key));
check(all.all.font==='system'&&all.all.size===1&&Object.keys(all.themes).length===0,'apply all persists pair and clears all previous per-theme exceptions');
await f.api.refreshScope('alice',true,'winter');check(f.font()==='system'&&f.size()==='1px','new theme uses all-theme pair');
await f.api.refreshScope('alice',true,'default');check(f.font()==='system'&&f.size()==='1px','previously customized theme also uses all-theme pair');
await f.el('web-font-size-default').fire();
check(f.font()==='font-a'&&f.size()==='1px'&&JSON.parse(f.values.get(key)).themes.default.font==='font-a','default restores and saves catalog default font with its default size for current theme');
await f.api.refreshScope('alice',true,'winter');check(f.font()==='system'&&f.size()==='1px','default for one theme preserves all-theme fallback elsewhere');
await f.api.refreshScope('bob',true,'winter');check(f.font()==='font-a'&&f.size()==='1px','different nickname cannot inherit another user theme choices');
check(!f.values.has('BossTimer.webFont.themes.v1:bob'),'loading a nickname never silently creates saved choices');
await f.api.refreshScope('alice',true,'avengers');f.fail(true);await f.pick('font-b');await f.el('web-font-size-save').fire();
check(JSON.parse(f.values.get(key)).all.font==='system'&&f.el('web-font-note').textContent.includes('저장하지 못'),'storage failure retains previous saved choices and reports failure');f.fail(false);
const fresh=fixture(Object.fromEntries(f.values));await fresh.api.refreshScope('alice',true,'default');
check(fresh.font()==='font-a'&&fresh.size()==='1px','saved theme pair survives a fresh module/page');
const remote=JSON.parse(fresh.values.get(key));remote.themes.default={font:'font-b',size:2};fresh.values.set(key,JSON.stringify(remote));fresh.api.onStorage({key});
for(let i=0;i<20;i++)await Promise.resolve();
check(fresh.font()==='font-b'&&fresh.size()==='2px','storage changes restore current theme pair in another tab');
await fresh.pick('font-a');
check(!fresh.el('web-font-size-default').disabled,'default remains available when preview already matches catalog default but saved theme differs');
await fresh.el('web-font-size-default').fire();
check(JSON.parse(fresh.values.get(key)).themes.default.font==='font-a','default saves reset even when preview already shows default');
check(f.requests.length===1&&fresh.requests.every(r=>!r.options?.method),'theme switches reuse one catalog request and never POST server defaults');
const race=fixture();await race.api.refreshScope('alice',true,'default');race.deferred.set('BossTimerFont_font_b',true);
const old=race.pick('font-b');await Promise.resolve();await race.api.refreshScope('alice',true,'winter');race.deferred.get('BossTimerFont_font_b')();await old;
check(race.font()==='font-a'&&race.size()==='1px','late font load from previous theme cannot override current theme');
const bad=fixture({[key]:JSON.stringify({schema:1,themes:{default:{font:'font-b',size:500}}})});await bad.api.refreshScope('alice',true,'default');
check(bad.font()==='font-a'&&bad.size()==='1px','out-of-range stored pairs are ignored safely');
const missing=fixture({[key]:JSON.stringify({schema:1,all:{font:'font-b',size:2},themes:{default:{font:'deleted-font',size:1}}})});await missing.api.refreshScope('alice',true,'default');
check(missing.font()==='font-b'&&missing.size()==='2px','unavailable theme font falls back to valid all-theme pair');
const iron=fixture({},true);await iron.api.refreshScope('alice',true,'avengers');
check(iron.font()==='suit'&&iron.size()==='0px','fresh Iron Man uses SUIT and its default size');
await iron.api.refreshScope('alice',true,'default');
check(iron.font()==='font-a','other themes retain catalog default');
await iron.api.refreshScope('alice',true,'avengers');await iron.pick('font-b');await iron.el('web-font-size-save').fire();
await iron.api.refreshScope('alice',true,'winter');await iron.api.refreshScope('alice',true,'avengers');
check(iron.font()==='font-b','saved personal Iron Man font takes precedence over SUIT');
await iron.el('web-font-size-default').fire();
check(iron.font()==='suit'&&JSON.parse(iron.values.get(key)).themes.avengers.font==='suit','Iron Man default button restores and saves SUIT');
const prior=fixture({[legacy]:'font-b'},true);await prior.api.refreshScope('alice',true,'avengers');
check(prior.font()==='font-b','legacy personal font remains preferred over theme deployment default');
const absent=fixture();await absent.api.refreshScope('alice',true,'avengers');
check(absent.font()==='font-a','unavailable SUIT retains safe catalog fallback');
return {checks:checks.length};

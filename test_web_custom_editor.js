/* Independent color targets, role gates, and shared DOM ownership. */
const checks=[];
function check(ok,note){if(!ok)throw Error(note);checks.push(note);}
function fixture(){
 const nodes=new Map();
 function element(id){if(!nodes.has(id))nodes.set(id,{id,hidden:false,attrs:{},events:{},parentElement:null,
  setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.events[k]=fn;},focus(){this.focused=true;},
  append(node){node.parentElement=this;}});return nodes.get(id);}
 element('custom-selection-host').append(element('custom-groups'));
 const doc={getElementById:element};
 const api=new Function('document',editorSource+';return globalThis.WebCustomEditor;')(doc);
 let allowed=false,editing=true,busy=false,repaints=0;
 api.init({known:id=>['a','b'].includes(id),catalog:()=>[{id:'a'},{id:'b'}],busy:()=>busy,
  switched(){api.refresh(allowed,editing);repaints++;},repaint(){api.refresh(allowed,editing);repaints++;}});
 return{api,nodes,grant(value){allowed=value;api.refresh(allowed,editing);},edit(value){editing=value;api.refresh(allowed,editing);},
  busy(value){busy=value;},click(id){element(id).events.click();},repaints:()=>repaints};
}
const f=fixture(),personal=new Set(['a']);f.grant(false);
check(f.nodes.get('custom-color-tab').hidden,'ordinary users cannot see color tab');
f.click('custom-color-tab');check(!f.api.active(),'hidden color action cannot bypass permission');
f.grant(true);f.click('custom-color-tab');
check(f.api.active()&&!f.nodes.get('custom-color-panel').hidden,'registered admin can open color panel');
check(f.nodes.get('custom-groups').parentElement.id==='custom-color-host','color tab reuses picker DOM');
check(f.api.selection(personal).size===0,'color targets start independently of personal boss choices');
f.api.change(set=>set.add('b'));
check([...f.api.selection(personal)].join()==='b'&&[...personal].join()==='a','color choice cannot modify personal choices');
f.click('custom-selection-tab');
check(f.api.selection(personal)===personal&&f.nodes.get('custom-groups').parentElement.id==='custom-selection-host','returning restores original personal selection and grid');
f.click('custom-color-tab');check(f.api.selection(personal).has('b'),'color targets survive switching tabs');
f.click('custom-color-all');check(f.api.selection(personal).size===2&&personal.size===1,'color bulk selection is independent');
f.busy(true);f.click('custom-color-clear');check(f.api.selection(personal).size===2,'in-flight color saves lock target changes');
f.busy(false);f.click('custom-color-clear');check(f.api.selection(personal).size===0&&personal.has('a'),'color clear cannot clear personal choices');
f.nodes.get('custom-color-tab').events.keydown({key:'ArrowLeft',preventDefault(){}});
check(!f.api.active()&&f.nodes.get('custom-selection-tab').focused,'keyboard switches to personal tab');
f.click('custom-color-tab');f.grant(false);
check(!f.api.active()&&f.nodes.get('custom-color-tab').hidden&&f.api.selection(personal)===personal,'revocation returns to ordinary personal editor');
f.grant(true);f.click('custom-color-tab');f.edit(false);
check(!f.api.active(),'closing settings resets editor to personal tab');
// Compact embedded palette must retain the same public color API/data.
const begin='// BEGIN WEB BOSS SPECTRUM MODULE',end='// END WEB BOSS SPECTRUM MODULE';
const embedded=source.slice(source.indexOf(begin)+begin.length,source.indexOf(end));
const moduleOf=code=>new Function(code+';return WebBossSpectrum;')();
const readable=moduleOf(spectrumSource),packed=moduleOf(embedded);
check(JSON.stringify(readable.palettes)===JSON.stringify(packed.palettes),'embedded color palettes match readable source');
for(const color of ['#ffffff','#000000','#6f7999'])check(readable.ink(color)===packed.ink(color),'embedded contrast matches '+color);
return(async()=>{
// Exercise the real module boundary, including the compacted public hook key.
for(const [label,code] of [['readable',spectrumSource],['embedded',embedded]]){
 const nodes=new Map(),events={},scope={};
 const make=()=>({children:[],attrs:{},events:{},hidden:false,dataset:{},style:{setProperty(){},removeProperty(){}},
  classList:{toggle(){}},setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.events[k]=fn;},
  append(...children){for(const child of children){child.parentElement=this;this.children.push(child);}},
  replaceChildren(...children){this.children=[];this.append(...children);},focus(){},scrollIntoView(){}});
 for(const text of [html,editorSource])for(const match of text.matchAll(/id="([a-z0-9-]+)"/g))if(!nodes.has(match[1]))nodes.set(match[1],make());
 const el=id=>nodes.get(id)||null;
 el('custom-selection-host').append(el('custom-groups'));
 const document={getElementById:el,createElement:make,querySelectorAll:()=>[],addEventListener(k,fn){events[k]=fn;}};
 const editor=new Function('document','globalThis',editorSource+';return globalThis.WebCustomEditor;')(document,scope);
 const a='a'.repeat(24),b='b'.repeat(24),personal=new Set([a]);let completeSave,requests=0;
 const api=new Function('document','globalThis','fetch','AbortController','setTimeout','clearTimeout',code+';return WebBossSpectrum;')(
  document,scope,async(url,options)=>{requests++;if(options.method==='POST')return new Promise(resolve=>completeSave=resolve);
   return{ok:true,json:async()=>({schema:2,numbers:{},can_save:true})};},class{},()=>1,()=>{});
 api.init({selected:()=>api.selection(personal),catalog:()=>[{id:a},{id:b}],known:id=>[a,b].includes(id),editing:()=>true,
  theme:()=> 'gray',note(){},repaint(){api.refresh();}});
 for(let i=0;i<5;i++)await Promise.resolve();
 check(!el('custom-color-tab').hidden,label+' integrated permission exposes color tab');
 el('custom-color-tab').events.click();
 check(editor.active()&&el('custom-color-tab').attrs['aria-selected']==='true',label+' integrated click opens color tab without hook errors');
 check(el('custom-groups').parentElement===el('custom-color-host'),label+' integrated switch moves picker to color panel');
 el('custom-color-all').events.click();
 check(api.selection(personal).size===2&&personal.size===1,label+' integrated color targeting preserves personal choices');
 el('boss-spectrum-bar').events.click({target:{closest:()=>({dataset:{spectrumNumber:'7'}})}});
 const save=el('boss-spectrum-save').events.click();
 el('custom-selection-tab').events.click();el('custom-color-clear').events.click();
 check(editor.active()&&api.selection(personal).size===2,label+' compacted busy hook protects tab and targets during real save');
 completeSave({ok:true,json:async()=>({schema:2,numbers:{[a]:7,[b]:7},can_save:true})});await save;
 el('custom-selection-tab').events.click();
 check(!editor.active()&&api.selection(personal)===personal,label+' integrated tab works again after save');
 check(requests===2,label+' tab switches and selection changes add no network reads');
}
for(const [label,code] of [['readable',spectrumSource],['embedded',embedded]]){
 let reads=0,repaints=0;
 const id='a'.repeat(24);
 const api=new Function('document','fetch','AbortController','setTimeout','clearTimeout','globalThis',code+';return WebBossSpectrum;')(
  {getElementById:()=>null},async()=>{reads++;return{ok:true,json:async()=>({schema:2,numbers:{[id]:7},can_save:false})};},
  class{},()=>1,()=>{},{});
 api.init({selected:()=>new Set(),known:()=>true,theme:()=> 'gray',note(){},repaint(){repaints++;}});
 for(let i=0;i<5;i++)await Promise.resolve();
 check(reads===1&&repaints===1,label+' missing optional companion still loads published colors once');
 check(/^#[0-9a-f]{6}$/.test(api.color({catalog_id:id})),label+' legacy host fallback preserves schedule colors');
}
return{checks:checks.length};
})();

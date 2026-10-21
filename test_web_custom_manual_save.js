/* Production selection persistence: only an explicit save commits a draft. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
const values=new Map(),writes=[];let fail=false;
const a='a'.repeat(24),b='b'.repeat(24),many=Array.from({length:4000},(_,i)=>i.toString(16).padStart(24,'0'));
const state=source.slice(source.indexOf("let customScope=''"),source.indexOf('function readCustomSelection('));
const logic=source.slice(source.indexOf('function readCustomSelection('),source.indexOf('function customRowSelected('));
const api=new Function('localStorage','catalog',`
  let viewKey='',mainKey='',renderedKey='',activeKey='';
  const refreshCustomUI=()=>{},tick=()=>{},refreshAlarms=()=>{};
  const setTimeout=()=>{throw Error('selection changes must not schedule autosave');};
  ${state}\n${logic}
  customCatalog=catalog;
  return {load:readCustomSelection,change:changeCustomSelection,save:saveCustomSelection,
    signature:()=>selectionSignature(customDraft),catalog:acceptCatalog,
    undo(){changeCustomSelection(set=>{set.clear();customSaved.forEach(id=>set.add(id));});},
    state:()=>({dirty:customDirty(),draft:[...customDraft],saved:[...customSaved],message:customSaveMessage})};
`)({getItem:k=>values.get(k)||null,setItem(k,v){if(fail)throw Error('storage denied');writes.push(k);values.set(k,v);}},
  [a,b,...many].map(id=>({id,kind:'boss',group:'2챕'})));
values.set('first',JSON.stringify({schema:2,revision:2,included:[a],excluded:[]}));api.load('first');
check(api.state().saved.join()===a&&writes.length===0,'opening preferences preserves saved selection');
api.change(set=>set.add(b));
check(api.state().dirty&&api.state().draft.length===2&&writes.length===0,'selection remains an unsaved draft without timer or writes');
api.undo();check(!api.state().dirty&&writes.length===0,'undo restores saved selection without a write');
api.change(set=>set.add(b));api.load('other');
check(!values.has('other')&&JSON.parse(values.get('first')).included.join()===a&&writes.length===0,'profile change cannot implicitly commit old draft');
api.load('first');check(api.state().draft.join()===a,'reopening reloads last explicitly saved selection');
api.change(set=>set.add(b));fail=true;
check(api.save()===false&&api.state().dirty&&api.state().saved.join()===a,'storage failure preserves saved state and draft for retry');
fail=false;check(api.save()===true&&!api.state().dirty&&writes.length===1,'explicit save commits exactly once');
check(JSON.parse(values.get('first')).included.join()===a+','+b,'explicit save persists requested selections');
check(api.save()===false&&writes.length===1,'unchanged save does not write again');
api.change(set=>set.clear());check(writes.length===1&&api.state().dirty,'clear all remains unsaved');
api.save();api.load('other');api.load('first');check(api.state().saved.length===0,'explicitly saved empty selection survives reload');
check((source.match(/saveCustomSelection\(/g)||[]).length===2&&source.includes("element('custom-save').addEventListener('click',()=>saveCustomSelection())"),
  'only the save-button listener calls the save function, including lifecycle and refresh paths');
check(!source.includes('autoSaveCustom')&&!source.includes('customChangedAt')&&!source.includes('customTimer'),'obsolete autosave state and callbacks removed');
check(!html.includes('7초 후 자동 저장'),'editor guidance no longer promises automatic saving');
// Clock/render/cache-key reads must reuse the signature until selection changes.
values.set('many',JSON.stringify({schema:2,revision:2,included:many,excluded:[]}));api.load('many');
let sorts=0;const originalSort=Array.prototype.sort;
try {
  Array.prototype.sort=function(...args){++sorts;return originalSort.apply(this,args);};
  for(let i=0;i<600;i++){api.signature();api.state();}
} finally {Array.prototype.sort=originalSort;}
check(sorts===2,'600 unchanged clock/key/dirty reads sort each 4000-item selection only once');
const old=api.signature();
api.change(set=>{set.delete(many[0]);set.add(b);});
check(api.state().draft.length===4000&&api.state().dirty&&api.signature()!==old,'same-size replacement invalidates cached signature');
api.undo();check(!api.state().dirty&&api.signature()===old,'undo restores canonical selection and dirty state');
api.change(set=>{const first=[...set][0];set.delete(first);set.add(first);});
check(!api.state().dirty&&api.signature()===old,'insertion-order changes do not count as selection changes');
api.catalog({boss_catalog:[{id:b,kind:'boss',group:'2챕',legacy_ids:[many[0]]}]});
check(!api.state().dirty&&api.state().saved.includes(b)&&api.state().draft.includes(b),'catalog alias migration updates both signature caches without creating a draft');
return {checks:checks.length};

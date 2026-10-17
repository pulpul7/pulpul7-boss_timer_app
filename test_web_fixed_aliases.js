/* Offline personal selection migration checks. Parameter: source. */
const checks=[], values=new Map();let writes=0,blocked=false;
function check(value,message){if(!value)throw Error(message);checks.push(message);}
const state=source.slice(source.indexOf("let customScope=''"),source.indexOf('function readCustomSelection('));
const read=source.slice(source.indexOf('function readCustomSelection('),source.indexOf('function changeCustomSelection('));
const storage={getItem:key=>values.get(key) ?? null,setItem(key,value){if(blocked)throw Error('blocked');writes++;values.set(key,value);}};
const api=new Function('localStorage','clearTimeout',`
 let viewKey='',mainKey='',renderedKey='',activeKey='';
 const saveCustomSelection=()=>{throw Error('unexpected save');};
 ${state}\n${read}
 return {load(scope){customScope='';readCustomSelection(scope);},accept:acceptCatalog,
  edit(id){customDraft.delete(id);customChangedAt=123;customTimer=7;},
  state(){return {saved:[...customSaved].sort(),draft:[...customDraft].sort(),dirty:customDirty(),at:customChangedAt,timer:customTimer};}};
`)(storage,()=>{});
const old='a'.repeat(24), current='b'.repeat(24), unknown='c'.repeat(24), other='d'.repeat(24);
const catalog={boss_catalog:[{id:current,legacy_ids:[old]},{id:other}]};
values.set('personal',JSON.stringify({schema:1,selected:[old,current,unknown]}));
api.load('personal');api.accept(catalog);
check(JSON.stringify(api.state().saved)===JSON.stringify([current,unknown]),'old and current aliases merge into one personal selection');
check(!api.state().dirty&&api.state().draft.length===2,'migration preserves clean draft and unknown selections');
check(writes===1&&!JSON.parse(values.get('personal')).selected.includes(old),'canonical choices are persisted once');
api.accept(catalog);
check(writes===1,'repeated status catalogs do not create extra storage writes');
values.set('pending',JSON.stringify({schema:1,selected:[old,other]}));
api.load('pending');api.edit(other);api.accept(catalog);
check(api.state().dirty&&api.state().saved.includes(other)&&!api.state().draft.includes(other),'pending user edits remain separate from saved choices');
check(api.state().at===123&&api.state().timer===7,'migration preserves existing seven second auto-save timing');
check(JSON.parse(values.get('pending')).selected.includes(other),'migration does not save unfinished user edits');
values.set('blocked',JSON.stringify({schema:1,selected:[old]}));blocked=true;
api.load('blocked');api.accept(catalog);
check(api.state().saved[0]===current&&!api.state().dirty,'blocked storage still restores selection in memory');
values.set('later',JSON.stringify({schema:1,selected:[old]}));blocked=false;
api.load('later');api.accept({});
check(api.state().saved[0]===old,'temporarily missing catalog keeps old selection');
api.accept(catalog);
check(api.state().saved[0]===current,'later catalog arrival restores renamed selection');
return checks;

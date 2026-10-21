/* Production release exclusions, one-time legacy reset and future catalog growth. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
const state=source.slice(source.indexOf("let customScope=''"),source.indexOf('function readCustomSelection('));
const logic=source.slice(source.indexOf('function readCustomSelection('),source.indexOf('function customRowSelected('));
const values=new Map();let writes=0,fail=false;
const api=new Function('localStorage',`
 let viewKey='',mainKey='',renderedKey='',activeKey='',nicknameValue='test';
 const refreshCustomUI=()=>{},tick=()=>{},refreshAlarms=()=>{},majorBoss=()=>false;
 ${state}\n${logic}
 return {load:readCustomSelection,catalog:rows=>acceptCatalog({boss_catalog:rows}),save:saveCustomSelection,
   change:changeCustomSelection,defaultOn:customDefaultSelected,
   selected:id=>customDraft.has(id),saved:id=>customSaved.has(id),dirty:customDirty};
`)({getItem:k=>values.get(k)||null,setItem(k,v){if(fail)throw Error('denied');writes++;values.set(k,v);}});
let serial=0;
const row=(name,group='4챕',extra={})=>({id:(++serial).toString(16).padStart(24,'0'),name,group,kind:'boss',...extra});
const rows=[row('normal2','2챕'),row('absolute2','2챕',{absolute:true}),row('normal3','3챕'),
 row('absolute3','3챕',{absolute:true}),row('normal4'),row('normal8','8챕'),row('normal9','9챕'),
 row('normal10','10챕'),row('4 층','던전'),row('7층','던전'),row('10층','던전'),row('상위던전','던전'),
 row('월 드 보 스','고정보스',{kind:'fixed'}),row('발할라 대전','고정보스',{kind:'fixed'}),
 row('황금 고블린','고정보스',{kind:'fixed'}),row('지옥성채 정예','고정보스',{kind:'fixed'}),
 row('지옥성채','고정보스',{kind:'fixed'}),row('길던','특수 이벤트'),row('체크된침공','특수 이벤트'),row('침공','특수 이벤트')];
rows.push(row('사용자 이벤트','특수 이벤트'));
const excluded=new Set([0,2,8,12,13,14,15,19,20]);
check(rows.every((r,i)=>api.defaultOn(r)===!excluded.has(i)),'release rules exclude only 2/3 ordinary bosses, fourth floor, listed events and all-invasion mode');
check(rows.filter(r=>r.absolute).every(api.defaultOn),'absolute bosses remain included in excluded chapters');
values.set('prefs',JSON.stringify({schema:2,revision:1,included:[rows[0].id,rows[20].id],excluded:[rows[18].id]}));
values.set('unrelated-video-cache','keep');api.load('prefs');
api.catalog([]);check(writes===0,'empty catalog does not consume the one-time migration');
api.catalog(rows);
check(rows.every((r,i)=>api.selected(r.id)===!excluded.has(i)),'legacy personal choices switch to distribution defaults once');
check(writes===1&&!api.dirty(),'initial migration persists once without leaving an unsaved draft');
const initial=JSON.parse(values.get('prefs'));
check(initial.schema===2&&initial.revision===2&&initial.excluded.length===0&&initial.included.length===0&&!('selected' in initial),'new release defaults reset prior major choices once, then store exceptions rather than a frozen include list');
check(values.get('unrelated-video-cache')==='keep','migration preserves unrelated browser storage');
api.catalog(rows);check(writes===1,'unchanged catalog does not rewrite preferences');
api.change(set=>{set.delete(rows[4].id);set.add(rows[0].id);});
check(api.dirty()&&writes===1,'personal changes remain drafts');
const future=row('future fixed','고정보스',{kind:'fixed'});rows.push(future);api.catalog(rows);
check(api.selected(future.id)&&api.saved(future.id),'new fixed boss automatically enters saved and draft views');
check(!api.selected(rows[4].id)&&api.selected(rows[0].id)&&api.dirty(),'catalog growth preserves unsaved inclusions and exclusions');
api.save();const saved=JSON.parse(values.get('prefs'));
check(saved.excluded.join()===rows[4].id&&saved.included.join()===rows[0].id,'save stores only personal exceptions to the release policy');
api.load('other');api.load('prefs');api.catalog(rows);
check(!api.selected(rows[4].id)&&api.selected(rows[0].id)&&!api.dirty(),'later visits retain personal choices rather than reapplying migration');
const later=[row('chapter11','11챕'),row('upper2','던전'),row('future event','고정보스',{kind:'fixed'})];
api.catalog([...rows,...later]);check(later.every(r=>api.selected(r.id)),'future chapters, upper dungeons and fixed events automatically join after a saved preference');
api.catalog(rows.filter(r=>r.id!==rows[4].id));api.change(set=>set.delete(rows[5].id));api.save();
api.catalog(rows);check(!api.selected(rows[4].id),'temporarily missing excluded boss remains excluded when it returns');
api.change(set=>set.add(rows[4].id));fail=true;
check(!api.save()&&api.dirty()&&!api.saved(rows[4].id),'storage failure preserves committed policy and pending draft');
fail=false;api.save();check(!api.dirty(),'explicit retry saves the draft');
const before=writes;api.catalog([...rows.map(r=>({...r,label:'changed alias'}))]);
check(writes===before&&!api.selected(rows[5].id),'display alias changes preserve exclusions without writes');
api.change(set=>set.add(rows[20].id));api.save();api.load('other');api.load('prefs');api.catalog(rows);
check(api.selected(rows[20].id),'explicitly selecting user events after the reset remains a saved personal choice');
return checks;

/* Production personal boss selection: invasion modes, identity and explicit save. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
const state=source.slice(source.indexOf("let customScope=''"),source.indexOf('function readCustomSelection('));
const logic=source.slice(source.indexOf('function readCustomSelection('),source.indexOf('function pickerStar('));
const values=new Map();let writes=0;
const api=new Function('localStorage',`
 let viewKey='',mainKey='',renderedKey='',activeKey='',nicknameValue='test';
 const refreshCustomUI=()=>{},tick=()=>{},refreshAlarms=()=>{},majorBoss=()=>false;
 ${state}\n${logic}
 return {load:readCustomSelection,catalog:acceptCatalog,save:saveCustomSelection,
   select:ids=>changeCustomSelection(set=>{set.clear();ids.forEach(id=>set.add(id));}),
   selected:customRowSelected,has:hasCustomSelection,dirty:customDirty};
`)({getItem:k=>values.get(k)||null,setItem(k,v){writes++;values.set(k,v);}});
const a='a'.repeat(24),b='b'.repeat(24),all='c'.repeat(24),chosen='d'.repeat(24),fixed='e'.repeat(24),special='f'.repeat(24);
const catalog=[{id:a,kind:'boss',name:'Canonical A',label:'Alias A',group:'2챕'},
 {id:b,kind:'boss',name:'Canonical B',group:'9챕'},
 {id:all,kind:'boss',name:'침공',label:'전체침공',group:'특수 이벤트'},
 {id:chosen,kind:'boss',name:'체크된침공',group:'특수 이벤트'},
 {id:fixed,kind:'fixed',name:'Fixed',group:'고정보스'},
 {id:special,kind:'boss',name:'사용자이벤트',group:'특수 이벤트'}];
const invasion=id=>({catalog_id:id,kind:'invasion',name:'Invasion display alias'});
const home=id=>({catalog_id:id,kind:'boss',name:'Different home alias'});
values.set('prefs',JSON.stringify({schema:1,selected:[all]}));api.load('prefs');api.catalog({boss_catalog:catalog});
check(!api.selected(invasion(a))&&api.selected(invasion(b))&&!api.selected(invasion('0'.repeat(24))),'legacy invasion preference migrates to checked-invasion distribution defaults excluding chapter two');
check(writes===1&&!api.dirty(),'catalog applies new defaults once and persists the new preference generation');
api.select([a]);
check(api.selected(home(a))&&!api.selected(home(b)),'home-server choices remain independent');
check(!api.selected(invasion(a)),'home selection alone does not implicitly enable invasions');
api.select([a,chosen]);
check(api.selected(invasion(a))&&!api.selected(invasion(b)),'checked-invasion mode includes only selected home bosses by identity');
check(!api.selected(invasion(fixed))&&!api.selected(invasion(special)),'unselected fixed and special events cannot enable invasions');
api.select([chosen,fixed,special]);
check(!api.selected(invasion(fixed))&&!api.selected(invasion(special)),'selected fixed and special events are not home-server boss choices');
check(!api.selected(invasion(a)),'checked-invasion mode with no home boss shows no invasion');
api.select([a,b,chosen]);
check(api.selected(invasion(a))&&api.selected(invasion(b)),'later home selection immediately expands checked invasions');
api.select([b,chosen]);
check(!api.selected(invasion(a))&&api.selected(invasion(b)),'deselection immediately removes the corresponding invasion');
api.select([chosen,all]);
check(api.selected(invasion(a))&&api.selected(invasion(b)),'all-invasion choice takes precedence when both modes are selected');
api.select([a,chosen]);check(writes===1&&api.dirty(),'mode and boss changes stay unsaved until explicit save');
check(api.save()&&writes===2,'explicit save persists the two independent selections once');
api.load('other');api.load('prefs');
check(api.selected(invasion(a))&&!api.selected(invasion(b))&&!api.dirty(),'checked-invasion mode survives profile switch and reload');
api.select([]);check(!api.has()&&!api.selected(invasion(a)),'clear all removes both invasion modes');
api.select([all]);
check(api.selected(invasion('0'.repeat(24))),'all-invasion mode includes an invasion whose home boss is not in the catalog');
api.catalog({boss_catalog:[]});
check(!api.has()&&!api.selected(invasion(a)),'missing catalog cannot activate a stale mode identity');
const groupHook=source.match(/^groupId:(.*),$/m)[1];
const groupId=new Function('customDraft','customInvasionId','customCheckedInvasionId',`return (${groupHook});`);
check(groupId(new Set([chosen]),all,chosen)(invasion(a))===chosen,'checked invasion color follows its own category assignment');
check(groupId(new Set([all,chosen]),all,chosen)(invasion(a))===all,'all-invasion color takes precedence when all-invasion mode is selected');
check(groupId(new Set([chosen]),all,chosen)({kind:'boss'})==='','ordinary bosses retain their individual spectrum assignment');
return checks;

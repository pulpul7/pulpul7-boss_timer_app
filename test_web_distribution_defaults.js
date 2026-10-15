/* Offline V8 checks: production defaults and saved OFF choices. Parameters: notices, sound, schedule. */
const checks=[];
function check(value,message){if(!value)throw Error(message);checks.push(message);}
const values=new Map(),elements=new Map(),events={};
const localStorage={getItem:key=>values.get(key) ?? null,setItem:(key,value)=>values.set(key,value)};
function node(){return {options:[],children:[],style:{setProperty(){}},append(...n){this.options.push(...n);},
  replaceChildren(...n){this.children=n;},setAttribute(k,v){this[k]=v;},addEventListener(){},hidden:false};}
const document={getElementById:id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);},
  createElement:node,addEventListener(){},body:{append(){}}};
const window={addEventListener:(name,fn)=>events[name]=fn};
const data={server_name:'test',guild_name:'guild',season:'18',events:[]};
const audio=new Function('document','window','localStorage',sound+'\nreturn WebNoticeSound;')(document,window,localStorage);
await audio.refresh('fresh',true);
check(elements.get('notice-sound-select').value==='water','new sound preference defaults to 02 water');
check(elements.get('notice-sound-enabled').checked===true,'new sound preference is ON');
check(elements.get('notice-sound-volume').value==='35','new sound preference volume is 35 percent');
values.set('BossTimer.webNoticeSound.v1:saved',JSON.stringify({selected:'piano',enabled:false,volume:17}));
await audio.refresh('saved',true);
check(elements.get('notice-sound-select').value==='piano'&&!elements.get('notice-sound-enabled').checked&&elements.get('notice-sound-volume').value==='17',
  'saved sound selection, mute and volume override release defaults');
const banner=new Function('document','window','localStorage','WebNoticeSound','data','deviceProfile','schedulePushSync','performance',
  notices+'\nreturn WebNotices;')(document,window,localStorage,{refresh:async()=>{},stop(){}},data,()=>({pc:true}),()=>{},{now:()=>100});
banner.refresh('fresh',true);
check(JSON.stringify(banner.times())==='[2,null,null]','new shared timing is one alert at two minutes');
check(elements.get('push-toggle')['aria-pressed']==='true','new desktop alert preference is ON');
values.set('BossTimer.webNotice.v1:'+JSON.stringify(['saved','test','guild','18']),JSON.stringify({times:[10,1,0],banner:false}));
banner.refresh('saved',true);
check(JSON.stringify(banner.times())==='[10,1,0]'&&elements.get('push-toggle')['aria-pressed']==='false','saved alert OFF and custom times are preserved');
const state=schedule.slice(schedule.indexOf('const REST_TABS_KEY='),schedule.indexOf("let viewKey=''"));
const read=schedule.slice(schedule.indexOf('function readRestPreferences('),schedule.indexOf('function refreshFixedFilter('));
const prefs=new Function('localStorage',`let selectedFilter='all';${state}\n${read}
 return {load(){readRestPreferences();readFixedPreferences();return {rest:restPrefs,fixed:fixedPrefs};}};`)(localStorage);
let p=prefs.load();
check(p.rest.all&&p.fixed.all,'all tab starts with rest and fixed ON');
check(p.rest.custom&&p.fixed.custom,'custom tab starts with rest and fixed ON');
check(!p.rest.today&&!p.fixed.today,'major tab starts with rest and fixed OFF');
values.set('BossTimer.webRest.tabs.v1',JSON.stringify({all:false,custom:false,today:true}));
values.set('BossTimer.webFixed.tabs.v1',JSON.stringify({all:false,custom:false,today:true}));
p=prefs.load();
check(!p.rest.all&&!p.rest.custom&&p.rest.today&&!p.fixed.all&&!p.fixed.custom&&p.fixed.today,'saved tab filters override release defaults');
const alarms=schedule.slice(schedule.indexOf('function readAlarms('),schedule.indexOf('function saveAlarms('));
const readAlarm=new Function('localStorage',`let alarmPrefs,alarmScope='alarm';${alarms}\nreadAlarms();return alarmPrefs;`);
check(readAlarm(localStorage).defaultOn===true,'all boss alarms default ON');
values.set('alarm',JSON.stringify({defaultOn:false,overrides:{}}));
check(readAlarm(localStorage).defaultOn===false,'saved master alarm OFF is preserved');
const push=schedule.slice(schedule.indexOf('async function initPush('),schedule.indexOf('async function togglePush('));
async function init(permission,saved){
  let requests=0,prepared=0,noted='';const storage={getItem:()=>saved};
  const api=new Function('localStorage','Notification','deviceProfile','WebNotices','data',`
    let pushInitialized=false,pushInitBusy=false,pushEnabled=false;
    const PUSH_ENABLED_KEY='key',paintPush=message=>{if(message)noted=message;},pushUnavailableReason=()=>'';
    let requests=0,prepared=0,noted='';
    const pushRequest=async()=>{requests++;return {ready:true,subscribed:false};};
    const preparePushSubscription=async()=>{prepared++;},syncPush=async()=>{};
    ${push}\nreturn async()=>{await initPush();return {requests,prepared,enabled:pushEnabled,noted};};
  `)(storage,{permission},()=>({pc:false}),{},{});
  return api();
}
let phone=await init('granted',null);
check(phone.enabled&&phone.prepared===1,'already-authorized phones connect with default ON');
phone=await init('default',null);
check(!phone.enabled&&phone.requests===0&&phone.noted.includes('허용'),'new phones require permission without unsolicited requests');
phone=await init('granted','false');
check(!phone.enabled&&phone.requests===0,'saved phone push OFF remains OFF');
return checks;

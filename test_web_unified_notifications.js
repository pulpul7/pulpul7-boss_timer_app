/* Offline notification delivery checks. Parameters: notices, push. Never sends. */
const checks=[];function check(v,m){if(!v)throw Error(m);checks.push(m);}
function pcFixture(prefs={defaultOn:false,overrides:{}},legacy=false) {
  const elements=new Map(),values=new Map(),events={},timers=new Map();let serial=0,clock=100000;
  class El {
    constructor(){this.children=[];this.options=[];this.handlers={};}
    append(...nodes){for(const n of nodes){n.parentNode=this;this.children.push(n);this.options.push(n);}}
    get firstElementChild(){return this.children[0];}
    setAttribute(k,v){this[k]=v;}
    addEventListener(k,v){this.handlers[k]=v;}
    replaceChildren(...nodes){this.children=[];this.append(...nodes);}
    remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(n=>n!==this);}
  }
  const document={body:new El(),getElementById:id=>{if(!elements.has(id))elements.set(id,new El());return elements.get(id);},createElement:()=>new El()};
  const data={server_name:'s',guild_name:'g',season:'18',schedule_stale:false,
    events:[{id:'a',name:'A',kind:'boss',at:500000,scheduled_at:'2026-10-16T12:00:00+09:00'},
      {id:'b',name:'B',kind:'boss',at:500000,scheduled_at:'2026-10-16T12:00:00+09:00'}]};
  values.set('BossTimer.webNotice.v1:'+JSON.stringify(['owner','s','g','18']),JSON.stringify({times:[2,null,null],banner:legacy}));
  const localStorage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)};
  const factory=new Function('document','window','localStorage','data','initial','setTimeout','clearTimeout','performance',`
    let alarmPrefs=initial,leaving=false;
    const deviceProfile=()=>({pc:true}),isBoss=r=>r.kind==='boss',displayName=r=>r.name;
    const alarmOn=r=>alarmPrefs.overrides['boss:'+r.name] ?? alarmPrefs.defaultOn;
    const toast=()=>{},schedulePushSync=()=>{},WebNoticeSound={refresh:async()=>{},notify:async()=>{},stop(){}};
    ${notices}\nreturn {api:WebNotices,setPrefs:p=>{alarmPrefs=p;WebNotices.invalidate();},prefs:()=>alarmPrefs};`);
  const out=factory(document,{addEventListener:(n,f)=>events[n]=f},localStorage,data,prefs,
    (fn,delay)=>{const id=++serial;timers.set(id,{fn,delay});return id;},id=>timers.delete(id),{now:()=>clock});
  out.api.refresh('owner',true);
  return {...out,elements,events,values,cards:()=>elements.get('web-banner-stack')?.children || []};
}
const pc=pcFixture();
check(pc.elements.get('push-toggle').hidden&&pc.elements.get('push-test').textContent.length>2,'PC hides the redundant switch and labels the test');
pc.api.tick(380000);check(pc.cards().length===0,'PC all OFF delivers no scheduled messages');
pc.api.test();check(pc.cards().length===1,'PC test delivers immediately with no selected bosses');
check(!pc.prefs().defaultOn&&Object.keys(pc.prefs().overrides).length===0,'PC test leaves boss selection OFF');
pc.api.tick(380000);check(pc.cards().length===1,'a test does not turn real boss messages back on');
pc.api.test();check(pc.cards().length===1,'repeated PC tests keep their cooldown');
const legacy=pcFixture({defaultOn:true,overrides:{'boss:A':false}},false);
legacy.api.tick(380000);check(legacy.cards().length===1&&legacy.cards()[0].children[2].textContent==='B','old banner OFF no longer blocks a selected boss, and unchecked bosses stay excluded');
legacy.setPrefs({defaultOn:false,overrides:{}});legacy.api.tick(380000);check(legacy.cards().length===1,'PC OFF immediately invalidates cached notification groups');
const cross=pcFixture({defaultOn:true,overrides:{}});
const scope='BossTimer.webNotice.v1:'+JSON.stringify(['owner','s','g','18']);
cross.events.storage({key:scope,newValue:JSON.stringify({times:[2,null,null],banner:false})});cross.api.tick(380000);
check(cross.cards().length===1,'legacy transport changes in another tab cannot disable selected-boss delivery');
function phoneFixture(options={}) {
  const elements=new Map(),values=new Map(),timers=new Map(),calls={get:0,permission:0,prepare:[],posts:[],notes:[]};
  let serial=0,clock=100000,failures=options.failPosts||0,testCount=0;
  const element=id=>{if(!elements.has(id))elements.set(id,{setAttribute(k,v){this[k]=v;},textContent:'',hidden:false});return elements.get(id);};
  const notification={permission:options.permission||'granted',requestPermission(){calls.permission++;this.permission=options.grant||'granted';return Promise.resolve(this.permission);}};
  if(options.legacyOff)values.set('BossTimer.webPush.enabled.v1','false');
  const factory=new Function('element','Notification','localStorage','performance','setTimeout','clearTimeout','options','calls','getFailure','setFailure',`
    let data={server_name:'s',guild_name:'g',season:'18',events:[{kind:'boss',name:'A'},{kind:'boss',name:'B'}]},leaving=false,alarmScope='alarms';
    let alarmPrefs={defaultOn:false,overrides:{}},selectedFilter='all';
    const mainEvents=()=>data.events.filter(r=>r.name==='B'),refreshAlarms=()=>{};
    const deviceProfile=()=>({pc:false,phone:true}),isBoss=r=>r.kind==='boss',dayKey=()=> '2026-10-16';
    const setText=(el,text)=>el.textContent=text,toast=()=>{},serverNow=()=>0;
    const WebNotices={times:()=>[2,null,null],invalidate(){},paint(){},test(){}};
    ${push}
    const sub={toJSON:()=>({endpoint:'https://fcm.googleapis.com/fcm/send/unit',keys:{}})};
    const registration=()=>({pushManager:{getSubscription:async()=>sub}});
    pushUnavailableReason=()=>options.unavailable || '';
    showPushGuide=message=>calls.notes.push(message);
    preparePushSubscription=async(config,renew)=>{calls.prepare.push(renew);pushRegistration=registration();return sub;};
    pushRequest=async(method,payload)=>{
      if(method==='GET'){calls.get++;await options.getGate;return {ready:true,subscribed:!options.missingRecord};}
      calls.posts.push(JSON.parse(JSON.stringify(payload)));
      if(payload.action==='test'){calls.tests=(calls.tests||0)+1;return options.renewTest&&calls.tests===1?{renew:true}:{ok:true,message:'test sent'};}
      if(payload.prefs)calls.remotePrefs=JSON.parse(JSON.stringify(payload.prefs));
      await options.postGate;
      if(getFailure()>0){setFailure(getFailure()-1);throw Error('temporary');}
      return {ok:true};
    };
    return {initPush,enablePush,testPush,syncPush,schedulePushSync,save:saveAlarms,
      setPrefs:p=>alarmPrefs=JSON.parse(JSON.stringify(p)),toggle:toggleMasterAlarms,alarmOn,filter:v=>selectedFilter=v,
      ready(){pushEnabled=true;pushRegistration=registration();},
      device:pushDeviceId,state:()=>({enabled:pushEnabled,prefs:alarmPrefs,busy:pushBusy,initBusy:pushInitBusy,signature:pushSyncedSignature})};`);
  const api=factory(element,notification,{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)},
    {now:()=>clock},(fn,delay)=>{const id=++serial;timers.set(id,{fn,delay});return id;},id=>timers.delete(id),options,calls,()=>failures,n=>failures=n);
  return {api,calls,elements,timers,values,
    async flush(){const entry=[...timers].sort((a,b)=>a[1].delay-b[1].delay)[0];if(!entry)return;timers.delete(entry[0]);clock+=Math.max(0,entry[1].delay);await entry[1].fn();},
    fail:n=>failures=n};
}
const phone=phoneFixture();await phone.api.initPush();
check(phone.calls.get===0&&!phone.elements.get('push-controls').hidden&&!phone.elements.get('push-test').disabled,'phone with no alarms still exposes an enabled test button');
await phone.api.testPush();
check(phone.calls.get===1&&phone.calls.prepare.length===1&&phone.calls.posts.filter(p=>p.action==='test').length===1,'phone test prepares delivery and immediately sends without any selected bosses');
check(phone.api.state().enabled&&!phone.api.state().prefs.defaultOn&&phone.calls.posts[0].prefs.defaultOn===false,'test enables only delivery and preserves all OFF server preferences');
await phone.api.testPush();check(phone.calls.posts.filter(p=>p.action==='test').length===1,'phone test cooldown prevents duplicate requests');
phone.api.setPrefs({defaultOn:false,overrides:{'boss:A':true}});phone.api.save();await phone.flush();
check(phone.calls.posts.at(-1).prefs.overrides['boss:A']===true&&phone.calls.prepare.length===1,'selecting a boss changes message preferences without resubscribing');
phone.api.setPrefs({defaultOn:false,overrides:{}});phone.api.save();await phone.flush();
check(phone.calls.posts.at(-1).prefs.defaultOn===false&&phone.api.state().enabled&&phone.calls.prepare.length===1,'all OFF keeps delivery registered but sends zero selected bosses to the server');
const auto=phoneFixture({legacyOff:true});auto.api.setPrefs({defaultOn:false,overrides:{'boss:B':true}});await auto.api.initPush();
check(auto.api.state().enabled&&auto.calls.permission===0&&auto.calls.posts[0].prefs.overrides['boss:B'],'a selected boss automatically readies an already-authorized phone despite legacy OFF');
const relogin=phoneFixture({missingRecord:true});relogin.api.setPrefs({defaultOn:true,overrides:{}});await relogin.api.initPush();
check(relogin.calls.prepare[0]===false,'a new login record does not unnecessarily replace the existing browser subscription');
check(relogin.api.device()===relogin.calls.posts[0].device&&/^[a-f0-9]{32}$/.test(relogin.api.device()),'push updates carry the persistent browser device identifier');
check(relogin.values.get('BossTimer.webPush.device.v1')===relogin.api.device(),'device identity survives cookie and login changes');
const gesture=phoneFixture({permission:'default'});const requested=gesture.api.testPush();
check(gesture.calls.permission===1,'permission is requested synchronously inside the test gesture');await requested;
check(gesture.calls.posts.filter(p=>p.action==='test').length===1,'permission approval continues directly to test delivery');
const denied=phoneFixture({permission:'default',grant:'denied'});await denied.api.testPush();
check(denied.calls.get===0&&denied.calls.posts.length===0&&denied.calls.notes.length===1,'denied permission shows guidance without attempting a push');
const unsupported=phoneFixture({unavailable:'install home-screen app'});await unsupported.api.testPush();
check(unsupported.calls.notes.length===1&&unsupported.calls.posts.length===0,'iPhone setup guidance stays available through the test button');
let release;const gate=new Promise(resolve=>release=resolve);const concurrent=phoneFixture({getGate:gate});
const one=concurrent.api.enablePush(true,true),two=concurrent.api.enablePush(true,true);await Promise.resolve();release();await Promise.all([one,two]);
check(concurrent.calls.get===1&&concurrent.calls.prepare.length===1&&concurrent.calls.posts.length===1,'concurrent setup operations share one registration');
const double=phoneFixture();await Promise.all([double.api.testPush(),double.api.testPush()]);
check(double.calls.posts.filter(p=>p.action==='test').length===1,'double-click cannot create two test pushes');
const expired=phoneFixture({renewTest:true});await expired.api.testPush();
check(expired.calls.prepare.length===2&&expired.calls.prepare[1]===true&&expired.calls.posts.filter(p=>p.action==='test').length===2,'one expired subscription is repaired and the same test is retried once');
const rapid=phoneFixture();rapid.api.ready();
rapid.api.setPrefs({defaultOn:true,overrides:{}});rapid.api.schedulePushSync(true);
rapid.api.setPrefs({defaultOn:false,overrides:{'boss:B':true}});rapid.api.schedulePushSync(true);
check(rapid.timers.size===1,'rapid changes coalesce into one pending update');await rapid.flush();
check(rapid.calls.posts.length===1&&rapid.calls.posts[0].prefs.overrides['boss:B']===true,'coalesced update carries the final boss selection');
const retry=phoneFixture({failPosts:3});retry.api.ready();await retry.api.syncPush();await retry.flush();await retry.flush();
check(retry.calls.posts.length===3&&retry.timers.size===0,'failed preference updates retry only twice with backoff');
const uncertain=phoneFixture();uncertain.api.ready();await uncertain.api.syncPush();
uncertain.api.setPrefs({defaultOn:false,overrides:{'boss:A':true}});uncertain.fail(1);await uncertain.api.syncPush();
check(uncertain.calls.remotePrefs.overrides['boss:A']===true,'fixture models a server-accepted ON whose response is lost');
uncertain.api.setPrefs({defaultOn:false,overrides:{}});uncertain.api.save();await uncertain.flush();
check(uncertain.calls.remotePrefs.defaultOn===false&&!uncertain.calls.remotePrefs.overrides['boss:A'],
  'OFF is sent even if an earlier lost ON response left the cached signature at OFF');
let finishPost;const postGate=new Promise(resolve=>finishPost=resolve);
const pendingOn=phoneFixture({postGate,failPosts:1});pendingOn.api.setPrefs({defaultOn:false,overrides:{'boss:A':true}});
const firstEnable=pendingOn.api.enablePush(true,true);
for(let i=0;i<20&&!pendingOn.calls.remotePrefs;i++)await Promise.resolve();
check(pendingOn.calls.remotePrefs?.overrides['boss:A']===true,'initial registration can be accepted before its response');
pendingOn.api.setPrefs({defaultOn:false,overrides:{}});pendingOn.api.save();finishPost();await firstEnable;await pendingOn.flush();
check(pendingOn.calls.remotePrefs.defaultOn===false&&!pendingOn.calls.remotePrefs.overrides['boss:A'],
  'OFF during initial ON registration is retried after an uncertain response');
const master=phoneFixture();master.api.ready();master.api.setPrefs({defaultOn:true,overrides:{}});
master.api.toggle(false);await master.flush();
check(!master.api.alarmOn({kind:'boss',name:'A'})&&!master.api.alarmOn({kind:'boss',name:'B'})&&master.calls.posts.at(-1).prefs.defaultOn===false,
  'actual master OFF disables every boss and propagates the same message filter');
master.api.filter('today');master.api.toggle(true);await master.flush();
check(!master.api.alarmOn({kind:'boss',name:'A'})&&master.api.alarmOn({kind:'boss',name:'B'}),
  'major master ON changes only matching dated bosses without enabling the other bosses');
master.api.filter('custom');master.api.toggle(false);await master.flush();
check(!master.api.alarmOn({kind:'boss',name:'B'})&&master.api.state().enabled&&master.calls.prepare.length===0,
  'custom master OFF changes message selection without tearing down delivery');
const bossGesture=phoneFixture({permission:'default'});bossGesture.api.setPrefs({defaultOn:false,overrides:{'boss:A':true}});bossGesture.api.save();
check(bossGesture.calls.permission===1,'switching a boss ON requests permission in the same user gesture');
await bossGesture.api.enablePush(true,true);
check(bossGesture.api.state().enabled&&bossGesture.calls.prepare.length===1,'boss selection and concurrent setup share one delivery registration');
return checks;

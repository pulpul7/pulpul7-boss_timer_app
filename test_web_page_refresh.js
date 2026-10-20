/* Run the production refresh module with fake lifecycle, storage and timers. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
const module=source.split('// BEGIN WEB PAGE REFRESH')[1].split('// END WEB PAGE REFRESH')[0];
function fixture({token='a'.repeat(32),version='b'.repeat(64),saved=null}={}){
  let serial=0,dirty=false,saves=0;const timers=new Map(),events={},navigations=[],scrolls=[],storage=new Map();
  if(saved)storage.set('BossTimer.webRefreshView.v1',JSON.stringify(saved));
  const document={hidden:false,documentElement:{dataset:{webRefreshToken:token,webPageVersion:version}},
    addEventListener:(type,fn)=>events[type]=fn};
  const window={scrollY:42,location:{replace:path=>navigations.push(path)},scrollTo:(...args)=>scrolls.push(args),
    addEventListener:(type,fn)=>events[type]=fn};
  const sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
  const api=new Function('document','window','sessionStorage','setTimeout','clearTimeout','customDirty','saveCustomSelection',
    `let selectedFilter='custom',allDay='2026-10-19';function selectFilter(v){selectedFilter=v;allDay='';}
     ${module};return {...WebPageRefresh,view:()=>({filter:selectedFilter,day:allDay})};`)(document,window,sessionStorage,
      (fn,delay)=>{timers.set(++serial,{fn,delay});return serial;},id=>timers.delete(id),()=>dirty,()=>{saves++;throw Error('refresh must not save selections');});
  return {api,document,events,navigations,storage,scrolls,timers,
    dirty(){dirty=true;},saved(){dirty=false;},saves:()=>saves,
    fire(){const jobs=[...timers];timers.clear();for(const [,job]of jobs)job.fn();}};
}
const same=fixture();same.api.observe({web_refresh_token:'a'.repeat(32),web_page_version:'b'.repeat(64)});
check(same.timers.size===0,'unchanged state causes no timer, reload or extra network request');
same.api.observe({web_refresh_token:'c'.repeat(32)});same.api.observe({web_refresh_token:'c'.repeat(32)});
check(same.timers.size===1,'one signal schedules only one automatic navigation');same.fire();
check(same.navigations.join(',')==='/'&&same.storage.size===1,'signal navigates through the server queue and saves the current view');
same.api.observe({web_refresh_token:'d'.repeat(32)});same.fire();
check(same.navigations.length===1,'overlapping signals cannot trigger duplicate navigation');
const files=fixture();files.api.observe({web_page_version:'c'.repeat(64)});files.fire();
check(files.navigations.length===1,'changed asset version reloads without a Discord command');
const hidden=fixture();hidden.document.hidden=true;hidden.api.observe({web_refresh_token:'c'.repeat(32)});
check(hidden.timers.size===0&&hidden.navigations.length===0,'hidden pages do not wake just to refresh');
hidden.document.hidden=false;hidden.events.visibilitychange();
check(hidden.navigations.length===1,'returning to a hidden page applies its pending refresh');
const bad=fixture();bad.api.observe({web_refresh_token:'bad',web_page_version:'wrong'});
check(bad.timers.size===0,'malformed signals never navigate');
const legacy=fixture({token:'',version:''});legacy.api.observe({web_refresh_token:'a'.repeat(32),web_page_version:'b'.repeat(64)});
check(legacy.timers.size===0,'missing bootstrap state establishes a baseline rather than a reload loop');
const changed=fixture();changed.dirty();changed.api.observe({web_refresh_token:'c'.repeat(32)});changed.fire();
check(changed.navigations.length===0&&changed.saves()===0,'unsaved selection defers navigation without writing it');
changed.api.observe({web_refresh_token:'c'.repeat(32)});changed.fire();
check(changed.navigations.length===0&&changed.saves()===0,'repeated refresh cannot implicitly commit selections');
changed.saved();changed.api.observe({web_refresh_token:'c'.repeat(32)});changed.fire();
check(changed.navigations.length===1&&changed.saves()===0,'manual save or undo permits deferred refresh');
const restore=fixture({saved:{at:Date.now(),filter:'custom',day:'2026-10-20',scroll:120}});restore.api.restore();restore.fire();
check(restore.api.view().day==='2026-10-20'&&restore.scrolls[0][1]===120&&restore.storage.size===0,'automatic refresh restores tab/date/scroll once');
const stale=fixture({saved:{at:Date.now()-400000,filter:'all',scroll:999}});stale.api.restore();stale.fire();
check(stale.scrolls.length===0,'stale restoration state is discarded');
return checks;

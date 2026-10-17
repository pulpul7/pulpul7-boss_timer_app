/* Shared production countdown with wall-clock and page lifecycle simulation. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
function fixture(seconds=5,ready=true){
 let now=0,serial=0;const timers=new Map(),events={},navigations=[],count={textContent:String(seconds)};
 const document={hidden:false,documentElement:{dataset:{retrySeconds:String(seconds)}},getElementById:()=>ready?count:null,
  addEventListener:(type,fn)=>events[type]=fn};
 const window={location:{replace:path=>navigations.push(path)},addEventListener:(type,fn)=>events[type]=fn};
 new Function('document','window','Date','setTimeout','clearTimeout',script)(document,window,
  {now:()=>now},(fn,delay)=>{timers.set(++serial,{fn,at:now+delay});return serial;},id=>timers.delete(id));
 return {count,timers,events,document,navigations,ready(){ready=true;events.DOMContentLoaded();},advance(target){
  while(true){const next=[...timers].filter(([,t])=>t.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];
   if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].fn();}now=target;
 }};
}
const f=fixture();
check(f.count.textContent==='5'&&f.navigations.length===0,'five-second wait starts without requesting the page');
f.events.pageshow();check(f.timers.size===1,'initial pageshow reuses one countdown timer');
f.advance(1000);check(f.count.textContent==='4','remaining seconds visibly decrease');
f.advance(4999);check(f.count.textContent==='1'&&f.navigations.length===0,'automatic navigation never happens before the deadline');
f.advance(5000);check(f.count.textContent==='0'&&f.navigations.join(',')==='/'&&f.timers.size===0,'deadline opens the main page once without a manual link');
f.events.pageshow();f.events.visibilitychange();
check(f.navigations.length===1,'lifecycle events cannot send duplicate automatic reloads');
const short=fixture(2);short.advance(2000);
check(short.navigations.length===1,'actual remaining Retry-After time is used instead of starting five more seconds');
const hidden=fixture();hidden.advance(2000);hidden.document.hidden=true;hidden.events.visibilitychange();
check(hidden.timers.size===0,'hidden wait page removes its countdown timer');
hidden.advance(10000);hidden.document.hidden=false;hidden.events.visibilitychange();
check(hidden.count.textContent==='0'&&hidden.navigations.length===1,'returning after suspension uses elapsed time and opens immediately');
const restored=fixture();restored.events.pagehide();check(restored.timers.size===0,'leaving cancels the timer');
restored.advance(6000);restored.events.pageshow();
check(restored.navigations.length===1,'restored wait page completes an already elapsed deadline');
const loading=fixture(5,false);loading.advance(3400);loading.ready();
check(loading.count.textContent==='2','late-rendered content shows actual remaining time rather than restarting at five');
loading.advance(4000);
check(loading.count.textContent==='1','a partial-second DOM-ready event stays aligned to the real second boundary');
loading.advance(5000);
check(loading.navigations.length===1,'slow stylesheet/DOM loading does not add another wait after rendering');
const verySlow=fixture(5,false);verySlow.advance(8000);
check(verySlow.navigations.length===1,'deadline can finish even before waiting-page content is ready');
return checks;

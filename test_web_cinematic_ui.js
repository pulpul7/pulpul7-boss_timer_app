/* Runtime skin recovery and caption gating without media or application state. */
const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
function fixture(){
 const links=[],timers=new Map();let serial=0;
 const api=new Function('document','setTimeout','clearTimeout',`
  let skin=null,skinLoading=false,skinAttempts=0,skinTimer=0,controlsReady=false,enabled=true;
  let at=1000,paints=0,pumps=0,opens=0;
  const now=()=>at,arrival={hidden:true},video={paused:true,hidden:true},scheduledRow={at:10000};
  const transport={},hero={isConnected:true,dataset:{},nextElementSibling:transport,after(node){this.nextElementSibling=node;node.isConnected=true;}};
  const paintTransport=()=>paints++,pump=()=>pumps++,setTransportOpen=()=>opens++;
  ${source.slice(source.indexOf('function paintArrival('),source.indexOf('function mount('))}
  return {load:loadSkin,paint:paintArrival,restore:restoreTransport,arrival,hero,transport,
   state:s=>hero.dataset.cinematic=s,time:v=>at=v,enabled:v=>enabled=v,
   ready:()=>controlsReady,counts:()=>({paints,pumps,opens,skinAttempts}),pending:()=>skinTimer};
 `)({createElement(){return {events:{},addEventListener(type,fn){this.events[type]=fn;},remove(){this.removed=true;}};},head:{append(link){links.push(link);}}},(fn,ms)=>{timers.set(++serial,{fn,ms});return serial;},id=>timers.delete(id));
 return{api,links,timers,run(){const[id,t]=timers.entries().next().value;timers.delete(id);t.fn();}};
}
const f=fixture();f.api.load();check(f.links.length===1&&!f.api.ready(),'one initial companion CSS request');f.api.load();check(f.links.length===1,'pending style load is not duplicated');
f.api.state('playing');f.api.paint();check(f.api.arrival.hidden,'caption stays hidden before CSS is ready');
f.links[0].events.error();check(f.links[0].removed&&f.timers.size===1&&[...f.timers.values()][0].ms===1000,'first style error schedules bounded one-second retry');f.run();f.links[1].events.error();check([...f.timers.values()][0].ms===2000,'second error backs off two seconds');f.run();f.links[2].events.error();check(f.timers.size===0&&f.api.counts().skinAttempts===3,'three failures stop without polling');f.api.load();check(f.links.length===3,'ordinary updates do not restart exhausted retries');
f.api.load(true);check(f.links.length===4,'explicit theme reentry can retry after failure');f.api.state(undefined);f.links[3].events.load();check(f.api.ready()&&f.api.counts().paints===1&&f.api.counts().pumps===1,'successful style load restores controls and resumes normal pump');check(f.api.arrival.hidden,'future boss alone never exposes caption');
check(f.timers.size===0&&!f.api.pending(),'successful load clears watchdog without background polling');
for(const state of ['paused','still',undefined]){f.api.state(state);f.api.paint();check(f.api.arrival.hidden,'caption hidden outside active playback '+state);}
f.api.state('playing');f.api.paint();check(!f.api.arrival.hidden,'caption appears only in active styled playback');f.api.time(10000);f.api.paint();check(f.api.arrival.hidden,'caption hides at boss spawn');f.api.time(1000);f.api.enabled(false);f.api.paint();check(f.api.arrival.hidden,'other theme hides caption');
f.api.hero.nextElementSibling=null;f.api.transport.isConnected=false;f.api.restore();check(f.api.hero.nextElementSibling===f.api.transport&&f.api.transport.isConnected,'detached transport restored after existing hero');f.api.load(true);check(f.links.length===4,'ready style is reused after remount');
const disabled=fixture();disabled.api.load();disabled.links[0].events.error();disabled.api.enabled(false);disabled.run();check(disabled.links.length===1&&disabled.timers.size===0,'leaving theme stops retry chain');disabled.api.enabled(true);disabled.api.load(true);check(disabled.links.length===2,'returning to theme resumes failed style loading');
const hung=fixture();hung.api.load();check([...hung.timers.values()][0].ms===5000,'hung stylesheet has a five-second deadline');hung.run();check(hung.links[0].removed&&hung.timers.size===1,'hung request enters the same bounded retry path');hung.links[0].events.load();check(!hung.api.ready(),'late load from timed-out link cannot mark controls ready');
check(source.includes('arrival.hidden=true;'),'caption is hidden before it is appended');
return checks;

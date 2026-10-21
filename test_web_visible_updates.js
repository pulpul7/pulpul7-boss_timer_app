/* Actual production visibility and clock functions; no DOM/network/browser. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
const rowCode=source.slice(source.indexOf('let timingVisibilityObserver='),source.indexOf('function renderRows('));
function rowFixture(observe=true) {
  let callback,now=1000,descriptions=0,layouts=0;
  const rows=Array.from({length:1000},(_,i)=>({row:{id:String(i),at:0,kind:'boss'},
    item:{isConnected:true,classList:{toggle(){}},getBoundingClientRect(){layouts++;throw Error('tick layout read');}},state:{textContent:''}}));
  const hooks={time:()=>now,describe:()=>{descriptions++;return String(now);}};
  const window=observe?{IntersectionObserver:class {
    constructor(fn,options){callback=fn;this.options=options;}
    disconnect(){}observe(){}
  }}:{};
  const document={hidden:false};
  const api=new Function('window','document','hooks','timingRows',`
    const data={},serverNow=hooks.time,describe=hooks.describe,preciseBossIds=()=>new Set();
    const isOngoingRest=()=>false,isBoss=()=>true,bossFinished=()=>false,spawnBlink=()=>{},withinHour=()=>false;
    const setText=(target,value)=>{if(target.textContent!==value)target.textContent=value;};
    ${rowCode}
    return {watch:observeTimingRows,paint:()=>{for(const row of timingRows)if(row.visible!==false)paintTimingRow(row,serverNow(),new Set());},
      replace:rows=>timingRows=rows};`)(window,document,hooks,rows);
  api.watch();
  return {api,rows,document,time:t=>now=t,count:()=>descriptions,layouts:()=>layouts,
    signal:entries=>callback(entries.map(([index,visible])=>({target:rows[index].item,isIntersecting:visible})))};
}
const view=rowFixture();
view.api.paint();check(view.count()===1000,'new rows receive an initial correct timestamp before visibility arrives');
view.signal(view.rows.map((_,i)=>[i,i<10]));
const baseline=view.count();
for(let i=0;i<60;i++){view.time(2000+i*1000);view.api.paint();}
check(view.count()-baseline===600,'one minute updates ten visible rows 600 times instead of all 1000 rows 60000 times');
check(view.layouts()===0,'visibility tracking introduces no per-second bounding-box reads');
view.time(90000);view.signal([[900,true]]);
check(view.rows[900].state.textContent==='(90000)','scrolling a previously hidden row into view immediately catches up its time');
const before=view.count();view.document.hidden=true;view.signal([[901,true]]);
check(view.count()===before,'observer callbacks never repaint while the document is hidden');
view.document.hidden=false;view.api.replace([]);view.api.watch();view.signal([[0,true]]);
check(view.count()===before,'late observer callbacks for removed schedule rows are ignored');
const legacy=rowFixture(false);legacy.api.paint();legacy.time(5000);legacy.api.paint();
check(legacy.count()===2000&&legacy.rows[999].state.textContent==='(5000)','browsers without IntersectionObserver retain complete normal updates');

const clockCode=source.slice(source.indexOf('let scheduleClockTimer='),source.indexOf('function tick()'));
let stamp=0,serial=0,ticks=0;const timers=new Map(),document={hidden:false};
const clock=new Function('document','setTimeout','clearTimeout','tick',`let leaving=false;${clockCode}
  return {start:startScheduleClock,stop:stopScheduleClock,leave:()=>leaving=true};`)(document,
  (fn,delay)=>{timers.set(++serial,{fn,at:stamp+delay});return serial;},id=>timers.delete(id),()=>ticks++);
function advance(time){while(true){const item=[...timers].filter(([,v])=>v.at<=time).sort((a,b)=>a[1].at-b[1].at)[0];
  if(!item)break;stamp=item[1].at;timers.delete(item[0]);item[1].fn();}stamp=time;}
clock.start();clock.start();check(timers.size===1,'repeated starts keep one schedule clock timer');
advance(60000);check(ticks===60,'visible schedule retains its one-second clock cadence');
document.hidden=true;clock.stop();advance(660000);clock.start();
check(ticks===60&&timers.size===0,'ten hidden minutes have no clock callbacks or wakeups');
document.hidden=false;clock.start();clock.start();advance(661000);
check(ticks===61&&timers.size===1,'resuming starts only one clock without catch-up bursts');
clock.leave();advance(662000);check(timers.size===0,'leaving the page never perpetuates the clock');
// Retained menus keep their ordinary clock/connection updates.
const upperCode=source.slice(source.indexOf("setText(element('clock'),clock(now));",source.indexOf("element('overview').hidden=",source.indexOf('function tick()'))),source.indexOf('const preciseIds=preciseBossIds(now);'));
const upperNodes=new Map();let upperHidden=true,upperWrites=0,dayRefreshes=0,idleWrites=0;
const upperElement=id=>{if(!upperNodes.has(id))upperNodes.set(id,{id,textContent:'',hidden:false});return upperNodes.get(id);};
const upperDocument={documentElement:{classList:{contains:()=>upperHidden}}};
const upper=new Function('document','element','setText','refreshDays',`
  let data={},connected=true,lastContact=0,pollInterval=15000,selectedFilter='all';
  const performance={now:()=>0},clock=String,dateLabel=String,dayStart=x=>x,selectedDay=x=>x,dayKey=x=>x;
  ${source.slice(source.indexOf('function updateConnection()'),source.indexOf('let scheduleClockTimer='))}
  return now=>{${upperCode}updateConnection();};
`)(upperDocument,upperElement,(n,v)=>{if(n.id==='idle-clock')idleWrites++;else upperWrites++;n.textContent=v;},()=>dayRefreshes++);
for(let i=0;i<600;i++)upper(i*1000);
check(upperWrites>0&&upperNodes.has('connection'),'retained menus keep their normal clock/date/connection writes');
check(idleWrites===0&&!upperNodes.has('idle-clock'),'removed compact clock performs no folded writes');
check(dayRefreshes===600,'bottom date navigation remains independent of hidden upper menus');
upperHidden=false;upper(600000);
check(upperNodes.get('clock').textContent==='600000'&&upperNodes.get('connection').textContent.length>0,'restored upper menu immediately shows current time and connection');
check(idleWrites===0,'removed compact clock performs no visible writes');
return checks;

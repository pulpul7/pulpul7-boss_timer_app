/* Offline work-budget and cancellation checks against production functions. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
function section(start,end){return source.slice(source.indexOf(start),source.indexOf(end));}

function snowFixture(width=390){
  let serial=0,paints=0,clock=0,callbacks=0;const frames=new Map(),timers=new Map();
  const pen={setTransform(){},clearRect(){paints++;},drawImage(){}};
  const canvas={hidden:false,width:0,height:0};
  const document={hidden:false},window={innerWidth:width,innerHeight:844,devicePixelRatio:3};
  const api=new Function('window','document','element','requestAnimationFrame','cancelAnimationFrame','setTimeout','clearTimeout',
    section('const winterSnow=','const rainGlass=')+`;winterSnow.canvas=element();winterSnow.context=pen;
      return {resize:resizeWinterSnow,start:()=>syncWinterSnow(true),stop:()=>syncWinterSnow(false),state:winterSnow};`
      .replace('winterSnow.context=pen','winterSnow.context=element().pen'))(
    window,document,()=>Object.assign(canvas,{pen}),fn=>{frames.set(++serial,fn);return serial;},id=>frames.delete(id),
    (fn,delay)=>{timers.set(++serial,{fn,at:clock+delay});return serial;},id=>timers.delete(id));
  api.resize();api.start();
  return {api,window,document,frames,timers,canvas,paints:()=>paints,callbacks:()=>callbacks,
    step(t){clock=t;for(const [id,timer] of [...timers])if(timer.at<=clock){timers.delete(id);timer.fn();}
      const item=frames.entries().next().value;if(item){frames.delete(item[0]);callbacks++;item[1](t);}}};
}
const snow=snowFixture(),snowPC=snowFixture(1280);
check(snow.canvas.width===390&&snow.canvas.height===844,'phone snow uses one canvas pixel per CSS pixel even at DPR 3');
const flakes=snow.api.state.flakes.length;
for(let i=0;i<240;i++){snow.step(1000+i*1000/120);snowPC.step(1000+i*1000/120);}
check(snow.paints()<=48&&snow.paints()>=35,'phone snow stays within 24fps on a 120Hz screen');
check(snowPC.paints()<=60&&snowPC.paints()>=45,'desktop snow retains a 30fps cap');
check(snow.callbacks()<=48&&snow.callbacks()===snow.paints(),'snow sleeps between useful frames instead of running 240 callbacks');
check(snow.api.state.flakes.length===flakes&&flakes===32&&snowPC.api.state.flakes.length===69,
  'phone and desktop snow counts are reduced by 30 percent with nearest-integer rounding');
snow.document.hidden=true;snow.step(4000);
check(snow.frames.size===0&&snow.timers.size===0&&snow.canvas.hidden,'background snow cancels frames and timers and hides its canvas');
snow.document.hidden=false;snow.api.start();snow.api.start();
check(snow.frames.size===1&&!snow.canvas.hidden,'returning to winter starts only one frame loop');
snow.api.stop();check(snow.frames.size===0&&snow.timers.size===0,'leaving winter cancels its frame loop and timer');

let stamp=0,nextTimer=0,prepares=0,spawns=0,removals=0;
const timers=new Map(),foreground={hidden:true};
const rain=new Function('element','performance','document','setTimeout','clearTimeout',
  `const rainForeground={active:true,failed:false,timer:0,url:'blob:cached',drops:new Set(),pointers:new Set(),touches:0,idleUntil:4000};
   const prepareRainForeground=()=>hooks.prepare(),addRainForegroundDrops=()=>hooks.spawn();
   ${section('function noteRainForegroundInput(', 'function prepareRainForeground(')}
   ${section('function resumeRainForeground(', 'function deviceProfile(')}
   return {input:noteRainForegroundInput,resume:resumeRainForeground,stop:stopRainForeground,state:rainForeground};`
   .replace('const prepareRainForeground=', 'const hooks=element().hooks;const prepareRainForeground='))(
  ()=>Object.assign(foreground,{hooks:{prepare(){prepares++;},spawn(){spawns++;}}}),{now:()=>stamp},{hidden:false},
  (fn,delay)=>{timers.set(++nextTimer,{fn,at:stamp+delay});return nextTimer;},id=>timers.delete(id));
function runUntil(time){while(true){const entry=[...timers].filter(([,v])=>v.at<=time).sort((a,b)=>a[1].at-b[1].at)[0];if(!entry)break;
  stamp=entry[1].at;timers.delete(entry[0]);entry[1].fn();}stamp=time;}
rain.resume();const timerCount=nextTimer;
for(let i=0;i<200;i++){stamp=i*10;rain.input({type:'scroll'});}
check(nextTimer===timerCount&&timers.size===1,'200 scrolling events reuse one pending droplet timer');
runUntil(5989);check(spawns===0,'droplets wait four seconds after the final input');
runUntil(5990);check(spawns===1,'idle droplets resume at the correct deadline');
rain.state.drops.add({timer:0,node:{remove(){removals++;}}});
rain.input({type:'pointerdown',pointerId:1});
for(let i=0;i<100;i++)rain.input({type:'pointermove',pointerId:1});
check(removals===1&&timers.size===0,'a held pointer removes existing droplets once without rebuilding timers');
rain.input({type:'pointerup',pointerId:1});
check(timers.size===1,'releasing the pointer restarts one idle timer');
rain.stop();check(timers.size===0&&foreground.hidden,'rain cleanup cancels its timer');

let audioTimers=[];
const audio=new Function('setTimeout',`const rainAudio={generation:0,playing:true,loading:false,source:{},context:{state:'running',suspend(){hooks.suspend();return Promise.resolve();}}};
 const hooks={suspend:()=>suspends++};let suspends=0;
 const retireRainSource=()=>{},renderRainControls=()=>{};
 ${section('function stopRainAudio(', 'async function createRainBuffer(')}
 return {stop:stopRainAudio,state:rainAudio,count:()=>suspends};`)(fn=>audioTimers.push(fn));
audio.stop();check(audio.count()===0,'stopping rain leaves time for the existing fade-out');
audio.state.generation++;audio.state.playing=true;audioTimers.shift()();
check(audio.count()===0,'a previous stop cannot suspend newly started rain audio');
audio.stop();audioTimers.shift()();check(audio.count()===1,'stopped rain suspends its audio context after the fade');

let copyScans=0;
const closedCopy=new Function('element','preciseBossIds',section('function renderCopy(', 'function updateConnection(')+';return renderCopy;')(
  ()=>({open:false}),()=>{copyScans++;return new Set();});
for(let i=0;i<60;i++)closedCopy(i*1000);
check(copyScans===0,'a closed copy dialog never scans the schedule on clock ticks');
const tickCalls=[];
const editorTick=new Function('calls',`let leaving=false,data={},selectedFilter='custom',customEditing=true,renderedKey='old',hiddenMenuCollapsed=true;
 const document={hidden:false},serverNow=()=>1000;
 const WebNotices={tick:()=>calls.push('notices')},WebLimitedSakura={update:()=>calls.push('head'),refresh:()=>{}};
 const refreshCustomUI=()=>calls.push('editor'),renderCopy=()=>calls.push('copy'),updateConnection=()=>calls.push('connection');
 ${section('function tick()', 'function renderActive(')};return tick;`)(tickCalls);
editorTick();
check(tickCalls.join(',')==='notices,editor,copy,head,connection',
  'an open custom editor retains notices and copy updates without repainting the hidden schedule');
return checks;

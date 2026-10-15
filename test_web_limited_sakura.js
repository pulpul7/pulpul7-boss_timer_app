/* Offline V8 regression checks. Parameter: moduleCode. No browser/network required. */
const passed=[];
function check(value,message){if(!value)throw Error(message);passed.push(message);}
function context(reduced=true,values=new Map(),blobMode='ok') {
  let serial=0,requests=0,reads=0,writes=0,draws=0,clock=0,image;
  const frames=new Map(),timers=new Map(),events={},attributes=new Map(),listeners={};
  const button={hidden:true,textContent:'',setAttribute(k,v){this[k]=v;},addEventListener(k,v){listeners[k]=v;}};
  const root={setAttribute:(k,v)=>attributes.set(k,v),removeAttribute:k=>attributes.delete(k),style:{setProperty(){}}};
  const ctx={save(){},restore(){},translate(){},rotate(){},scale(){},setTransform(){},clearRect(){},drawImage(){draws++;}};
  const surface=()=>({style:{},setAttribute(){},getContext:()=>ctx,toBlob(f){if(blobMode==='throw')throw Error('blocked');if(blobMode==='ok')f({});}});
  const rect={left:20,top:160,width:300,height:70,right:320,bottom:230};
  const normal={id:'boss',dataset:{},className:'event-row',classList:{contains:()=>false},getBoundingClientRect:()=>rect};
  const rest={...normal,id:'rest',classList:{contains:name=>name==='type-break'}};
  const document={hidden:false,documentElement:root,body:{append(){}},getElementById:()=>button,
    createElement:surface,querySelectorAll:selector=>selector.startsWith('.schedule-tabs')?[]:nodes,addEventListener:(k,v)=>events[k]=v};
  let nodes=[rest,normal];
  const media={matches:reduced,addEventListener(k,v){events.motion=v;}};
  const window={innerWidth:1280,innerHeight:800,scrollY:0,devicePixelRatio:1,matchMedia:()=>media,addEventListener:(k,v)=>events[k]=v};
  class Image {constructor(){image=this;}set src(value){requests++;}}
  const localStorage={getItem:k=>{reads++;return values.get(k)||null;},setItem:(k,v)=>{writes++;values.set(k,v);}};
  const source=moduleCode.replace('return {sync,refresh};',
    'return {sync,refresh,test:{wind,scan,createPetal,advance,decorations,targets:()=>targets,petals:()=>petals}};');
  const api=new Function('document','window','Image','requestAnimationFrame','cancelAnimationFrame','URL','localStorage','setTimeout','clearTimeout','performance',source+'\nreturn WebLimitedSakura;')(
    document,window,Image,f=>{const id=++serial;frames.set(id,f);return id;},id=>frames.delete(id),{createObjectURL:()=> 'blob:fixture'},
    localStorage,(f)=>{const id=++serial;timers.set(id,f);return id;},id=>timers.delete(id),{now:()=>clock});
  return {api,values,frames,timers,events,attributes,document,media,button,requests:()=>requests,reads:()=>reads,writes:()=>writes,draws:()=>draws,resetDraws:()=>{draws=0;},
    setClock:now=>{clock=now;},setNodes:list=>{nodes=list;},load:()=>image.onload(),click:()=>listeners.click(),run(now){clock=now;const [id,f]=frames.entries().next().value;frames.delete(id);f(now);}};
}
const oldOff=new Map([['BossTimer.limitedSakura.motion.v1:tester',JSON.stringify({schema:1,enabled:false})]]);
const c=context(true,oldOff);check(c.requests()===0,'atlas remains lazy');
c.api.sync(true);check(c.requests()===1,'one atlas request');
await c.load();c.run(200);c.run(250);
check(c.frames.size===1&&c.api.test.petals().length>0,'petals run even with reduced motion and a previously saved OFF');
check(c.reads()===0&&c.writes()===0,'old ON/OFF storage is no longer read or written');
check(!moduleCode.includes('sakura-motion-toggle'),'ON/OFF control removed from module');
c.api.test.scan(250);check(c.api.test.targets().length===1&&c.api.test.targets()[0].node.id==='boss','rest rows remain excluded');
c.resetDraws();c.api.test.decorations();check(c.draws()===2,'both existing outer flower decorations remain');
c.document.hidden=true;c.events.visibilitychange();check(c.frames.size===0,'hidden page pauses');
c.document.hidden=false;c.events.visibilitychange();check(c.frames.size===1,'visible page resumes');
c.api.sync(false);check(c.frames.size===0,'other themes stop the loop');
c.api.sync(true);check(c.requests()===1,'returning to spring reuses atlas');
const pair=context();pair.api.sync(true);await pair.load();pair.api.test.scan(200);
for(let i=0;i<32;i++)pair.api.test.createPetal();
const particles=pair.api.test.petals();
check(particles.filter(p=>p.perch).length===4,'perching density is 25% of the previous one-in-two rule');
for(const p of particles)pair.api.test.advance(p,p.duration+.01);
check(particles.filter(p=>p.phase==='perch').length===4,'only sparse petals pause on borders');
check(particles.filter(p=>p.phase==='fall').length===28,'remaining petals continue falling');
const perched=particles.find(p=>p.phase==='perch');
const at={...perched.pose};pair.api.test.advance(perched,.05);
check(perched.pose.x===at.x&&perched.pose.y===at.y,'perched attachment point stays fixed in wind');
pair.api.test.advance(perched,perched.hold);const released={...perched.pose};pair.api.test.advance(perched,.016);
check(Math.abs(perched.pose.angle-released.angle)<.02,'wind does not snap at release');
const base=pair.api.test.targets()[0].node;
const inner=Array.from({length:8},(_,i)=>({...base,id:'inside'+i}));
const outer=Array.from({length:2},(_,i)=>({...base,id:'outer'+i,classList:{contains:name=>name==='schedule-frame'}}));
pair.setNodes([...inner,...outer]);pair.api.test.scan(300);pair.resetDraws();pair.api.test.decorations(300);
check(pair.draws()===14,'8 outer-side flowers plus 2 interior flowers and 4 container flowers remain');
const beforeScroll=particles.map(p=>({p,phase:p.phase,pose:{...p.pose}}));
pair.setClock(310);pair.events.scroll();pair.resetDraws();pair.api.test.decorations(320);
check(pair.draws()===12,'scroll hides only interior fixed flowers, preserving perimeter decorations');
check(beforeScroll.every(({p,phase,pose})=>p.phase===phase&&p.pose.x===pose.x&&p.pose.y===pose.y)&&pair.api.test.petals().length===32,
  'scroll does not remove or release falling and perched petals');
pair.setClock(470);pair.events.scroll();pair.resetDraws();pair.api.test.decorations(500);
check(pair.draws()===12,'continued scrolling extends hiding without duplicate animation loops');
check(pair.frames.size===1,'scroll reuses the existing animation loop');
pair.resetDraws();pair.api.test.decorations(649);check(pair.draws()===12,'interior flowers stay hidden until scroll idle');
pair.resetDraws();pair.api.test.decorations(650);check(pair.draws()===14,'interior flowers return after 180ms of scroll idle');
pair.api.sync(false);pair.events.scroll();pair.api.sync(true);pair.api.test.scan(660);
pair.resetDraws();pair.api.test.decorations(660);check(pair.draws()===14,'theme switching resets scroll hiding and inactive scroll is ignored');
for(let t=0;t<20;t+=.2) {
  const a=pair.api.test.wind(t,3),b=pair.api.test.wind(t+.001,3);
  check(Math.abs(a)<=1&&Math.abs(b-a)<.01,'wind is bounded and continuous at '+t.toFixed(1));
}
check(pair.api.test.wind(2,1)!==pair.api.test.wind(2,6),'flowers have independent gust phases');
for(const mode of ['throw','never']) {
  const blocked=context(true,new Map(),mode);blocked.api.sync(true);const loading=blocked.load();
  check(blocked.frames.size===1,'blocked/slow texture encoding cannot block petals: '+mode);
  for(const f of [...blocked.timers.values()])f();await loading;
  check(blocked.attributes.get('data-sakura-ready')==='true','texture guard resolves: '+mode);
}
return passed;

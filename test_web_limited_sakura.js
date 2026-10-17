/* Offline V8 regression checks. Parameter: moduleCode. No browser/network. */
const passed=[];
function check(value,message){if(!value)throw Error(message);passed.push(message);}
function context(blobMode='ok',mobile=false,warm=false) {
  let serial=0,requests=0,reads=0,writes=0,clock=0,image,rectReads=0,draws=0,clears=0,callbacks=0;
  const frames=new Map(),timers=new Map(),deadlines=new Map(),events={},attributes=new Map(),encodedWidths=[];
  const window={innerWidth:mobile?390:1280,innerHeight:800,scrollX:0,scrollY:0,devicePixelRatio:1,
    matchMedia:()=>({matches:true}),addEventListener:(k,v)=>events[k]=v};
  const style=()=>({setProperty(k,v){this[k]=v;},getPropertyValue(k){return this[k]||'';}});
  class Element {
    constructor(className='',id='',top=160){this.className=className;this.id=id;this.dataset={};this.style=style();this.children=[];this.box={left:20,top,width:300,height:70};this.connected=true;
      this.classList={contains:name=>this.className.split(' ').includes(name),
        add:name=>{if(!this.classList.contains(name))this.className+=' '+name;},
        remove:name=>{this.className=this.className.split(' ').filter(n=>n!==name).join(' ');}};
    }
    get isConnected(){return this.connected && (!this.parentElement || this.parentElement.isConnected);}
    setAttribute(k,v){this[k]=v;}
    append(el){if(el.parentElement)el.remove();el.parentElement=this;el.connected=true;this.children.push(el);}
    remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(c=>c!==this);this.parentElement=null;this.connected=false;}
    getBoundingClientRect(){rectReads++;const b=this.box,left=b.left-window.scrollX,top=b.top-window.scrollY;return {left,top,width:b.width,height:b.height,right:left+b.width,bottom:top+b.height};}
  }
  const root={setAttribute:(k,v)=>attributes.set(k,v),removeAttribute:k=>attributes.delete(k),style:style()};
  const ctx={save(){},restore(){},translate(){},rotate(){},scale(){},setTransform(){},clearRect(){clears++;},drawImage(){draws++;}};
  if(warm)Object.assign(ctx,{getImageData:()=>({data:new Uint8Array(72*72*4).fill(255)}),
    beginPath(){},lineTo(){},moveTo(){},closePath(){},clip(){},transform(){}});
  const normal=new Element('event-row','boss-a'),rest=new Element('event-row type-break','rest',350);
  let nodes=[rest,normal],icons=[];
  const document={hidden:false,documentElement:root,body:new Element('body'),head:new Element('head'),
    createElement:tag=>{const el=new Element();if(tag==='canvas'){el.getContext=()=>ctx;el.toBlob=f=>{encodedWidths.push(el.width);if(blobMode==='throw')throw Error('blocked');if(blobMode==='ok')f({});};}return el;},
    querySelectorAll:selector=>selector==='.sakura-anchor'?nodes.filter(n=>n.classList.contains('sakura-anchor')):selector.startsWith('.schedule-tabs')?icons:nodes,
    addEventListener:(k,v)=>events[k]=v};
  class Image {constructor(){image=this;}set src(value){requests++;}}
  const petalBinding=/([\w$]+)=\[\],width=0,height=0/.exec(moduleCode)?.[1] || 'petals';
  const source=moduleCode.replace(/return\s*\{sync,refresh,update\};/,
    `return {sync,refresh,update,test:{wind,scan,view,createPetal,advance,contact,perch,release,count:()=>${petalBinding}.length}};`);
  const api=new Function('document','window','Image','requestAnimationFrame','cancelAnimationFrame','URL','Blob','localStorage','setTimeout','clearTimeout','performance',source+'\nreturn WebLimitedSakura;')(
    document,window,Image,f=>{const id=++serial;frames.set(id,f);return id;},id=>frames.delete(id),{createObjectURL:()=> 'blob:fixture'},class Blob {},
    {getItem(){reads++;},setItem(){writes++;}},(f,delay=0)=>{const id=++serial;timers.set(id,f);deadlines.set(id,clock+delay);return id;},
    id=>{timers.delete(id);deadlines.delete(id);},{now:()=>clock});
  return {api,frames,timers,events,attributes,document,window,normal,rest,Element,encodedWidths,
    requests:()=>requests,reads:()=>reads,writes:()=>writes,rectReads:()=>rectReads,draws:()=>draws,clears:()=>clears,callbacks:()=>callbacks,
    setClock:now=>{clock=now;},setNodes:list=>{nodes=list;},setIcons:list=>{icons=list;},load:()=>image.onload(),
    scroll(now,y=window.scrollY,target=document){clock=now;window.scrollY=y;events.scroll({target});},
    run(now){clock=now;for(const [id,f] of [...timers])if(deadlines.get(id)<=clock){timers.delete(id);deadlines.delete(id);f();}
      const entry=frames.entries().next().value;if(!entry)return;const [id,f]=entry;frames.delete(id);callbacks++;f(now);}};
}
const c=context();check(c.requests()===0,'atlas stays lazy');
c.api.sync(true);await c.load();c.run(200);
check(c.requests()===1&&c.frames.size+c.timers.size===1&&c.draws()>0,'one atlas and one visible animation loop');
check(c.reads()===0&&c.writes()===0,'no legacy OFF storage access');
check(!moduleCode.includes('sakura-motion-toggle'),'petals stay always ON');
const flowers=c.normal.children.slice();
check(flowers.length===1&&flowers.every(n=>n.classList.contains('sakura-fixed')),'only the edge flower remains attached to the card');
check(flowers[0].style.left==='1px'&&Math.abs(parseFloat(flowers[0].style.width)-40.6)<.01,'desktop left-edge flower is 30 percent smaller');
check(c.rest.children.length===0,'rest has no butterfly or blossom garlands');
check(c.document.head.children.length===1&&c.document.head.children[0].src==='/themes/limited-sakura-butterfly-v1.js','one lazy controller script request');
check(flowers.every(n=>n['aria-hidden']==='true'),'decoration hidden from assistive reading');
const reads=c.rectReads(),styleBefore=flowers.map(n=>JSON.stringify(n.style));
c.scroll(210,100);c.scroll(215,110);c.run(216);
check(c.rectReads()===reads,'rapid window scrolling performs no synchronous layout reads');
check(flowers.every((n,i)=>c.normal.children[i]===n&&JSON.stringify(n.style)===styleBefore[i]),'attached flowers keep the same DOM identity and local position during scrolling');
check(!c.attributes.has('data-sakura-scrolling'),'scrolling never hides inner flowers');
check(c.frames.size+c.timers.size===1,'scroll does not duplicate frame or timer loops');
c.scroll(400,120);c.run(500);check(!c.attributes.has('data-sakura-scrolling'),'continued scrolling keeps inner flowers visible');
c.run(621);check(flowers.every(n=>c.normal.children.includes(n)),'inner flowers remain attached after scroll idle');
check(c.normal.children[0]===flowers[0],'geometry rescans preserve existing attachments');
const hit=c.api.test.contact({x:100,y:20},{x:100,y:50});
check(hit&&hit.target.node===c.normal&&hit.edge==='top','a falling petal catches the first real crossed border');
const projected=c.api.test.view(hit.target);c.window.scrollY+=11;
check(c.api.test.view(hit.target).top===projected.top-11,'cached document geometry tracks scrolling without a rescan');
c.window.scrollY-=11;
check(!c.api.test.contact({x:100,y:225},{x:100,y:245}),'rest borders never catch petals');
const samples=Array.from({length:32},()=>c.api.test.createPetal());
check(samples.filter(p=>p.perchable).length===4,'only one in eight petals can perch');
check(samples.every(p=>p.phase==='fall'&&p.pose.y<0),'all petals fall freely from above instead of being attracted to a border');
const p=samples[0];p.perchable=true;p.pose={x:100,y:40.9,angle:.7};p.vy=30;p.vx=0;
c.api.test.advance(p,.016);
check(p.phase==='perch'&&p.el.parentElement===c.normal,'actual contact anchors a perched petal to the card');
const perched=p.el,local={left:perched.style.left,top:perched.style.top},before={...p.pose};
c.window.scrollY+=17;c.api.test.advance(p,.016);
check(p.el===perched&&p.pose.y===before.y-17&&p.el.style.left===local.left&&p.el.style.top===local.top,'perched petals scroll natively without a delayed viewport transform');
p.age=p.hold;c.api.test.advance(p,.016);const released={...p.pose};
check(p.phase==='fall'&&!p.el&&c.normal.children.every(n=>n!==perched),'release removes the temporary attachment');
c.api.test.advance(p,.016);
check(Math.hypot(p.pose.x-released.x,p.pose.y-released.y)<2&&Math.abs(p.pose.angle-released.angle)<.05,'release resumes at the same point and rotation');
const detached=samples[8];c.api.test.perch(detached,hit);c.normal.connected=false;
check(!c.api.test.advance(detached,.016)&&!detached.el,'removed cards cannot leave stale perched decorations');
c.normal.connected=true;
const free=samples[1];free.pose={x:200,y:0,angle:0};free.perchable=false;
free.key=3;free.roll=0;free.spin=.6;
const rolling=[];for(let i=0;i<600;i++){c.api.test.advance(free,.016);rolling.push(free.roll);}
check(Math.max(...rolling)-Math.min(...rolling)>12,'thin petals tumble through multiple face/edge cycles');
check(Math.abs(free.pose.angle)>.1&&free.pose.y>100,'wind changes rotation while gravity continues falling');
const inner=Array.from({length:40},(_,i)=>new c.Element('event-row','inside'+i,140+i*20));
const outer=Array.from({length:2},(_,i)=>new c.Element('schedule-frame','outer'+i,200));
c.setNodes([...inner,...outer,c.rest]);c.api.test.scan(700);
check(inner.every(n=>n.children.every(e=>!e.classList.contains('sakura-inner'))),'all interior blossoms are removed');
check(outer.every(n=>n.children.length<=1),'sparse outer cards never duplicate a flower at the same position');
check(flowers.every(n=>!n.isConnected),'stale flower attachments are removed');
const attached=inner.find(n=>n.children.length),kept=attached.children.slice();kept.forEach(n=>n.remove());c.api.test.scan(710);
check(attached.children.length===kept.length&&attached.children.every(n=>!kept.includes(n)),
  'schedule content replacement repairs only missing attachments');
c.run(720);c.run(736);check(c.rest.children.length===0&&c.document.head.children.length===1,'scroll and animation never recreate a rest-box butterfly or reload its controller');
c.document.hidden=true;c.events.visibilitychange();check(c.frames.size===0,'hidden page pauses');
c.document.hidden=false;c.events.visibilitychange();check(c.frames.size===1,'visible page resumes');
c.api.sync(false);check(c.frames.size===0&&inner.every(n=>n.children.length===0)&&c.rest.children.length===0,'other themes remove attached flowers and butterfly');
c.api.sync(true);check(c.requests()===1,'returning to spring reuses the atlas');
// Dense phone lists and long scroll jumps must not recycle fixed flowers.
const dense=context('ok',true);
const rows=Array.from({length:64},(_,i)=>new dense.Element('event-row','dense'+i,i*28));
const far=new dense.Element('event-row','far',6000);dense.setNodes([...rows,far]);
dense.api.sync(true);await dense.load();dense.run(200);
check(rows.some(n=>n.children.length>0)&&rows.some(n=>n.children.length===0)&&far.children.length>0,
  'sparse membership includes offscreen cards without depending on scrolling');
const owned=new Map([...rows,far].map(n=>[n,n.children.slice()]));
check(far.children.every(n=>n.style.animationPlayState==='paused'),
  'far offscreen flowers pause animation without removal or hiding');
dense.scroll(250,1300);dense.run(251);
check(rows[50].children.every((n,i)=>n===owned.get(rows[50])[i]),
  'newly visible flowers exist before deferred geometry discovery');
dense.run(500);dense.scroll(600,6000);dense.run(1000);
check([...owned].every(([node,list])=>list.every((n,i)=>node.children[i]===n)),
  'long scrolls preserve every flower DOM identity');
check(far.children.every(n=>n.style.animationPlayState==='running'),
  'reentering flowers resume their existing animation');
check(rows[0].children.every(n=>n.style.animationPlayState==='paused'),
  'flowers left behind are paused in place');
dense.scroll(1100,0);dense.run(1400);
check([...owned].every(([node,list])=>list.every((n,i)=>node.children[i]===n)),
  'scrolling back does not create replacement flowers');
dense.setNodes(rows.filter((_,i)=>i!==34));dense.api.test.scan(1500);
check(owned.get(rows[34]).every(n=>!n.isConnected)&&far.children.length===0,
  'actual schedule removal still cleans obsolete decorations');
const empty=context();empty.setNodes([]);empty.api.sync(true);await empty.load();empty.run(200);
check(empty.frames.size+empty.timers.size===1,'no rest boxes or boss rows is safe with one pending animation wakeup');
const phone=context('ok',true);phone.api.sync(true);await phone.load();phone.run(200);
check(phone.normal.children.length===1&&phone.rest.children.length===0,'mobile also has no interior flower');
check(Math.abs(parseFloat(phone.normal.children[0].style.width)-32.2)<.01,'phone left-edge flower is 30 percent smaller');
const hero=new phone.Element('hero','hero',40);hero.box.height=120;
const pool=Array.from({length:400},(_,i)=>new phone.Element('event-row','density-'+i,200+i*28));
phone.setNodes([hero,...pool]);phone.api.test.scan(800);
check(hero.children.length===2&&hero.children[0].style.left==='1px'&&hero.children[0].style.top==='64%'&&hero.children[0].style.width==='46px',
  'next boss keeps one full-size left flower below the vertical center regardless of density');
check(hero.children[1].style.left==='calc(100% - 1px)'&&hero.children[1].style.top==='70%'&&hero.children[1].style.width==='46px',
  'next boss also keeps a full-size right flower below its alarm button');
check(hero.children[1].style.getPropertyValue('--sakura-static')==='var(--sakura-twig)'&&
  hero.children[0].style.getPropertyValue('--sakura-static')!=='var(--sakura-twig)',
  'right hero decoration uses a different twig blossom');
const heroFlowers=hero.children.slice();phone.scroll(810,200);phone.api.test.scan(1300);
check(hero.children.every((el,i)=>el===heroFlowers[i]),'both hero flowers survive scrolling without replacement');

const hash=s=>[...s].reduce((n,c)=>(n*31+c.charCodeAt(0))>>>0,0);
const left=pool.filter(n=>hash(n.id)%2),right=pool.filter(n=>!(hash(n.id)%2));
const keptLeft=left.filter(n=>n.children.length),keptRight=right.filter(n=>n.children.length);
check(keptLeft.length/left.length>=.35&&keptLeft.length/left.length<=.45,'about 40 percent of left edge flowers remain');
check(keptRight.length===0,'non-hero right edge flowers are removed');
check([...keptLeft,...keptRight].every(n=>Math.abs(parseFloat(n.children[0].style.width)-32.2)<.01),'right and left edge flowers share the reduced size');
for(const [mobile,limit] of [[false,8],[true,4]]) {
  const sparse=context('ok',mobile);sparse.api.sync(true);await sparse.load();
  for(let i=0;i<80;i++)sparse.run(i*1000+200);
  check(sparse.api.test.count()<=limit&&sparse.api.test.count()>0,'fewer simultaneous petals on '+(mobile?'phone':'desktop'));
}
for(let t=0;t<20;t+=.2) {
  const a=c.api.test.wind(t,3),b=c.api.test.wind(t+.001,3);
  check(Math.abs(a)<=1&&Math.abs(b-a)<.01,'wind remains bounded and continuous '+t.toFixed(1));
}
check(c.api.test.wind(2,1)!==c.api.test.wind(2,6),'independent gust phases');
const warmed=context('ok',false,true);warmed.api.sync(true);await warmed.load();
for(let round=0;round<100;round++) {
  for(const [id,f] of [...warmed.timers]){warmed.timers.delete(id);f();}
  await Promise.resolve();await Promise.resolve();
}
check(warmed.attributes.get('data-sakura-wind-ready')==='true','all petal deformation frames finish warming');
check(warmed.encodedWidths.filter(w=>w===4032).length===4&&Math.max(...warmed.encodedWidths)<=4096,
  '84-frame strips stay within a 4096px texture width on phones');
for(const mode of ['throw','never']) {
  const blocked=context(mode);blocked.api.sync(true);const loading=blocked.load();
  check(blocked.frames.size===1,'slow or blocked texture encoding does not block falling animation '+mode);
  for(const f of [...blocked.timers.values()])f();await loading;
  check(blocked.attributes.get('data-sakura-ready')==='true','texture timeout resolves '+mode);
}
const budget=context('ok',true);budget.window.devicePixelRatio=3;
const visibleIcon=new budget.Element('alarm','visible-icon',100),hiddenIcon=new budget.Element('alarm','hidden-icon',3000);
budget.setIcons([visibleIcon,hiddenIcon]);budget.api.sync(true);await budget.load();budget.api.refresh('stable');budget.run(200);
const budgetCanvas=budget.document.body.children.find(n=>n.className==='limited-sakura-canvas');
check(budgetCanvas.width===390&&budgetCanvas.height===800,'phone canvas uses CSS pixel resolution even on DPR 3');
const stableReads=budget.rectReads(),initialClears=budget.clears();
for(let i=1;i<=240;i++){if(i%120===0)budget.api.refresh('stable');budget.run(200+i*1000/120);}
check(budget.clears()-initialClears<=49&&budget.clears()-initialClears>=35,'120Hz phone caps expensive canvas frames to at most 24fps');
check(budget.callbacks()<=50,'spring sleeps between useful frames instead of waking on every 120Hz display frame');
check(budget.rectReads()===stableReads,'unchanged one-second clock ticks do not rescan every schedule box');
check(visibleIcon.style.getPropertyValue('--sakura-icon-play')==='running'&&hiddenIcon.style.getPropertyValue('--sakura-icon-play')==='paused',
  'offscreen alarm flower sprites are paused while visible sprites keep moving');
budget.api.refresh('changed');budget.run(2500);
check(budget.rectReads()>stableReads,'actual schedule changes refresh collision geometry');
budget.document.hidden=true;budget.events.visibilitychange();
check(budget.attributes.get('data-sakura-paused')==='true'&&budget.frames.size===0&&budget.timers.size===0,'background page explicitly pauses CSS artwork, canvas and wakeup timer');
const lightWarm=context('ok',true,true);lightWarm.api.sync(true);await lightWarm.load();
for(let round=0;round<100;round++){
  for(const [id,f] of [...lightWarm.timers]){lightWarm.timers.delete(id);f();}
  await Promise.resolve();await Promise.resolve();
}
check(lightWarm.attributes.get('data-sakura-wind-ready')==='true'&&lightWarm.encodedWidths.filter(w=>w===2016).length===4,
  'phone prepares four smaller 42-frame wind strips');
check(lightWarm.draws()<warmed.draws()*.2,'phone wind preparation uses less than one fifth of desktop draw calls');
const interrupted=context('ok',true,true);interrupted.api.sync(true);await interrupted.load();
interrupted.document.hidden=true;interrupted.events.visibilitychange();
for(let round=0;round<12;round++){
  for(const [id,f] of [...interrupted.timers]){interrupted.timers.delete(id);f();}
  await Promise.resolve();await Promise.resolve();
}
check(!interrupted.attributes.has('data-sakura-wind-ready'),'hiding the page stops unfinished wind preparation');
interrupted.document.hidden=false;interrupted.events.visibilitychange();
for(let round=0;round<100;round++){
  for(const [id,f] of [...interrupted.timers]){interrupted.timers.delete(id);f();}
  await Promise.resolve();await Promise.resolve();
}
check(interrupted.attributes.get('data-sakura-wind-ready')==='true','returning to the page resumes canceled wind preparation');
const noAtlas=context();noAtlas.api.sync(true);noAtlas.api.update(1000,{id:'upcoming',at:16000});
const forwarded=[];noAtlas.window.WebSakuraButterfly={sync:value=>forwarded.push(['sync',value]),update:(now,row)=>forwarded.push(['update',now,row])};
noAtlas.document.head.children[0].onload();
check(forwarded[0][1]===true&&forwarded[1][2].id==='upcoming'&&noAtlas.frames.size===0,
  'controller receives enabled state and next-boss clock before the flower atlas loads');
noAtlas.api.sync(false);check(forwarded.some(row=>row[0]==='sync'&&row[1]===false),'theme exit is forwarded independently of flower loading');
return passed;

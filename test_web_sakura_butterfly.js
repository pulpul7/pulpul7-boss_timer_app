/* Offline time and pointer regression checks. Parameter: moduleCode. */
const checks=[];
function check(value,message){if(!value)throw Error(message);checks.push(message);}
function fixture(at=20000,homeY=600,seed=7) {
  let ms=0,wall=0,created=0,serial=0;const frames=new Map(),events={};
  let random=seed;const flightMath=Object.create(Math);
  flightMath.random=()=>((random=(Math.imul(random,1664525)+1013904223)>>>0)/4294967296);
  const window={innerWidth:390,innerHeight:800,scrollX:0,scrollY:0,addEventListener:(name,fn)=>events[name]=fn};
  class Element {
    constructor(){this.children=[];this.style={setProperty(k,v){this[k]=v;}};this.dataset={};this.handlers={};this.isConnected=true;this.width=180;this.height=40;this.y=0;}
    append(el){this.children.push(el);el.parentElement=this;created++;}
    remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(n=>n!==this);this.isConnected=false;}
    setAttribute(k,v){this[k]=v;}
    addEventListener(k,v){this.handlers[k]=v;}
    getBoundingClientRect(){return {left:20,top:this.y-window.scrollY,width:this.width,height:this.height};}
    closest(){return this.folded?this:null;}
  }
  const home=new Element(),hero=new Element();home.y=homeY;hero.y=140;
  const document={hidden:false,body:new Element(),createElement:()=>new Element(),querySelector:s=>s==='.hero'?hero:home,addEventListener:(name,fn)=>events[name]=fn};
  const instrumented=moduleCode.replace('return {sync,update,draw};',
    'return {sync,update,draw,test:()=>({el,layer,mode,pose:{...pose},trip,flight:flight&&{...flight},bubble})};');
  new Function('window','document','performance','Date','URL','Blob','requestAnimationFrame','cancelAnimationFrame','Math',instrumented)(
    window,document,{now:()=>ms},class Date {static now(){return wall;}},{createObjectURL:()=> 'blob:art'},class Blob {},
    fn=>{const id=++serial;frames.set(id,fn);return id;},id=>frames.delete(id),flightMath);
  const api=window.WebSakuraButterfly;
  let row={id:'boss-a',at};api.sync(true);api.update(0,row);api.draw();
  return {api,document,window,home,hero,frames,events,created:()=>created,row,
    step(t){ms=t;wall=t;const entry=frames.entries().next().value;if(entry){frames.delete(entry[0]);entry[1](t);}else api.update(t,row);return api.test();},
    wake(t){wall=t;document.hidden=false;events.visibilitychange();const entry=frames.entries().next().value;frames.delete(entry[0]);entry[1](ms);return api.test();},
    update(t,next=row){ms=t;wall=t;row=next;api.update(t,next);api.draw();return api.test();},
    touch(){let prevented=false,stopped=false;api.test().el.handlers.pointerdown({preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});return prevented&&stopped;}};
}
const f=fixture(),first=f.api.test().el;
check(f.api.test().mode==='home'&&!first.hidden,'one butterfly rests above the dedication by default');
check(first.dataset.state==='resting'&&first.children[0].className==='sakura-insect','resting posture uses the small insect wrapper');
check(f.document.body.children.length===1,'only one butterfly element exists');
check(f.document.body.children[0]===f.api.test().layer&&first.parentElement===f.api.test().layer,
  'the insect and startle bubble stay inside one clipped viewport layer');
const restPose={...f.api.test().pose};
f.window.scrollY=600;f.step(1000);f.update(1100);
check(f.api.test().el===first&&f.api.test().pose.x===restPose.x&&f.api.test().pose.y===restPose.y,
  'scrolling preserves the same butterfly and document position');
check(first.style.transform.includes((restPose.y-f.window.scrollY)+'px'),
  'viewport rendering compensates scrolling without moving the document anchor');
f.window.scrollY=0;
check(f.step(4999).mode==='home','not triggered before the fifteen-second window');
check(f.step(5000).mode==='transfer','dedication butterfly leaves at fifteen seconds');
check(Math.abs(f.api.test().flight.duration-650/.3)<.01,'general flight is 30 percent of the previous speed');
check(first.dataset.role==='boss'&&first.children[0].style['--sakura-insect-scale']==='1','boss visit grows to double visual size');
check(f.step(7167).mode==='approach','same butterfly approaches the hero from offscreen');
const during={...f.api.test().flight},count=f.created();
f.window.scrollY=900;f.step(8000);f.api.sync(true);f.update(8001);
check(f.api.test().el===first&&f.api.test().flight.start===during.start&&f.created()===count,
  'scrolling and repeated sync calls do not recreate or restart a flight');
check(f.step(14500).mode==='perched'&&first.dataset.state==='resting','butterfly lands and slowly rests on the next boss');
const onBoss={...f.api.test().pose};f.window.scrollY=0;f.update(14600);
check(f.api.test().pose.x===onBoss.x&&f.api.test().pose.y===onBoss.y,'scrolling does not pull the landed butterfly back to a viewport target');
f.update(14700,{id:'simultaneous-boss',at:20000});
check(f.api.test().mode==='perched'&&f.api.test().el===first,'simultaneous bosses share one visit for their spawn time');
check(f.step(15000).mode==='perched','butterfly remains when five seconds remain');
check(f.step(20000).mode==='perched','butterfly remains on the boss at spawn');
check(f.step(20999).mode==='perched','butterfly remains until one second after spawn');
check(f.step(21000).mode==='escape'&&f.api.test().bubble.hidden,'untouched butterfly departs exactly one second after spawn');
check(f.api.test().flight.duration===2600,'automatic boss departure is half the previous speed');
check(f.step(23600).mode==='away'&&first.hidden,'automatic departure ends outside the screen');
check(f.step(23601).mode==='return','after departure butterfly returns toward the dedication');
check(f.step(31601).mode==='home'&&f.api.test().el===first,'return reuses the same element');
const t=fixture(40000),tEl=t.api.test().el;
check(t.touch(),'pointer input is handled without activating an underlying control');
check(t.api.test().mode==='escape'&&!t.api.test().bubble.hidden&&t.api.test().bubble.alt==='!!','touch displays a real exclamation bubble image');
check(t.step(550).mode==='escape'&&!tEl.hidden,'touch escape is still visible at the old 550ms deadline');
check(t.step(9167).mode==='away'&&tEl.hidden,'home escape is 30 percent of the previous speed using the original baseline');
check(t.step(9999).mode==='away','touch escape does not immediately respawn');
check(t.step(10000).mode==='return'&&t.step(18000).mode==='home','idle butterfly eventually returns without creation');
const b=fixture();b.step(5000);b.step(7167);b.step(14500);b.step(14600);b.touch();
check(b.api.test().mode==='escape'&&!b.api.test().bubble.hidden,'touching a perched butterfly also startles it');
check(b.api.test().flight.duration===5500,'boss touch escape is half of the previous speed');
b.step(20100);b.update(20200);
check(b.api.test().mode==='away','one boss cannot restart a dismissed visit');
const late=fixture();late.step(5000);late.step(21000);
check(late.api.test().mode==='escape','a resumed background page skips stale landing after the departure deadline');
const afterSpawn=fixture();afterSpawn.hero.y=1800;afterSpawn.update(5000);
afterSpawn.hero.y=140;afterSpawn.update(20000);
check(afterSpawn.api.test().mode==='transfer','bringing the boss into view at spawn still permits its visit');
afterSpawn.step(20250);afterSpawn.step(20500);
check(afterSpawn.api.test().mode==='perched','a late arrival can land during the post-spawn second');
check(afterSpawn.step(21000).mode==='escape','late arrival uses the same one-second departure deadline');
const expired=fixture();expired.hero.y=1800;expired.update(5000);
expired.hero.y=140;expired.update(21000);
check(expired.api.test().mode==='home','an unvisited boss cannot start a flight after its departure deadline');
const missing=fixture();missing.hero.width=0;missing.update(5000);
check(missing.api.test().mode==='home','no next-boss box means no flight to a missing target');
missing.home.width=0;missing.update(5100);check(missing.api.test().el.hidden,'folded dedication does not leave a floating idle butterfly');
f.api.sync(false);check(f.api.test().el===first&&f.api.test().mode==='escape'&&f.frames.size===1,'theme change preserves the same butterfly while it flies away');
f.api.sync(false);const exitStart=f.api.test().flight.start;f.step(32000);f.api.sync(false);
check(f.api.test().flight.start===exitStart&&f.api.test().el===first,'further theme choices do not restart the exit');
f.step(42000);check(f.api.test().el===null&&f.document.body.children.length===0&&f.frames.size===0,'theme exit removes the element only after reaching outside');
const independent=fixture(20000,1600);const original=independent.api.test().el;
check(independent.frames.size===0,'an idle offscreen butterfly consumes no animation frame loop');
check(independent.step(5000).mode==='approach','offscreen dedication skips an invisible transfer and starts approaching at fifteen seconds');
check(independent.step(14500).mode==='perched'&&independent.api.test().el===original,'independent frame loop lands on the boss without flower draw calls');
independent.document.hidden=true;independent.events.visibilitychange();check(independent.frames.size===0,'hidden page pauses its own frame loop');
check(independent.api.test().layer.hidden===true,'hidden page removes the butterfly layer from painting, including wing animations');
check(independent.wake(21000).mode==='escape','wall clock handles sleeping phones before a stale landing can restart');
check(independent.api.test().layer.hidden===false,'visible page restores the same butterfly layer');
const carry=fixture(40000);carry.touch();carry.step(500);const live={...carry.api.test().pose},start=carry.api.test().flight.start;
carry.api.sync(false);check(carry.api.test().flight.start===start&&carry.api.test().pose.x===live.x&&carry.api.test().pose.y===live.y,
  'switching theme during touch escape preserves both position and progress');
carry.step(9167);check(carry.api.test().el===null&&carry.frames.size===0,'cross-theme touch escape finishes independently');
const back=fixture(40000);back.api.sync(false);back.step(600);const previous=back.api.test().el,begin=back.api.test().flight.start;
back.api.sync(true);back.step(1000);check(back.api.test().el===previous&&back.api.test().flight.start===begin,'returning to blossom during exit does not teleport to the dedication');
const curve=fixture(40000);curve.touch();const path={...curve.api.test().flight};
const deviations=[.25,.5,.75].map(u=>{const p=curve.step(path.duration*u).pose,q=u*u*(3-2*u);return Math.hypot(p.x-(path.from.x+(path.to.x-path.from.x)*q),p.y-(path.from.y+(path.to.y-path.from.y)*q));});
check(Math.max(...deviations)>4&&Math.max(...deviations)<38,'flight has small natural height changes without a large orbit');
const loop=fixture(40000);loop.touch();const route=loop.api.test().flight;
const samples=Array.from({length:121},(_,i)=>loop.step(route.duration*i/120).pose);
const dx=route.to.x-route.from.x,dy=route.to.y-route.from.y;
check(samples.every((p,i)=>!i||p.x>=samples[i-1].x-.01),
  'escape progresses toward the edge without circling back across the page');
check(samples.every(p=>Math.abs(p.angle)<=22.01),'gentle height changes do not bank at exaggerated angles');
check(Math.hypot(samples[120].x-route.to.x,samples[120].y-route.to.y)<.01,'small flight beats finish exactly at the target');
check(route.to.y>0&&route.to.y<loop.window.innerHeight,'escape leaves through a nearby side edge rather than a large upward sweep');
const different=fixture(40000,600,91);different.touch();
check(JSON.stringify(different.api.test().flight.points)!==JSON.stringify(route.points),'each trip chooses different height beats');
for(const width of [320,390,768,1280]) {
  const screen=fixture(40000);screen.window.innerWidth=width;screen.touch();
  check(screen.api.test().flight.to.x===width+70,'exit destination follows the current viewport width '+width);
  screen.step(screen.api.test().flight.duration);
  check(screen.api.test().el.hidden,'butterfly disappears after crossing the viewport edge '+width);
}
check(first.dataset.role==='home'&&first.children[0].style['--sakura-insect-scale']==='.5','home butterfly returns to its small scale');
check(b.api.test().el.dataset.role==='boss'&&b.api.test().el.children[0].style['--sakura-insect-scale']==='1',
  'boss visitor and its departure use twice the home visual size');
const resting=fixture(40000),restingNode=resting.api.test().el;resting.window.scrollY=300;resting.events.scroll();
check(resting.api.test().el===restingNode&&resting.api.test().mode==='home'&&resting.frames.size===0,'scroll projects the existing resting butterfly without starting a frame loop');
const collision=fixture();collision.step(4000);collision.api.sync(false);collision.step(5000);
const escaping=collision.api.test().flight.start,where={...collision.api.test().pose};collision.api.sync(true);collision.update(5001);
check(collision.api.test().mode==='escape'&&collision.api.test().flight.start===escaping&&Math.hypot(collision.api.test().pose.x-where.x,collision.api.test().pose.y-where.y)<2,
  'fifteen-second arrival cannot interrupt an ongoing cross-theme exit');
collision.step(13167);check(collision.step(13183).mode==='approach','pending visit starts from outside only after the exit finishes');
const turning=fixture(40000);turning.touch();turning.step(9167);turning.step(10000);turning.step(14000);
const returning={...turning.api.test().pose};turning.update(14000,{id:'new-boss',at:28000});
check(turning.api.test().mode==='transfer'&&turning.api.test().flight.from.x===returning.x&&turning.api.test().flight.from.y===returning.y,
  'a new boss during a visible return redirects continuously instead of teleporting');
const idleBudget=fixture(100000);const idleTransform=idleBudget.api.test().el.style.transform;
for(let t=1000;t<=5000;t+=1000)idleBudget.update(t);
check(idleBudget.frames.size===0&&idleBudget.api.test().el.style.transform===idleTransform,
  'resting butterfly does not schedule animation frames or change its transform on clock ticks');
const throttle=fixture(100000);throttle.touch();let moves=0,lastTransform=throttle.api.test().el.style.transform;
for(let i=1;i<=240;i++){
  const state=throttle.step(i*1000/120),transform=state.el.style.transform;
  if(transform!==lastTransform)moves++;lastTransform=transform;
}
check(moves<=61&&moves>=45,'120Hz screen limits butterfly flight transforms to at most 30fps');
const folded=fixture(20000);folded.home.folded=true;folded.update(1000);
check(folded.api.test().el.hidden&&folded.frames.size===0,'folded theme menu hides its resting butterfly without an animation loop');
folded.update(5000);
check(folded.api.test().mode==='approach'&&folded.frames.size===1,'folding the theme menu still allows the visible fifteen-second boss visit');
const hiddenBoss=fixture(20000);hiddenBoss.hero.y=1800;hiddenBoss.update(0);hiddenBoss.step(5000);
check(hiddenBoss.api.test().mode==='home'&&hiddenBoss.frames.size===0,'an offscreen boss does not start an invisible flight');
hiddenBoss.window.scrollY=1700;hiddenBoss.events.scroll();
check(hiddenBoss.api.test().mode==='approach'&&hiddenBoss.frames.size===1,'scrolling the boss into view during the visit window starts the approach');
const foldedReturn=fixture(100000);foldedReturn.touch();foldedReturn.step(9167);foldedReturn.step(10000);
check(foldedReturn.api.test().mode==='return','visible theme menu receives the return flight');
foldedReturn.home.folded=true;foldedReturn.update(11000);
check(foldedReturn.api.test().mode==='home'&&foldedReturn.api.test().el.hidden&&foldedReturn.frames.size===0,
  'folding the menu cancels an unnecessary return flight immediately');
foldedReturn.home.folded=false;foldedReturn.update(12000);
check(!foldedReturn.api.test().el.hidden&&foldedReturn.frames.size===0,'unfolding restores the same resting butterfly without continuous frames');
return checks;

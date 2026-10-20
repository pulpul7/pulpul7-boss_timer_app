/* Production menu controller: staged scrolling, taps, idle and mouse position. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
function fixture(pointer=true,shift=0){
  let now=0,serial=0,blocked=false,layouts=0,heroBottom=200,heroHeight=100;
  const timers=new Map(),events={},winEvents={},classes=new Set();
  const listen=target=>(type,handler)=>(target[type]??=[]).push(handler);
  const window={hooks:{ticks:0},scrollY:0,scrollTo(x,y){this.scrollY=y;},PointerEvent:pointer?class {}:null,addEventListener:listen(winEvents)};
  const document={hidden:false,documentElement:{classList:{add(v){classes.add(v);window.scrollY=Math.max(0,window.scrollY-shift);},remove:v=>classes.delete(v)}},
    querySelector:selector=>selector==='.hero'?{getBoundingClientRect(){layouts++;return {bottom:heroBottom,height:heroHeight};}}:blocked,addEventListener:listen(events)};
  const code=source.slice(source.indexOf('// BEGIN WEB MENU IDLE'),source.indexOf('// END WEB MENU IDLE'));
  const api=new Function('document','window','performance','setTimeout','clearTimeout',`
    let data={},hiddenMenuCollapsed=true,customEditing=false;const tick=()=>++hooks.ticks;
    const hooks=window.hooks;${code}
    return {idle:WebMenuIdle,settings:v=>hiddenMenuCollapsed=!v,editing:v=>customEditing=v,data:v=>data=v};`)(
      document,window,{now:()=>now},(fn,ms)=>{timers.set(++serial,{fn,at:now+ms});return serial;},id=>timers.delete(id));
  function emit(type,extra={},win=false){
    const point={clientX:0,clientY:0};
    const e={type,touches:type==='touchstart'||type==='touchmove'?[point]:[],changedTouches:[point],cancelable:true,
      defaultPrevented:false,preventDefault(){if(this.cancelable)this.defaultPrevented=true;},...extra};
    for(const fn of (win?winEvents:events)[type]||[])fn(e);return e;
  }
  const f={api,document,window,timers,emit,serial:()=>serial,refreshes:()=>window.hooks.ticks,block:v=>blocked=v,layouts:()=>layouts,
    hero:(bottom,height=100)=>{heroBottom=bottom;heroHeight=height;},scroll(y){window.scrollY=y;emit('scroll');},
    advance(ms){const end=now+ms;for(;;){const next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
      if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].fn();}now=end;},folded:()=>classes.has('menus-idle')};
  f.reveal=()=>{f.scroll(0);f.advance(301);f.emit('wheel',{deltaY:-10});};
  f.tap=()=>{emit('touchstart');return emit('touchend');};return f;
}
const f=fixture();f.advance(2999);check(!f.folded(),'visible until three seconds');f.advance(1);check(f.folded(),'three idle seconds fold');
const down=fixture();const downStart=down.emit('wheel',{deltaY:600});check(down.folded()&&downStart.defaultPrevented&&down.window.scrollY===0,'first downward wheel folds menus and stops at next boss');
for(let i=0;i<10;i++){down.advance(40);check(down.emit('wheel',{deltaY:30}).defaultPrevented,'same downward gesture is consumed');down.scroll(50);}
check(down.window.scrollY===0&&down.folded(),'noncancelable/compositor scroll cannot drift beyond downward stop');down.advance(301);const downNext=down.emit('wheel',{deltaY:100});down.scroll(100);check(!downNext.defaultPrevented&&down.window.scrollY===100,'fresh downward wheel scrolls the list normally');
const noCancel=fixture();const unpreventable=noCancel.emit('wheel',{deltaY:600,cancelable:false});noCancel.scroll(100);check(!unpreventable.defaultPrevented&&noCancel.folded()&&noCancel.window.scrollY===0,'noncancelable first wheel is corrected by the scroll-position guard');
const downTouch=fixture();downTouch.emit('touchstart',{touches:[{clientX:10,clientY:400}]});const downMove=downTouch.emit('touchmove',{touches:[{clientX:10,clientY:300}]});check(downTouch.folded()&&downMove.defaultPrevented&&downTouch.window.scrollY===0,'first downward swipe folds and stops at next boss');
downTouch.scroll(90);check(downTouch.window.scrollY===0,'ongoing first swipe cannot move beyond stop');downTouch.emit('touchend',{changedTouches:[{clientX:10,clientY:300}]});check(downTouch.folded(),'release of downward swipe is not treated as reveal tap');
downTouch.emit('touchstart',{touches:[{clientX:10,clientY:400}]});const followMove=downTouch.emit('touchmove',{touches:[{clientX:10,clientY:300}]});downTouch.scroll(100);check(!followMove.defaultPrevented&&downTouch.window.scrollY===100,'new downward swipe can browse the list');downTouch.emit('touchend',{changedTouches:[{clientX:10,clientY:300}]});
const side=fixture();side.emit('touchstart',{touches:[{clientX:10,clientY:400}]});const sideMove=side.emit('touchmove',{touches:[{clientX:200,clientY:380}]});check(!side.folded()&&!sideMove.defaultPrevented,'mostly horizontal touch does not collapse menus');
const editorDown=fixture();editorDown.api.settings(true);const editingWheel=editorDown.emit('wheel',{deltaY:300});check(!editorDown.folded()&&!editingWheel.defaultPrevented,'settings scrolling bypasses downward stop');
for(const y of [0,-12,0])f.scroll(y);check(f.folded(),'stationary top and elastic overscroll cannot reveal');
for(const type of ['pointermove','pointerover','pointerdown','pointerup','click','keydown','focusin','wheel'])f.emit(type);
check(f.folded()&&f.timers.size===0,'mouse hover click ordinary keys and directionless wheel cannot reveal');
f.emit('wheel',{deltaY:50});f.emit('wheel',{deltaY:-50,ctrlKey:true});check(f.folded(),'downward wheel and zoom do not reveal');
f.reveal();check(!f.folded()&&f.window.scrollY===0,'fresh upward wheel at stationary top reveals');
const serial=f.serial();for(let i=0;i<200;i++)f.emit('pointermove');check(f.serial()===serial&&f.timers.size===1,'activity reuses one timer');
f.advance(2900);f.emit('keydown');f.advance(100);check(!f.folded(),'input renews visible deadline');f.advance(2900);check(f.folded(),'last input plus three seconds folds');
f.reveal();f.scroll(120);check(f.folded(),'downward document scroll folds immediately');
f.advance(301);f.emit('wheel',{deltaY:-80});f.scroll(0);check(f.folded(),'first scrolling ascent stops at next boss without menus');
for(let i=0;i<20;i++){f.advance(40);f.emit('wheel',{deltaY:-8});f.emit('scroll');}
check(f.folded(),'continuous wheel and inertia at top cannot cross the stop');
f.advance(301);f.emit('wheel',{deltaY:-30});check(!f.folded(),'new upward wheel after gesture break opens menus');
const compositor=fixture();compositor.advance(3000);compositor.scroll(120);compositor.advance(301);compositor.scroll(0);compositor.emit('wheel',{deltaY:-600});
check(compositor.folded(),'compositor reaching top before wheel dispatch still stops at next boss');compositor.advance(301);compositor.emit('wheel',{deltaY:-50});check(!compositor.folded(),'fresh wheel after compositor arrival can reveal');
f.emit('touchstart');f.advance(5000);check(!f.folded(),'held touch prevents idle folding');f.emit('touchend');f.advance(3000);check(f.folded(),'release restores normal idle deadline');
const tap=f.tap();check(!f.folded()&&tap.defaultPrevented,'light tap immediately shows menus without clicking a boss alarm');f.advance(3000);
f.scroll(120);f.emit('touchstart',{touches:[{clientX:10,clientY:200}]});f.emit('touchmove',{touches:[{clientX:10,clientY:320}]});f.scroll(0);f.emit('touchend',{changedTouches:[{clientX:10,clientY:320}]});
check(f.folded(),'mobile swipe from below stops at next boss and is not a tap');
f.emit('scroll');check(f.folded(),'mobile inertial scrolling cannot reopen menus');
f.emit('touchstart',{touches:[{clientX:10,clientY:200}]});const pull=f.emit('touchmove',{touches:[{clientX:10,clientY:230}]});
check(!f.folded()&&pull.defaultPrevented,'new upward scroll gesture at top reveals and prevents pull-to-refresh');f.emit('touchend',{changedTouches:[{clientX:10,clientY:230}]});f.advance(3000);
f.scroll(80);f.tap();check(!f.folded()&&f.window.scrollY===0,'light tap below top also shows accessible menus');f.advance(3000);
f.emit('touchstart');f.advance(451);f.emit('touchend');check(f.folded(),'long press is not a light tap');
f.emit('touchstart');f.emit('touchend',{changedTouches:[{clientX:30,clientY:0}]});check(f.folded(),'fast drag ending without move event is not a tap');
f.emit('touchstart');f.emit('touchcancel');check(f.folded(),'cancelled touch cannot reveal');
f.emit('touchstart',{touches:[{clientX:0,clientY:0},{clientX:30,clientY:30}]});f.emit('touchend');check(f.folded(),'pinch cannot reveal as a tap');
f.reveal();const normal=f.tap();check(!normal.defaultPrevented,'normal visible-control taps keep their default action');
for(const kind of ['settings','editing','dialog','loading']){
  f.reveal();if(kind==='settings')f.api.settings(true);if(kind==='editing')f.api.editing(true);if(kind==='dialog')f.block(true);if(kind==='loading')f.api.data(null);
  f.advance(3000);f.scroll(40);check(!f.folded(),kind+' preserves visible menus during idle and scroll');
  f.api.settings(false);f.api.editing(false);f.block(false);f.api.data({});f.advance(3000);check(f.folded(),kind+' returns to idle behavior');
}
f.document.hidden=true;f.emit('visibilitychange');check(f.timers.size===0,'background stops timers');f.advance(10000);f.document.hidden=false;f.emit('visibilitychange');f.emit('pageshow',{},true);check(f.folded(),'foreground and bfcache do not reopen');
f.reveal();f.emit('pagehide',{},true);check(f.timers.size===0,'pagehide stops');f.emit('pageshow',{},true);check(f.timers.size===1,'visible pageshow resumes a single deadline');
const anchored=fixture(true,160);anchored.scroll(30);anchored.emit('scroll');check(anchored.folded()&&anchored.window.scrollY===0,'collapse clamping to top cannot reveal');anchored.reveal();check(!anchored.folded(),'explicit upward wheel restores after clamping');
const mouse=fixture();for(let i=0;i<200;i++){mouse.emit('pointermove',{pointerType:'mouse',clientY:190});mouse.advance(20);}check(!mouse.folded(),'above-card movement keeps menus');
mouse.emit('pointermove',{pointerType:'mouse',clientY:220});mouse.advance(2999);check(!mouse.folded(),'below entry starts fresh three-second deadline');mouse.emit('pointermove',{pointerType:'mouse',clientY:230});mouse.advance(1);check(mouse.folded(),'below movement cannot extend deadline');mouse.emit('pointermove',{pointerType:'mouse',clientY:100});check(mouse.folded(),'moving above cannot reopen');
mouse.reveal();mouse.emit('pointermove',{pointerType:'mouse',clientY:240});mouse.advance(1500);mouse.emit('pointermove',{pointerType:'mouse',clientY:150});mouse.advance(2000);check(!mouse.folded(),'moving above cancels previous below deadline');mouse.advance(1000);check(mouse.folded(),'ordinary idle still folds after moving above');
const burst=fixture();for(let i=0;i<600;i++){burst.emit('pointermove',{pointerType:'mouse',clientY:220});burst.advance(5);}check(burst.folded()&&burst.layouts()<=16,'below movement does not measure geometry on every event');
const touched=fixture();for(let i=0;i<40;i++){touched.emit('pointermove',{pointerType:'touch',clientY:240});touched.advance(100);}check(!touched.folded(),'touch movement is not resting mouse activity');
const legacy=fixture(false);for(let i=0;i<40;i++){legacy.emit('mousemove',{clientY:240});legacy.advance(100);}check(legacy.folded(),'legacy mouse shares below deadline');legacy.tap();check(!legacy.folded(),'legacy touch tap restores');
const absent=fixture();absent.hero(0,0);for(let i=0;i<40;i++){absent.emit('pointermove',{pointerType:'mouse',clientY:500});absent.advance(100);}check(!absent.folded(),'hidden next boss cannot create below condition');
check(/html\.menus-idle[^\n]*\.app-header[^\n]*\.live-bar[^\n]*#frame-heading[^\n]*#recent\{display:none!important\}/.test(css),'all themes hide the same menus and previous bosses');
const restored=fixture();restored.advance(3000);restored.advance(60000);check(restored.refreshes()===0,'folded menus keep static spacing without requesting clock refreshes');
restored.reveal();check(restored.refreshes()===1,'menu restoration immediately refreshes date clock and connection exactly once');
return {checks:checks.length};

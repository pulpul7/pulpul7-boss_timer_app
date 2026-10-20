// Shared upper-menu visibility; embedded by sync_web_sakura.py.
const WebMenuIdle=(()=>{
  const root=document.documentElement;
  const inputOptions={capture:true,passive:true};
  let timer=null,last=nowMillis(),folded=false,pressed=false;
  let y=position(),mouseBelow=false,edge=Infinity,measured=-Infinity;
  let wheelAt=-Infinity,topAt=-Infinity,touch=null,wheelHold=false;

  function position(){return Math.max(0,window.scrollY);}
  function nowMillis(){return performance.now();}
  function stop(){clearTimeout(timer);timer=null;}
  function pin(){window.scrollTo(0,0);y=0;topAt=nowMillis();}
  function show(){
    if(!folded)return;
    folded=false;root.classList.remove('menus-idle');window.scrollTo(0,0);
    y=0;mouseBelow=false;measured=-Infinity;noteActivity();tick();
  }
  function blocked(){
    return !data||!hiddenMenuCollapsed||customEditing||document.querySelector(
      'dialog[open],input:focus,select:focus,textarea:focus,[contenteditable="true"]:focus');
  }
  function fold(){
    if(folded)return;
    stop();folded=true;root.classList.add('menus-idle');y=position();
  }
  function check(){
    timer=null;if(document.hidden||folded)return;
    const left=3000-(nowMillis()-last);
    if(left>0){timer=setTimeout(check,left);return;}
    if(pressed||blocked()){timer=setTimeout(check,3000);return;}
    fold();
  }
  function noteActivity(){
    if(folded)return;
    last=nowMillis();if(timer===null&&!document.hidden)timer=setTimeout(check,3000);
  }
  function pointerActivity(event){
    if(folded||document.hidden)return;
    if(event.pointerType&&event.pointerType!=='mouse'){mouseBelow=false;noteActivity();return;}
    const stamp=nowMillis();
    if(stamp-measured>=200){
      const box=document.querySelector('.hero')?.getBoundingClientRect();
      edge=box?.height>0?box.bottom:Infinity;measured=stamp;
    }
    const below=Number.isFinite(event.clientY)&&event.clientY>=edge;
    if(below!==mouseBelow){mouseBelow=below;noteActivity();}else if(!below)noteActivity();
  }
  function wheelInput(event){
    if(event.ctrlKey||!event.deltaY)return;
    const stamp=nowMillis(),fresh=stamp-wheelAt>300;
    wheelAt=stamp;
    if(fresh)wheelHold=false;
    if(event.deltaY>0&&!folded&&!blocked()){fold();pin();wheelHold=true;}
    if(wheelHold){event.preventDefault();return;}
    // One scroll gesture stops at the next boss. Only a fresh gesture opens menus.
    if(fresh&&stamp-topAt>300&&event.deltaY<0&&position()<=1&&y<=1)show();
  }
  function touchInput(event){
    const points=event.touches,point=points[0]||event.changedTouches?.[0];
    pressed=points.length>0;
    if(event.type==='touchstart'){
      wheelHold=false;
      touch=points.length===1?{x:point.clientX,startY:point.clientY,at:nowMillis(),
        atTop:position()<=1,wasFolded:folded,dragged:false,revealing:false,holdDown:false}:null;
    }else if(touch&&point){
      const dy=point.clientY-touch.startY;
      if(Math.abs(point.clientX-touch.x)>10||Math.abs(dy)>10)touch.dragged=true;
      if(event.type==='touchmove'&&!touch.wasFolded&&!folded&&dy< -12&&Math.abs(dy)>Math.abs(point.clientX-touch.x)&&!blocked()){
        fold();pin();touch.holdDown=true;
      }
      if(event.type==='touchmove'&&touch.atTop&&touch.wasFolded&&dy>12){
        touch.revealing=true;show();
      }
      if(event.type==='touchend'&&!pressed&&!touch.dragged&&nowMillis()-touch.at<=450&&folded){
        touch.revealing=true;show();
      }
      // The reveal gesture must not also refresh the page or click a boss alarm.
      if((touch.revealing||touch.holdDown)&&event.cancelable)event.preventDefault();
    }
    if(!pressed||event.type==='touchcancel')touch=null;
    noteActivity();
  }

  document.addEventListener(window.PointerEvent?'pointermove':'mousemove',pointerActivity,inputOptions);
  document.addEventListener('scroll',()=>{
    const next=position();
    if(next>1&&(touch?.holdDown||wheelHold&&nowMillis()-wheelAt<=300)){pin();return;}
    if(next>y&&!folded&&!blocked())fold();
    // Passive wheel events can arrive after compositor scrolling.
    if(next<=1&&y>1)topAt=nowMillis();
    y=position();
  },{passive:true});
  for(const type of ['pointerdown','pointerup','pointercancel'])document.addEventListener(type,()=>{
    pressed=type==='pointerdown';noteActivity();
  },inputOptions);
  for(const type of ['click','keydown','input','change','focusin','focusout','wheel'])document.addEventListener(type,noteActivity,inputOptions);
  document.addEventListener('wheel',wheelInput,{passive:false});
  for(const type of ['touchstart','touchmove','touchend','touchcancel'])document.addEventListener(type,touchInput,
    {capture:true,passive:type==='touchstart'||type==='touchcancel'});
  document.addEventListener('visibilitychange',()=>{stop();pressed=false;touch=null;if(!document.hidden)noteActivity();});
  window.addEventListener('blur',()=>{pressed=false;touch=null;});
  window.addEventListener('pagehide',stop);
  window.addEventListener('pageshow',noteActivity);
  noteActivity();
  return {activity:noteActivity};
})();

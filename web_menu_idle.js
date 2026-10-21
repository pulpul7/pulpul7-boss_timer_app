// Shared next-boss scroll alignment; embedded by sync_web_sakura.py.
const WebMenuIdle=(()=>{
  const desktop=deviceProfile().pc,phone=deviceProfile()['phone'];
  const input=document.getElementById('menu-mouse-hide'),key='BossTimer.menuMouseHide.v1';
  const delayInput=document.getElementById('menu-mouse-delay'),delayKey='BossTimer.menuMouseDelay.v1';
  const delayOptions=[1,3,5,10,15,30,60];
  let delayValue=10,hover=true;
  try{const value=Number(localStorage.getItem(delayKey));if(delayOptions.includes(value))delayValue=value;}catch(_){}
  try{hover=localStorage.getItem(key)!=='false';}catch(_){}
  if(input)input.checked=hover;
  if(delayInput){delayInput.value=String(delayValue);delayInput.disabled=!hover;}
  let timer=null,pressed=false,mouseOutside=false,wheelAt=-Infinity,wheelHold=null,touch=null;
  let y=position();
  function position(){return Math.max(0,window.scrollY);}
  function stop(){clearTimeout(timer);timer=null;}
  function blocked(){return !data||!hiddenMenuCollapsed||customEditing||document.querySelector(
    'dialog[open],input:focus,select:focus,textarea:focus,[contenteditable="true"]:focus');}
  function edge(){
    if(document.documentElement.scrollHeight<=window.innerHeight+1)return null;
    const hero=document.querySelector('.hero'),box=hero?.getBoundingClientRect();
    return box?.height>0?Math.max(0,box.top+position()):null;
  }
  function pin(next){
    if(Math.abs(position()-next)<=1)return;
    window.scrollTo(0,next);y=position();
  }
  function check(){
    timer=null;
    if(!desktop||!hover||!mouseOutside||document.hidden||pressed||position()>1||blocked())return;
    const next=edge();if(next!==null)pin(next);
  }
  function pointerActivity(event){
    if(!desktop||!hover||document.hidden||event.pointerType&&event.pointerType!=='mouse')return;
    mouseOutside=event.type.endsWith('leave');
    if(!mouseOutside)stop();
    else if(timer===null&&!pressed&&position()<=1&&!blocked())timer=setTimeout(check,delayValue*1000);
  }
  function wheelInput(event){
    if(phone||event.ctrlKey||!event.deltaY)return;
    touch=null;
    if(event.deltaY<0){wheelHold=null;if(touch)touch.holdDown=false;stop();return;}
    if(blocked())return;
    const stamp=performance.now();if(stamp-wheelAt>300)wheelHold=null;wheelAt=stamp;
    if(wheelHold===null){const next=edge();if(next!==null&&position()<next-2){wheelHold=next;pin(next);}}
    if(wheelHold!==null)event.preventDefault();
  }
  function touchStop(){
    if(!touch||pressed||!touch.dragged||blocked())return;
    const next=position();
    if(next<touch.edge){touch.holdDown=false;return;}
    if(touch.holdDown||touch.at<touch.edge-2&&next>=touch.edge){touch.holdDown=true;pin(touch.edge);}
  }
  function touchInput(event){
    const point=event.touches[0]||event.changedTouches?.[0];pressed=event.touches.length>0;
    if(event.type==='touchstart'){
      stop();touch=null;wheelHold=null;
      if(event.touches.length!==1||blocked()||event.target?.closest?.('dialog,#theme-panel,input,select,textarea,[contenteditable="true"]'))return;
      const next=edge();if(next!==null)touch={x:point.clientX,startY:point.clientY,at:position(),edge:next,dragged:false,holdDown:false};
    }else if(event.type==='touchcancel'||event.touches.length>1){touch=false;
    }else if(touch&&point){
      const dy=point.clientY-touch.startY;
      if(event.type==='touchmove')touch.dragged=Math.abs(dy)>12&&Math.abs(dy)>Math.abs(point.clientX-touch.x);
      if(dy>12)touch.holdDown=false;
      if(event.type==='touchend')touchStop();
    }
  }
  input?.addEventListener('change',()=>{
    const next=input.checked;try{localStorage.setItem(key,String(next));}catch(_){input.checked=hover;return;}
    hover=next;stop();mouseOutside=false;if(delayInput)delayInput.disabled=!hover;
  });
  delayInput?.addEventListener('change',()=>{
    const value=Number(delayInput.value);if(!delayOptions.includes(value)){delayInput.value=String(delayValue);return;}
    try{localStorage.setItem(delayKey,String(value));}catch(_){delayInput.value=String(delayValue);return;}
    delayValue=value;stop();if(mouseOutside)pointerActivity({type:'mouseleave'});
  });
  const root=document.documentElement;
  root.addEventListener(window.PointerEvent?'pointerenter':'mouseenter',pointerActivity);
  root.addEventListener(window.PointerEvent?'pointerleave':'mouseleave',pointerActivity);
  document.addEventListener('scroll',()=>{
    const next=position();if(next>1)stop();
    if(touch!==null){touchStop();y=position();return;}
    if(wheelHold!==null&&performance.now()-wheelAt<=300){
      if(next<wheelHold-1)wheelHold=null;else if(next>wheelHold+1)pin(wheelHold);
    }else if(!phone&&next>y&&!blocked()){
      const target=edge();if(target!==null&&y<target-2&&next>=target)pin(target);
    }
    y=position();
  },{passive:true});
  for(const type of ['pointerdown','pointerup','pointercancel'])document.addEventListener(type,()=>{pressed=type==='pointerdown';},{capture:true,passive:true});
  document.addEventListener('wheel',wheelInput,{passive:false});
  for(const type of ['touchstart','touchmove','touchend','touchcancel'])document.addEventListener(type,touchInput,{capture:true,passive:true});
  const reset=()=>{stop();pressed=false;touch=null;wheelHold=null;};
  document.addEventListener('visibilitychange',reset);window.addEventListener('blur',reset);window.addEventListener('pagehide',reset);
  return {activity:()=>{}};
})();

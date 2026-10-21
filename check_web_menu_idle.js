/* Run against the isolated PC fixture; menus remain in normal document flow. */
(async()=>{
 const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);},wait=ms=>new Promise(r=>setTimeout(r,ms));
 const root=document.documentElement,input=document.getElementById('menu-mouse-hide'),delay=document.getElementById('menu-mouse-delay');
 const visible=()=>['frame-heading','recent'].every(id=>getComputedStyle(document.getElementById(id)).display!=='none')&&!root.classList.contains('menus-idle');
 const boundary=type=>root.dispatchEvent(new PointerEvent(type,{pointerType:'mouse'}));
 const wheel=deltaY=>{const e=new WheelEvent('wheel',{deltaY,bubbles:true,cancelable:true});document.dispatchEvent(e);return e;};
 const edge=()=>document.querySelector('.hero').getBoundingClientRect().top+scrollY;
 const extra=document.createElement('div');extra.style.height='1500px';document.querySelector('.schedule-app').append(extra);
 const oldDelay=delay.value,oldOption=input.checked;
 try{
  delay.value='1';delay.dispatchEvent(new Event('change'));input.checked=true;input.dispatchEvent(new Event('change'));
  window.scrollTo(0,0);await wait(100);boundary('pointerleave');await wait(1100);
  check(Math.abs(document.querySelector('.hero').getBoundingClientRect().top)<=1,'auto timer aligns next boss at top');check(visible(),'automatic alignment preserves menus and previous bosses');
  const at=scrollY;boundary('pointerenter');check(scrollY===at,'mouse entry never jumps to menu');
  window.scrollTo(0,0);await wait(100);boundary('pointerleave');window.scrollTo(0,100);await wait(1200);check(Math.abs(scrollY-100)<=1,'user scroll cancels automatic alignment');
  window.scrollTo(0,0);await wait(100);boundary('pointerenter');const down=wheel(500);check(down.defaultPrevented&&Math.abs(scrollY-edge())<=1,'first downward wheel stops at next boss');
  check(!wheel(-10).defaultPrevented,'upward wheel immediately releases stop');window.scrollTo(0,100);await wait(100);check(Math.abs(scrollY-100)<=1&&visible(),'upward scroll does not snap back');
  wheel(500);await wait(350);check(!wheel(100).defaultPrevented,'fresh downward gesture continues');
  check(!window.previewErrors?.length,'no runtime errors');return{checks:checks.length};
 }finally{extra.remove();input.checked=oldOption;input.dispatchEvent(new Event('change'));delay.value=oldDelay;delay.dispatchEvent(new Event('change'));window.scrollTo(0,0);}
})()

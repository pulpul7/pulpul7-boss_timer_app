(async()=>{
  const checks=[],wait=ms=>new Promise(r=>setTimeout(r,ms));
  const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  const root=document.documentElement,menus=[document.querySelector('.app-header'),document.querySelector('.live-bar'),document.getElementById('frame-heading')];
  const visible=()=>menus.every(n=>getComputedStyle(n).display!=='none'),folded=()=>menus.every(n=>getComputedStyle(n).display==='none');
  const scroll=async y=>{window.scrollTo(0,y);await wait(150);};
  const wheel=deltaY=>{const e=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY});document.dispatchEvent(e);return e;};
  const touch=(type,y=400)=>{const point=new Touch({identifier:1,target:document.body,clientX:350,clientY:y});
    const e=new TouchEvent(type,{bubbles:true,cancelable:true,touches:type==='touchend'||type==='touchcancel'?[]:[point],changedTouches:[point]});document.dispatchEvent(e);return e;};
  const revealAtTop=async()=>{await scroll(100);check(folded(),'below top stays folded');await scroll(0);check(folded(),'first ascent stops at next boss');await wait(350);wheel(-100);check(visible()&&scrollY===0,'fresh upward gesture opens top menus');check(getComputedStyle(document.getElementById('recent')).display!=='none','reveal restores previous bosses');};
  const recent=document.getElementById('recent'),hero=document.querySelector('.hero');
  const spacer=document.createElement('div');spacer.style.height='900px';document.querySelector('main').append(spacer);
  document.getElementById('theme-default').click();await scroll(0);check(visible(),'dark menus initially visible');
  const firstDown=wheel(600);check(folded()&&firstDown.defaultPrevented&&scrollY===0,'first downward wheel hides menus and stops at next boss');
  for(let i=0;i<5;i++){await wait(40);check(wheel(20).defaultPrevented,'continuous downward wheel stays held');}
  await scroll(50);check(scrollY===0&&folded(),'first downward gesture cannot drift past next boss');await wait(350);check(!wheel(120).defaultPrevented,'new downward gesture permits list scroll');await scroll(140);check(scrollY>0,'list can scroll after first stop');await revealAtTop();
  touch('touchstart',400);const firstSwipe=touch('touchmove',300);check(folded()&&firstSwipe.defaultPrevented&&scrollY===0,'first downward swipe stops at next boss');await scroll(80);check(scrollY===0,'same downward swipe remains pinned');touch('touchend',300);
  touch('touchstart',400);const nextSwipe=touch('touchmove',300);check(!nextSwipe.defaultPrevented,'new downward swipe permits list scroll');await scroll(140);touch('touchend',300);await revealAtTop();
  WebMenuIdle.activity();
  const before=hero.getBoundingClientRect().top,oldClock=document.getElementById('clock').textContent;
  await wait(3100);check(folded(),'three seconds of idle collapse menus');
  const reclaimed=before-hero.getBoundingClientRect().top;check(reclaimed>100,'collapse reclaims vertical space');
  check(hero.isConnected&&getComputedStyle(hero).display!=='none','next boss remains visible');check(getComputedStyle(recent).display==='none','previous bosses hide');
  check(document.getElementById('clock').textContent!==oldClock,'schedule clock continues');
  document.dispatchEvent(new Event('scroll'));
  for(const type of ['pointermove','pointerover','pointerdown','pointerup'])document.dispatchEvent(new PointerEvent(type,{bubbles:true,pointerType:'mouse'}));
  document.dispatchEvent(new KeyboardEvent('keydown',{key:'x',bubbles:true}));check(folded(),'stationary top hover click and keys cannot reveal');
  wheel(-100);check(visible(),'fresh upward wheel at stationary top reveals');await wait(3200);check(folded(),'wheel reveal retains idle hide');
  touch('touchstart');check(folded(),'touch down alone cannot reveal before distinguishing a drag');
  const tap=touch('touchend');check(visible()&&tap.defaultPrevented,'light tap reveals and consumes alarm click');await wait(3200);check(folded(),'tap reveal retains idle hide');
  const oldTheme=root.getAttribute('data-theme'),oldPink=root.hasAttribute('data-theme-pink');
  for(const theme of ['default','sakura','sky','wood','green','blue','magenta','ivory','canary','forsythia','purple','gray','spring','winter','rain','avengers']){
    root.setAttribute('data-theme',theme);root.toggleAttribute('data-theme-pink',theme==='sakura');check(folded(),theme+' shares collapse CSS');
  }
  root.setAttribute('data-theme',oldTheme);root.toggleAttribute('data-theme-pink',oldPink);
  await scroll(160);wheel(-100);await scroll(0);check(folded(),'first wheel ascent reaches next boss without menus');
  for(let i=0;i<12;i++){wheel(-4);await wait(40);}check(folded(),'continued inertial wheel input does not cross stop');
  await wait(350);wheel(-100);check(visible(),'separate upward wheel opens menus');
  await scroll(30);check(folded(),'small downward scroll folds immediately');await revealAtTop();
  await scroll(120);touch('touchstart',400);touch('touchmove',550);await scroll(0);touch('touchend',550);check(folded(),'mobile dragging from below stops at next boss');
  touch('touchstart',400);const pull=touch('touchmove',450);check(visible()&&pull.defaultPrevented,'new upward swipe at top reveals without pull-to-refresh');touch('touchend',450);
  document.getElementById('tab-theme').click();await wait(3200);await scroll(50);check(visible()&&!document.getElementById('theme-panel').hidden,'theme settings remain usable');await scroll(0);document.getElementById('tab-all').click();
  touch('touchstart');await wait(3200);check(visible(),'held touch keeps menus');touch('touchmove',300);await scroll(80);check(folded(),'downward touch scrolling folds');touch('touchend',300);await revealAtTop();
  document.getElementById('tab-custom').click();document.getElementById('custom-open').click();await wait(3200);check(!root.classList.contains('menus-idle')&&!document.getElementById('custom-editor').hidden,'custom editor stays usable');
  const savedSelection=localStorage.getItem(customScope),choice=document.querySelector('#custom-groups [data-choice]:not(:disabled)');check(Boolean(choice),'boss selection exists');choice.click();await wait(7500);
  check(localStorage.getItem(customScope)===savedSelection&&customDirty(),'seven-second autosave remains removed');check(document.getElementById('custom-save-status').textContent.includes('\uC800\uC7A5 \uBC84\uD2BC'),'pending status requests save');
  document.getElementById('tab-all').click();document.getElementById('tab-custom').click();check(localStorage.getItem(customScope)===savedSelection&&customDirty(),'tab switch cannot save draft');document.getElementById('custom-open').click();document.getElementById('custom-save').click();check(!customDirty()&&localStorage.getItem(customScope)!==savedSelection,'explicit Save persists');document.getElementById('custom-close').click();document.getElementById('tab-all').click();
  const dialog=document.getElementById('copy-dialog');dialog.showModal();await wait(3200);check(visible(),'modal keeps menus');dialog.close();
  const moveMouse=clientY=>document.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerType:'mouse',clientX:100,clientY}));await scroll(0);
  for(let i=0;i<32;i++){moveMouse(hero.getBoundingClientRect().top+10);await wait(100);}check(visible(),'above-card mouse movement keeps menus');
  const below=hero.getBoundingClientRect().bottom+20;for(let i=0;i<32;i++){moveMouse(below+i%2);await wait(100);}check(folded(),'below-card mouse movement folds after three seconds');moveMouse(20);check(folded(),'mouse movement cannot reopen');await revealAtTop();
  spacer.remove();await scroll(0);WebMenuIdle.activity();
  const content=[...document.querySelectorAll('#overview,#events,#active-section,#schedule-bottom-nav,#push-controls,#empty')],styles=content.map(n=>n.style.display);content.forEach(n=>n.style.display='none');await wait(3200);
  check(folded()&&document.scrollingElement.scrollHeight>innerHeight,'short content retains scroll access');await scroll(3);check(scrollY>1&&folded(),'short content can leave top');await scroll(0);check(folded(),'short content also stops before menus');await wait(350);wheel(-100);check(visible(),'short content opens on separate upward wheel');content.forEach((n,i)=>n.style.display=styles[i]);
  check(!window.previewErrors?.length,'no runtime errors');check(root.scrollWidth<=390,'no horizontal overflow');
  return {checks:checks.length,reclaimed};
})()

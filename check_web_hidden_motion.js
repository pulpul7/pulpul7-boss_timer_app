/* Run in the isolated production-page Chrome fixture, not an operational page. */
(async()=>{
  const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const root=document.documentElement;
  document.getElementById('theme-avengers').click();
  const deadline=performance.now()+5000;
  while(!globalThis.WebIronmanCinematic&&performance.now()<deadline)await wait(25);
  check(Boolean(globalThis.WebIronmanCinematic),'cinematic runtime loaded');
  WebIronmanCinematic.enable(false);
  WebMenuIdle.activity();root.classList.remove('menus-idle');window.scrollTo(0,0);
  const realHero=document.querySelector('.hero'),header=document.querySelector('.avengers-header-energy');
  const probe=realHero.cloneNode(true);
  probe.querySelectorAll('[id]').forEach(n=>n.removeAttribute('id'));
  probe.querySelectorAll('video,canvas').forEach(n=>n.remove());
  probe.classList.add('spawn-blink');probe.hidden=false;
  probe.style.cssText='position:fixed;top:350px;left:0;width:350px;z-index:40';
  probe.dataset.cinematic='playing';probe.dataset.cinematicVisible='true';
  const caption=document.createElement('span');caption.className='ironman-cinematic-arrival';
  caption.textContent='probe';probe.append(caption);
  const energy=probe.querySelector('.avengers-hero-energy'),count=probe.querySelector('.hero-countdown');
  energy.dataset.inView='true';document.querySelector('.schedule-app').append(probe);
  const sample=node=>node.getAnimations({subtree:true}).map(a=>({a,time:Number(a.currentTime),state:a.playState}));
  async function stopped(node,label,allowAbsent=false){
    await wait(60);const before=sample(node);await wait(180);
    check((allowAbsent||before.length>0)&&before.every(({a,time,state})=>state==='paused'&&Math.abs(Number(a.currentTime)-time)<1),label);
  }
  async function moving(node,label){
    await wait(60);const before=sample(node);await wait(180);
    check(before.some(({a,time})=>a.playState==='running'&&Number(a.currentTime)>time+50),label+' '+JSON.stringify({samples:before.map(({a,time,state})=>({time,next:a.currentTime,state})),css:getComputedStyle(node,'::before').animationPlayState,inView:node.dataset.inView,display:getComputedStyle(node).display,folded:root.classList.contains('menus-idle')}));
  }
  try {
    root.setAttribute('data-avengers-awake','true');
    await moving(header,'visible header reactor advances its native animation');
    document.dispatchEvent(new WheelEvent('wheel',{deltaY:100,cancelable:true,bubbles:true}));
    await stopped(header,'folded header has no running reactor animation',true);
    const clockNode=document.getElementById('clock'),clockText=clockNode.textContent;
    let upperMutations=0;const upperObserver=new MutationObserver(entries=>upperMutations+=entries.length);
    for(const id of ['clock','schedule-title','connection'])upperObserver.observe(document.getElementById(id),{attributes:true,childList:true,characterData:true,subtree:true});
    await wait(1200);upperObserver.disconnect();
    check(upperMutations===0&&clockNode.textContent===clockText,'folded upper clock and connection have no repeated DOM writes');
    document.dispatchEvent(new WheelEvent('wheel',{deltaY:-100,cancelable:true,bubbles:true}));
    check(!root.classList.contains('menus-idle')&&clockNode.textContent===clock(serverNow()),'restored menu immediately catches up its clock');
    await wait(100);
    await moving(header,'restored menu resumes header reactor');
    await stopped(energy,'cinematic-hidden hero reactor timeline is stopped');
    check(getComputedStyle(count).visibility==='hidden','playing cinematic hides countdown');
    await stopped(count,'hidden countdown blink timeline is stopped');
    await moving(caption,'visible cinematic arrival keeps its intended blink');
    await moving(probe,'visible boss frame keeps its intended blink');
    probe.dataset.cinematicVisible='false';
    await stopped(caption,'offscreen cinematic caption timeline is stopped');
    await stopped(probe,'offscreen boss frame timeline is stopped');
    probe.dataset.cinematicVisible='true';
    root.setAttribute('data-avengers-awake','false');
    await stopped(caption,'background cinematic caption timeline is stopped');
    await stopped(probe,'background boss frame timeline is stopped');
    root.setAttribute('data-avengers-awake','true');probe.dataset.cinematic='still';
    check(getComputedStyle(count).visibility==='visible','last frame restores the countdown');
    await moving(count,'visible last-frame countdown resumes its alert blink');
    delete probe.dataset.cinematic;
    await moving(energy,'normal boss card resumes its reactor');
    // The runtime observer supplies the visibility flag; no extra frame/poll loop.
    const spacer=document.createElement('div');spacer.style.height='1500px';document.querySelector('main').append(spacer);
    window.scrollTo(0,document.documentElement.scrollHeight);await wait(200);
    check(realHero.dataset.cinematicVisible==='false','actual runtime observer marks offscreen hero');
    window.scrollTo(0,0);await wait(200);
    check(realHero.dataset.cinematicVisible==='true','actual runtime observer restores visible hero');spacer.remove();
    check(!window.previewErrors?.length,'no production page errors');
    return {checks:checks.length};
  } finally {probe.remove();root.classList.remove('menus-idle');root.setAttribute('data-avengers-awake','true');}
})()

// Local, accessible hover/click help. It moves existing content; IDs remain unique and live.
const WebHiddenHelp=(()=>{
  const host=document.getElementById('hidden-help-balloon'),body=document.getElementById('hidden-help-body');
  if(!host || !body) return {close:()=>{}};
  let active=null,pinned=false,hideTimer=null,ignoreFocus=false;
  function cancelHide() {clearTimeout(hideTimer);hideTimer=null;}
  function close() {
    cancelHide();if(!active) return;
    const {panel,origin,next,trigger}=active;
    panel.hidden=true;
    if(next && next.parentNode===origin) origin.insertBefore(panel,next);else origin.append(panel);
    trigger.setAttribute('aria-expanded','false');host.hidden=true;active=null;pinned=false;
  }
  function position(trigger) {
    const bounds=trigger.getBoundingClientRect(),popup=host.getBoundingClientRect(),margin=12;
    const left=Math.max(margin,Math.min(bounds.right-popup.width,window.innerWidth-popup.width-margin));
    const above=bounds.bottom+8+popup.height>window.innerHeight-margin;
    const top=Math.max(margin,Math.min(above?bounds.top-popup.height-8:bounds.bottom+8,window.innerHeight-popup.height-margin));
    host.style.left=left+'px';host.style.top=top+'px';host.classList.toggle('is-above',above);
    host.style.setProperty('--help-arrow-left',Math.max(18,Math.min(popup.width-18,bounds.left+bounds.width/2-left))+'px');
  }
  function open(trigger,pin=false) {
    cancelHide();
    if(active?.trigger===trigger) {pinned=pinned || pin;return;}
    close();
    const panel=document.getElementById(trigger.dataset.helpPanel);
    if(!panel) return;
    active={trigger,panel,origin:panel.parentNode,next:panel.nextSibling};pinned=pin;
    document.getElementById('hidden-help-title').textContent=trigger.dataset.helpTitle || '설명';
    body.append(panel);panel.hidden=false;host.hidden=false;trigger.setAttribute('aria-expanded','true');position(trigger);
  }
  function later() {
    cancelHide();if(pinned || !active) return;
    hideTimer=setTimeout(()=>{if(!host.contains(document.activeElement) && document.activeElement!==active?.trigger) close();},180);
  }
  for(const trigger of document.querySelectorAll('.hidden-help-trigger')) {
    trigger.setAttribute('aria-label',trigger.dataset.helpTitle || '설명');
    trigger.addEventListener('pointerenter',event=>{if(event.pointerType==='mouse') open(trigger);});
    trigger.addEventListener('pointerleave',event=>{if(event.pointerType==='mouse') later();});
    trigger.addEventListener('focus',()=>{if(!ignoreFocus) open(trigger);});
    trigger.addEventListener('blur',event=>{if(!host.contains(event.relatedTarget)) later();});
    trigger.addEventListener('click',event=>{
      if(active?.trigger===trigger && pinned) close();
      else {open(trigger,true);if(event.detail===0) document.getElementById('hidden-help-close').focus({preventScroll:true});}
    });
    trigger.addEventListener('keydown',event=>{
      if(event.key==='ArrowDown') {event.preventDefault();open(trigger,true);document.getElementById('hidden-help-close').focus({preventScroll:true});}
    });
  }
  host.addEventListener('pointerenter',cancelHide);host.addEventListener('pointerleave',later);
  host.addEventListener('focusin',cancelHide);host.addEventListener('focusout',event=>{if(!host.contains(event.relatedTarget)) later();});
  document.getElementById('hidden-help-close')?.addEventListener('click',()=>{
    const trigger=active?.trigger;close();ignoreFocus=true;try {trigger?.focus({preventScroll:true});} finally {ignoreFocus=false;}
  });
  document.addEventListener('click',event=>{if(active && !host.contains(event.target) && !active.trigger.contains(event.target)) close();},true);
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape' && active) {event.preventDefault();const trigger=active.trigger;close();ignoreFocus=true;try {trigger.focus({preventScroll:true});} finally {ignoreFocus=false;}}
  });
  document.addEventListener('scroll',event=>{if(active && !host.contains(event.target)) close();},{capture:true,passive:true});
  window.addEventListener('resize',close);window.addEventListener('pagehide',close);
  document.addEventListener('visibilitychange',()=>{if(document.hidden) close();});
  window.addEventListener('bosstimer-font-changed',close);
  return {close};
})();

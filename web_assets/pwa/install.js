/* Persistent mobile install entry; no polling, dismissal cookie or auto-prompt. */
(()=>{'use strict';
  const ua=navigator.userAgent;
  const apple=/iPhone|iPad|iPod/i.test(ua) || (navigator.platform==='MacIntel' && navigator.maxTouchPoints>1);
  const android=/Android/i.test(ua) && /Chrome\//i.test(ua) && !/SamsungBrowser|EdgA|OPR\//i.test(ua);
  if(!window.isSecureContext || (!apple && !android)) return;
  if(window.__btPwaInstallStarted) return;
  window.__btPwaInstallStarted=true;
  const mode=matchMedia('(display-mode: standalone), (display-mode: fullscreen)');
  let deferred=null, installed=false, busy=false, generation=0, bar, button, help;
  function render(){
    if(!bar) return;
    bar.hidden=installed || mode.matches || navigator.standalone===true;
    button.disabled=busy;
    button.textContent=deferred?'앱 설치':'설치 안내';
  }
  function installedNow(){installed=true;deferred=null;generation++;render();}
  async function checkInstalled(){
    render();
    if(mode.matches || navigator.standalone===true || installed || deferred || !android || !navigator.getInstalledRelatedApps) return;
    const current=++generation;
    try {
      const apps=await navigator.getInstalledRelatedApps();
      if(current!==generation || deferred) return;
      installed=apps.some(app=>app.platform==='webapp' &&
        ((app.url && new URL(app.url,location.href).href===new URL('/manifest.webmanifest',location.href).href) ||
         (app.id && new URL(app.id,location.href).href===new URL('/',location.href).href)));
    } catch(_) { /* Unknown installation status: keep the manual entry available. */ }
    render();
  }
  window.addEventListener('beforeinstallprompt',event=>{
    event.preventDefault();deferred=event;installed=false;generation++;render();
  });
  window.addEventListener('appinstalled',installedNow);
  mode.addEventListener('change',render);
  window.addEventListener('pageshow',checkInstalled);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden) checkInstalled();});
  function mount(){
    bar=document.createElement('section');bar.id='bt-pwa-install';
    bar.setAttribute('aria-label','보스 스케줄 앱 설치');
    const text=document.createElement('span');text.textContent='홈 화면에 설치하고 앱으로 열어보세요.';
    button=document.createElement('button');button.type='button';
    help=document.createElement('p');help.id='bt-pwa-install-help';help.hidden=true;
    help.textContent=apple?'브라우저의 공유 버튼 → 홈 화면에 추가 → 웹 앱으로 열기를 켜고 추가하세요. 항목이 없다면 Safari에서 이 주소를 열어주세요. 설치 후 홈 화면의 보스 스케줄 아이콘으로 실행하세요.':'Chrome 오른쪽 위 ⋮ 메뉴 → 앱 설치 또는 홈 화면에 추가를 선택하세요. 이미 설치했다면 홈 화면의 보스 스케줄 아이콘으로 열어주세요.';
    button.setAttribute('aria-controls',help.id);button.setAttribute('aria-expanded','false');
    button.addEventListener('click',async()=>{
      if(busy) return;
      const event=deferred;
      // An event can be consumed only once. A cancelled prompt never hides this entry.
      deferred=null;
      if(!event){help.hidden=false;button.setAttribute('aria-expanded','true');render();return;}
      busy=true;render();
      try {await event.prompt();await event.userChoice;}
      catch(_) { /* Browser may suppress its dialog; manual menu instructions remain. */ }
      finally {
        busy=false;
        if(!installed){help.hidden=false;button.setAttribute('aria-expanded','true');}
        render();
      }
    });
    bar.append(text,button,help);document.body.prepend(bar);render();checkInstalled();
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',mount,{once:true});
  else mount();
})();

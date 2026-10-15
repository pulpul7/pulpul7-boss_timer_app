// Shared keyboard reload guard, embedded in both page scripts. The server enforces it too.
const WebNavigationGuard=(()=>{
  const key='BossTimer.webReloadAt.v1', interval=5000;
  let acceptedAt=Date.now();
  try {localStorage.setItem(key,String(acceptedAt));} catch(_) {}
  window.addEventListener('keydown',event=>{
    const reload=event.key==='F5' || ((event.ctrlKey || event.metaKey) && String(event.key).toLowerCase()==='r');
    if(!reload || event.altKey) return;
    const now=Date.now();
    let last=acceptedAt;
    try {
      const shared=Number(localStorage.getItem(key));
      if(Number.isFinite(shared) && shared<=now) last=Math.max(last,shared);
    } catch(_) {}
    const wait=interval-Math.max(0,now-last);
    if(wait>0) {
      event.preventDefault();
      const message=`새로고침은 5초에 한 번 가능합니다. ${Math.ceil(wait/1000)}초 뒤 다시 시도해주세요.`;
      if(typeof toast==='function') toast(message);
      else {const notice=document.getElementById('notice');if(notice) notice.textContent=message;}
      return;
    }
    acceptedAt=now;
    try {localStorage.setItem(key,String(now));} catch(_) {}
  },true);
  return {logoutKey:'BossTimer.webLoginAfter.v1'};
})();

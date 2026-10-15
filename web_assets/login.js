'use strict';
// BEGIN WEB NAVIGATION GUARD
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
// END WEB NAVIGATION GUARD
const pin = document.getElementById('pin'), enter = document.getElementById('enter');
const notice = document.getElementById('notice');
const nickname = document.getElementById('nickname');
let ready = false, busy = false, checking = false, retryTimer = null;
let nextSessionAt=0, failures=0;
const needsLogin = new URLSearchParams(location.search).has('login');
let nextLoginAt=0, loginTimer=null, loginNotice=notice.textContent, loginWaitLabel='로그아웃 후', loginRedirectPending=false;
try {
  const stored=Number(localStorage.getItem(WebNavigationGuard.logoutKey));
  if(Number.isFinite(stored)) nextLoginAt=performance.now()+Math.min(300000,Math.max(0,stored-Date.now()));
} catch(_) {
  if(new URLSearchParams(location.search).has('logout')) nextLoginAt=performance.now()+2000;
}
function paintLoginControls() {
  clearTimeout(loginTimer);
  const wait=Math.max(0,nextLoginAt-performance.now());
  if(!wait && loginRedirectPending && !busy && !checking) {
    loginRedirectPending=false;location.replace('/');return;
  }
  enter.disabled=!ready || busy || checking || wait>0;
  enter.textContent=wait>0?`입장 (${Math.ceil(wait/1000)}초)`:'입장';
  notice.textContent=wait>0?`${loginWaitLabel} ${Math.ceil(wait/1000)}초 뒤 접속할 수 있습니다.`:loginNotice;
  if(wait>0) loginTimer=setTimeout(paintLoginControls,Math.min(wait,1000));
}
function setLoginNotice(message) {loginNotice=message;paintLoginControls();}
try { nickname.value = localStorage.getItem('bt_nickname') || 'guest'; } catch (_) {}
async function session() {
  if (checking || busy || document.hidden) return;
  clearTimeout(retryTimer);
  const remaining=nextSessionAt-performance.now();
  if(remaining>0) {retryTimer=setTimeout(session,remaining);return;}
  checking = true;
  paintLoginControls();
  let wait=0;
  const controller = new AbortController(), timer = setTimeout(()=>controller.abort(),4000);
  try {
    const response = await fetch('/api/session',{cache:'no-store',signal:controller.signal});
    const retry=Number(response.headers.get('Retry-After'));
    if(Number.isFinite(retry) && retry>0) wait=Math.min(retry,300)*1000;
    const data = await response.json();
    if (response.ok && data.authenticated) {ready=true;loginRedirectPending=true;return;}
    ready = response.ok && data.available && data.configured;
    setLoginNotice(ready ? (needsLogin || data.reauth_required ?
      '저장된 로그인이 만료되었습니다. 닉네임과 비밀번호를 다시 입력해주세요.' : '닉네임과 비밀번호를 입력해주세요.') :
      data.message || '관리자가 프로그램의 웹 → 비밀번호에서 먼저 설정해야 합니다.');
    notice.className = ready ? 'muted' : 'warning';
    paintLoginControls();
  } catch (error) {
    ready = false; enter.disabled = true;
    setLoginNotice('서버 연결을 확인해주세요. 자동으로 다시 확인합니다.');
  } finally {
    clearTimeout(timer); checking = false;
    paintLoginControls();
    failures=ready?0:failures+1;
    const delay=Math.max(wait,15000*2**Math.min(Math.max(0,failures-1),3))+Math.floor(Math.random()*1000);
    nextSessionAt=performance.now()+delay;
    if (!ready && !document.hidden) retryTimer = setTimeout(session,delay);
  }
}
document.getElementById('login').addEventListener('submit',async event => {
  event.preventDefault(); if (!ready || busy || checking || performance.now()<nextLoginAt) {paintLoginControls();return;}
  busy = true; setLoginNotice('확인 중');
  const controller = new AbortController(), timer = setTimeout(()=>controller.abort(),8000);
  try {
    const response = await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pin:pin.value,nickname:nickname.value.trim() || 'guest'}),signal:controller.signal});
    pin.value = '';
    const data = await response.json();
    if (response.ok) {
      try { localStorage.setItem('bt_nickname',nickname.value.trim() || 'guest'); } catch (_) {}
      loginRedirectPending=true;return;
    }
    const retry=Number(response.headers.get('Retry-After')) || Number(data.retry_after) || 0;
    nextLoginAt=performance.now()+Math.max(2000,Math.min(300,Math.max(0,retry))*1000);loginWaitLabel='다시 시도하려면';
    setLoginNotice(data.message || '입장하지 못했습니다.'); notice.className = 'warning';
  } catch (error) {
    nextLoginAt=performance.now()+2000;loginWaitLabel='다시 시도하려면';
    setLoginNotice('연결을 확인하고 다시 시도해주세요.');
  }
  finally { clearTimeout(timer); busy = false; paintLoginControls();pin.focus(); }
});
session();
window.addEventListener('pageshow',event=>{if(event.persisted) {pin.value='';session();}});
window.addEventListener('online',session);
window.addEventListener('storage',event=>{
  if(event.key!==WebNavigationGuard.logoutKey || !event.newValue) return;
  const until=Number(event.newValue);
  if(Number.isFinite(until)) {
    nextLoginAt=performance.now()+Math.min(300000,Math.max(0,until-Date.now()));loginWaitLabel='로그아웃 후';paintLoginControls();
  }
});
document.addEventListener('visibilitychange',()=>{clearTimeout(retryTimer);if(!document.hidden) session();});

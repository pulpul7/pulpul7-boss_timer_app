"""Bundled PIN page; mirrored from web_assets, without stored PINs."""

LOGIN_PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#151b24">
<link rel="stylesheet" href="/pwa/install.css"><script src="/pwa/install.js" defer></script>
<title>BossTimer 스케줄 입장</title><link rel="stylesheet" href="/style.css">
<script src="/login.js" defer></script></head><body><main>
<h1>BossTimer 스케줄</h1><p>관리자가 알려준 네 자리 비밀번호로 입장하세요.</p>
<form id="login"><label for="nickname">닉네임</label>
<input id="nickname" name="nickname" type="text" value="guest" maxlength="40"
 autocomplete="nickname" required aria-describedby="nickname-help">
<small id="nickname-help" class="muted">길드원은 사용하는 닉네임을, 손님은 guest 또는 원하는 닉네임을 입력하세요.</small>
<label for="pin">비밀번호</label>
<input id="pin" type="password" inputmode="numeric" pattern="[0-9]{4}" minlength="4" maxlength="4"
 autocomplete="current-password" required aria-label="네 자리 비밀번호">
<button id="enter" type="submit" disabled>입장</button></form>
<p id="notice" role="status">연결 확인 중</p>
<p class="muted">일반·게스트 비밀번호 중 안내받은 비밀번호로 입장하세요. 같은 브라우저에서는 로그인을 기억하며, 해당 비밀번호가 바뀌면 다시 입력합니다.</p>
<noscript>입장하려면 브라우저의 JavaScript를 켜주세요.</noscript>
</main></body></html>
""".encode('utf-8')

LOGIN_SCRIPT = r"""'use strict';
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
""".encode('utf-8')

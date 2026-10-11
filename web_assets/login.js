'use strict';
const pin = document.getElementById('pin'), enter = document.getElementById('enter');
const notice = document.getElementById('notice');
const nickname = document.getElementById('nickname');
let ready = false, busy = false, checking = false, retryTimer = null;
let nextSessionAt=0, failures=0;
const needsLogin = new URLSearchParams(location.search).has('login');
try { nickname.value = localStorage.getItem('bt_nickname') || 'guest'; } catch (_) {}
async function session() {
  if (checking || busy || document.hidden) return;
  clearTimeout(retryTimer);
  const remaining=nextSessionAt-performance.now();
  if(remaining>0) {retryTimer=setTimeout(session,remaining);return;}
  checking = true;
  let wait=0;
  const controller = new AbortController(), timer = setTimeout(()=>controller.abort(),4000);
  try {
    const response = await fetch('/api/session',{cache:'no-store',signal:controller.signal});
    const retry=Number(response.headers.get('Retry-After'));
    if(Number.isFinite(retry) && retry>0) wait=Math.min(retry,300)*1000;
    const data = await response.json();
    if (response.ok && data.authenticated) { ready=true; location.replace('/'); return; }
    ready = response.ok && data.available && data.configured;
    notice.textContent = ready ? (needsLogin || data.reauth_required ?
      '저장된 로그인이 만료되었습니다. 닉네임과 비밀번호를 다시 입력해주세요.' : '닉네임과 비밀번호를 입력해주세요.') :
      data.message || '관리자가 프로그램의 웹 → 비밀번호에서 먼저 설정해야 합니다.';
    notice.className = ready ? 'muted' : 'warning';
    enter.disabled = !ready || busy;
  } catch (error) {
    ready = false; enter.disabled = true;
    notice.textContent = '서버 연결을 확인해주세요. 자동으로 다시 확인합니다.';
  } finally {
    clearTimeout(timer); checking = false;
    failures=ready?0:failures+1;
    const delay=Math.max(wait,15000*2**Math.min(Math.max(0,failures-1),3))+Math.floor(Math.random()*1000);
    nextSessionAt=performance.now()+delay;
    if (!ready && !document.hidden) retryTimer = setTimeout(session,delay);
  }
}
document.getElementById('login').addEventListener('submit',async event => {
  event.preventDefault(); if (!ready || busy) return;
  busy = true; enter.disabled = true; notice.textContent = '확인 중';
  const controller = new AbortController(), timer = setTimeout(()=>controller.abort(),8000);
  try {
    const response = await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pin:pin.value,nickname:nickname.value.trim() || 'guest'}),signal:controller.signal});
    pin.value = '';
    const data = await response.json();
    if (response.ok) {
      try { localStorage.setItem('bt_nickname',nickname.value.trim() || 'guest'); } catch (_) {}
      location.replace('/'); return;
    }
    notice.textContent = data.message || '입장하지 못했습니다.'; notice.className = 'warning';
  } catch (error) { notice.textContent = '연결을 확인하고 다시 시도해주세요.'; }
  finally { clearTimeout(timer); busy = false; enter.disabled = !ready; pin.focus(); }
});
session();
window.addEventListener('pageshow',event=>{if(event.persisted) {pin.value='';session();}});
window.addEventListener('online',session);
document.addEventListener('visibilitychange',()=>{clearTimeout(retryTimer);if(!document.hidden) session();});

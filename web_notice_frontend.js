// Shared alarm timing; PC delivery stays inside this page and makes no API requests.
const WebNotices=(()=>{
  const defaults=[2,null,null], gap=121000, windowMs=30000;
  let scope='',times=[...defaults],enabled=true,seen={},plan=null,planEvents=null,planPrefs=null;
  let testUntil=0,testTimer=null;
  const el=id=>document.getElementById(id);
  const pc=()=>deviceProfile().pc;
  function valid(value) {
    if(!Array.isArray(value) || value.length!==3) return false;
    const active=value.filter(v=>v!==null);
    return active.length>0 && new Set(active).size===active.length && active.every(v=>Number.isInteger(v) && v>=0 && v<=10);
  }
  function loadSeen() {
    try {
      const saved=JSON.parse(localStorage.getItem(scope+':sent') || '{}'),now=Date.now();
      for(const [key,at] of Object.entries(saved).slice(-1500)) {
        if(key.length<=800 && Number.isFinite(at) && at>now-14400000 && at<=now) seen[key]=at;
      }
    } catch(_) {}
    const now=Date.now();
    seen=Object.fromEntries(Object.entries(seen).filter(([,at])=>at>now-14400000 && at<=now).slice(-1500));
  }
  function save() {
    try {localStorage.setItem(scope,JSON.stringify({times,banner:enabled}));}
    catch(_) {toast('이 브라우저에서는 알림 설정을 저장할 수 없습니다.');}
  }
  function invalidate() {plan=null;}
  function drawSettings() {
    for(let i=0;i<3;i++) {
      const select=el('notice-time-'+i);if(!select) continue;
      if(!select.options.length) {
        for(const value of [null,10,9,8,7,6,5,4,3,2,1,0]) {
          const option=document.createElement('option');option.value=value===null?'off':String(value);
          option.textContent=value===null?'사용안함':value===0?'젠':`${value}분 전`;select.append(option);
        }
      }
      select.value=times[i]===null?'off':String(times[i]);
      for(const option of select.options) {
        const value=option.value==='off'?null:Number(option.value);
        option.disabled=value!==null && times.some((v,j)=>j!==i && v===value);
      }
    }
    const summary=times.filter(v=>v!==null).map(v=>v===0?'젠':`${v}분 전`).join(' · ');
    if(el('notice-setting-note')) el('notice-setting-note').textContent=`알림 ${times.filter(v=>v!==null).length}회: ${summary}. 묶음의 첫 보스 시각을 기준으로 안내합니다.`;
  }
  function refresh(owner,visible) {
    const next='BossTimer.webNotice.v1:'+JSON.stringify([owner,data?.server_name || '',data?.guild_name || '',data?.season || '']);
    if(next!==scope) {
      scope=next;times=[...defaults];enabled=true;seen={};invalidate();
      try {const saved=JSON.parse(localStorage.getItem(scope) || '{}');if(valid(saved.times)) times=saved.times;enabled=typeof saved.banner==='boolean'?saved.banner:true;} catch(_) {}
      loadSeen();drawSettings();
    }
    if(el('notice-settings')) el('notice-settings').hidden=!visible;
    WebNoticeSound.refresh(owner,visible).catch(()=>{});
    if(pc()) paint();
  }
  function paint(message='') {
    if(!pc()) return;
    el('push-controls').hidden=false;
    const toggle=el('push-toggle');toggle.textContent=enabled?'알림 ON':'알림 OFF';
    toggle.setAttribute('aria-pressed',String(enabled));toggle.disabled=!data;
    const wait=Math.max(0,testUntil-performance.now());
    el('push-test').disabled=wait>0;
    el('push-test').title=wait>0?'테스트는 30초 간격으로 가능합니다.':'웹페이지 오른쪽 아래 배너를 테스트합니다.';
    el('push-note').textContent=message || '이 웹페이지 안에서 알람 ON인 보스를 설정한 시각에 안내합니다.';
    clearTimeout(testTimer);if(wait>0) testTimer=setTimeout(()=>paint(),Math.ceil(wait));
  }
  function banner(title,body) {
    let stack=el('web-banner-stack');
    if(!stack) {
      stack=document.createElement('aside');stack.id='web-banner-stack';stack.className='web-banner-stack';
      stack.setAttribute('aria-label','보스 알림 배너');
    }
    // Escape the schedule app's stacking context, beneath snow/rain artwork.
    if(stack.parentNode!==document.body) document.body.append(stack);
    while(stack.children.length>=3) stack.firstElementChild.remove();
    const card=document.createElement('section');card.className='web-boss-banner';card.setAttribute('role','status');
    const heading=document.createElement('strong');heading.textContent=title;
    const text=document.createElement('p');text.textContent=body;text.title=body;
    const close=document.createElement('button');close.type='button';close.textContent='닫기';
    close.setAttribute('aria-label','알림 배너 닫기');
    let timer=null;const remove=()=>{clearTimeout(timer);card.remove();};close.addEventListener('click',remove);
    card.append(heading,close,text);stack.append(card);timer=setTimeout(remove,10000);
    // Sound failures must never interrupt banner delivery or schedule rendering.
    WebNoticeSound.notify().catch(()=>{});
  }
  function groups() {
    if(plan && planEvents===data.events && planPrefs===alarmPrefs) return plan;
    const rows=data.events.filter(row=>isBoss(row) && Number.isFinite(row.at) && alarmOn(row)).sort((a,b)=>a.at-b.at || a.name.localeCompare(b.name));
    plan=[];let last=null;
    for(const row of rows) {
      if(last===null || row.at-last>gap) plan.push({at:row.at,rows:[row]});else plan[plan.length-1].rows.push(row);
      last=row.at;
    }
    planEvents=data.events;planPrefs=alarmPrefs;return plan;
  }
  function occurrence(row,minutes) {
    const canonical=data.boss_catalog?.find(b=>b.id===row.catalog_id)?.name;
    const special=canonical==='길던' || canonical==='사용자이벤트';
    return JSON.stringify([row.catalog_id || row.name,special?row.name.replace(/\s/g,''):'',row.scheduled_at,minutes]);
  }
  function tick(now) {
    if(!pc() || !enabled || !data || leaving || data.schedule_stale || !scope) return;
    for(const group of groups()) for(const minutes of times.filter(v=>v!==null)) {
      const due=group.at-minutes*60000;
      if(now<due || now>=due+windowMs) continue;
      loadSeen();
      const fresh=group.rows.filter(row=>!seen[occurrence(row,minutes)]);if(!fresh.length) continue;
      const names=[...new Set(fresh.map(row=>displayName(row)))];
      const shown=[];let length=0;
      for(const name of names) {if(length+name.length+(shown.length?3:0)>550) break;shown.push(name);length+=name.length+3;}
      const body=shown.join(' + ')+(shown.length<names.length?` 외 ${names.length-shown.length}개`:'');
      banner(minutes===0?'젠 알림':`${minutes}분 전 알림`,body);
      const at=Date.now();for(const row of fresh) seen[occurrence(row,minutes)]=at;
      const rows=Object.entries(seen).sort((a,b)=>a[1]-b[1]).slice(-1500);
      seen=Object.fromEntries(rows);
      try {localStorage.setItem(scope+':sent',JSON.stringify(seen));} catch(_) {}
    }
  }
  function toggle() {if(!data) return;enabled=!enabled;save();paint();if(enabled) tick(serverNow());else {el('web-banner-stack')?.replaceChildren();WebNoticeSound.stop();}}
  function test() {
    if(!pc()) return;
    const wait=testUntil-performance.now();
    if(wait>0) {toast(`테스트는 30초 간격으로 가능합니다. ${Math.ceil(wait/1000)}초 뒤 다시 시도해주세요.`);return;}
    try {
      banner('배너 테스트','알람 ON인 보스를 설정한 시각에 안내합니다.');
      testUntil=performance.now()+30000;
      paint('오른쪽 아래 테스트 배너를 확인해주세요. 실제 알림은 알림 ON 상태에서 동작합니다.');
    } catch(error) {
      console.warn('[BossTimer] web_banner_test_failed',error);
      paint('배너를 표시하지 못했습니다. 페이지를 새로고침한 뒤 다시 테스트해주세요.');
      toast('배너 표시 실패 · 페이지를 새로고침한 뒤 다시 테스트해주세요.');
    }
  }
  for(let i=0;i<3;i++) el('notice-time-'+i)?.addEventListener('change',()=>{
    const values=[0,1,2].map(j=>{const v=el('notice-time-'+j).value;return v==='off'?null:Number(v);});
    if(!valid(values)) {toast('서로 다른 시각으로 최소 한 번은 선택해주세요.');drawSettings();return;}
    times=values;save();drawSettings();schedulePushSync();
  });
  window.addEventListener('storage',event=>{
    if(event.key===scope) {
      try {const saved=JSON.parse(event.newValue || '{}');if(valid(saved.times)) times=saved.times;enabled=typeof saved.banner==='boolean'?saved.banner:true;drawSettings();paint();schedulePushSync();} catch(_) {}
    } else if(event.key===scope+':sent') loadSeen();
  });
  return {refresh,invalidate,paint,tick,toggle,test,times:()=>[...times]};
})();

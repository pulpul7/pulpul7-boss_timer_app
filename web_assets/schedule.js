'use strict';
const element = id => document.getElementById(id);
const instant = value => Date.parse(value.replace(/(\.\d{3})\d+/, '$1'));
const types = {boss:'일반',fixed:'고정',invasion:'침공',break:'휴식',maintenance:'점검',server_open:'서버 오픈'};
const isBoss = row => ['boss','fixed','invasion'].includes(row.kind);
const pad = n => String(n).padStart(2,'0');
let data=null, serverTime=0, received=0, receivedWall=0, lastContact=0, connected=false, leaving=false;
let renderedKey='', copyKey='', summaryKey='', timingRows=[], copyTimingRows=[], followingState=null, pollTimer=null, polling=false;
const pollInterval=15000;
let pollFailures=0;
let selectedFilter='all', activeKey='', allDay='', mainKey='', mainRows=[];
let nextPollAt=0, retryWait=0;
let showRest=true;
try { showRest=localStorage.getItem('BossTimer.webRest.visible')!=='false'; } catch (_) {}
let alarmScope='', alarmPrefs={defaultOn:true,overrides:{}}, alarmViewKey='';
let toastTimer=null, imageRows=[], imageInfo=null, imagePage=0, imageBlob=null, imageURL='', imageJob=0;
let logoutUnlocked=false, logoutPending=false, logoutReadyAt=0, logoutDelayTimer=null;
let logoutUnlockUntil=0, logoutUnlockTimer=null;
function node(tag,value='',className='') { const n=document.createElement(tag); n.textContent=value; n.className=className; return n; }
function setText(target,value) { if (target.textContent!==value) target.textContent=value; }
function serverNow() {
  const elapsed=performance.now()-received, wallElapsed=Date.now()-receivedWall;
  // Some mobile browsers suspend performance.now during device sleep.
  return serverTime+(wallElapsed>=0 && Math.abs(wallElapsed-elapsed)>2000 ? wallElapsed : elapsed);
}
function offsetOf(value) {
  const m=/([+-])(\d{2}):(\d{2})$/.exec(value || '');
  return m ? (m[1]==='-'?-1:1)*(Number(m[2])*60+Number(m[3])) : 0;
}
function localDate(milliseconds) { return new Date(milliseconds+offsetOf(data.window_start)*60000); }
function dayKey(milliseconds) { return localDate(milliseconds).toISOString().slice(0,10); }
function clock(milliseconds,full=false) {
  const d=localDate(milliseconds);
  return (full ? `${d.getUTCMonth()+1}/${d.getUTCDate()} ` : '')+`${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}
function dateLabel(milliseconds) {
  const d=localDate(milliseconds);
  return `${pad(d.getUTCMonth()+1)}월 ${pad(d.getUTCDate())}일 ${['일','월','화','수','목','금','토'][d.getUTCDay()]}요일`;
}
function contextTitle(includeSeason=true) {
  return [data.server_name,data.guild_name,includeSeason && data.season?data.season+'차':''].filter(Boolean).join(' ');
}
function fitHeader() {
  if (!data) return;
  const title=element('context'), full=contextTitle();
  setText(title,full);title.title=full;title.setAttribute('aria-label',full);
  if (title.scrollWidth>title.clientWidth) setText(title,contextTitle(false));
}
function restDuration(row) {
  const minutes=Math.max(0,Math.round((row.end-row.at)/60000)), hours=Math.floor(minutes/60);
  return hours?`${hours}시간 ${minutes%60}분`:`${minutes}분`;
}
function isOngoingRest(row,now) { return row.kind==='break' && row.at<=now && row.end>now; }
function restRowScale(row) {
  return typeof row.row_scale==='number' && Number.isFinite(row.row_scale) ?
    Math.min(12,Math.max(0.5,row.row_scale)):1;
}
function applyRestHeight(target,row) {
  if(row.kind==='break') target.style.setProperty('--rest-row-scale',String(restRowScale(row)));
}
function refreshRestFilter() {
  const button=element('rest-filter');setText(button,showRest?'휴식 ON':'휴식 OFF');
  button.setAttribute('aria-pressed',String(showRest));
}
function countdown(milliseconds,showSeconds=true) {
  const unit=showSeconds?1000:60000;
  const seconds=Math.max(0,Math.ceil(milliseconds/unit))*(unit/1000), days=Math.floor(seconds/86400);
  return (days?`${days}일 `:'')+`${pad(Math.floor(seconds%86400/3600))}:${pad(Math.floor(seconds%3600/60))}`+(showSeconds?`:${pad(seconds%60)}`:'');
}
function describe(row,now,preciseIds) {
  if (row.kind==='break') return restDuration(row);
  return row.at>now ? countdown(row.at-now,preciseIds.has(row.id) || withinHour(row,now)) : '경과';
}
function periodEnd(now,sharing=false) {
  const value=sharing?element('period').value:'default';
  if (value==='all') return instant(data.window_end);
  if (value==='24' || value==='48') return Math.min(instant(data.window_end),now+Number(value)*3600000);
  const d=localDate(now), extra=d.getUTCHours()>=17 ? 2 : 1;
  d.setUTCDate(d.getUTCDate()+extra); d.setUTCHours(8,0,0,0);
  return Math.min(instant(data.window_end),d.getTime()-offsetOf(data.window_start)*60000);
}
function included(row,sharing=false) {
  if (!sharing) return showRest || row.kind!=='break';
  return (element('include-fixed').checked || row.kind!=='fixed') && (element('include-break').checked || row.kind!=='break');
}
function selectedDay(now) {
  if(selectedFilter==='all' && allDay) return allDay;
  const day=localDate(now);
  day.setUTCDate(day.getUTCDate()+({tomorrow:1,after:2}[selectedFilter] || 0));
  return day.toISOString().slice(0,10);
}
function dayStart(day) { return Date.parse(day+'T00:00:00Z')-offsetOf(data.window_start)*60000; }
function nextDay(day) { return new Date(Date.parse(day+'T00:00:00Z')+86400000).toISOString().slice(0,10); }
function onDay(row,day) {
  const start=dayStart(day), end=start+86400000;
  return (row.at>=start && row.at<end) || (row.kind==='break' && row.at<start && row.end>start);
}
function hasDay(day) { return data.events.some(row=>included(row) && onDay(row,day)); }
function moveDay(next) {
  if (!data || selectedFilter!=='all') return;
  const now=serverNow(), current=selectedDay(now), target=next?nextDay(current):dayKey(now);
  if(target===current || (next && !hasDay(target))) return;
  allDay=next?target:''; tick(); refreshAlarms();
  if(next) window.scrollTo({top:0,behavior:'auto'});
}
function refreshDays(now) {
  const all=selectedFilter==='all'; element('day-navigation').hidden=!all;
  element('schedule-bottom-nav').hidden=!all;
  if(!all) return;
  const current=selectedDay(now), today=current===dayKey(now);
  element('day-today').disabled=today; element('day-today').setAttribute('aria-pressed',String(today));
  element('day-next').disabled=!hasDay(nextDay(current));
  element('day-next-bottom').disabled=element('day-next').disabled;
}
function majorBoss(row) {
  const name=row.name.replace(/\s*★\s*$/,'').replace(/\s/g,'');
  if(name==='지옥성채정예') return false;
  if(typeof row.major==='boolean') return row.major;
  return row.important===true || (row.kind==='fixed' && (name==='지옥성채' ||
    /^(공성전|쟁탈전|길던|길드던전|트리니티리그|점령전|방어전)/.test(name)));
}
function mainEvents(now) {
  const day=selectedDay(now), key=[data.schedule_version,data.major_version,selectedFilter,day,showRest].join('|');
  if(key!==mainKey) {
    mainKey=key;
    mainRows=data.events.filter(row=>onDay(row,day) && included(row) &&
      (selectedFilter==='all' || (isBoss(row) && majorBoss(row))));
  }
  return mainRows;
}
function visibleRows(now,sharing=false) {
  const eligible=sharing?data.events.filter(row=>included(row,true)):mainEvents(now), past=eligible.filter(row=>isBoss(row) && row.at<now).slice(-2);
  const end=sharing?periodEnd(now,true):Infinity, future=eligible.filter(row=>(row.at>=now || (row.kind==='break' && row.end>now)) && row.at<=end);
  return [...past,...future].sort((a,b)=>a.at-b.at || a.name.localeCompare(b.name,'ko'));
}
function colorOf(row,sharing=false) {
  const fallback={boss:'#72b7ff',fixed:'#debdff',invasion:'#ffbda7',break:'#adccb8',maintenance:'#f8cd8a',server_open:'#f8cd8a'}[row.kind] || '#edf1f7';
  const enabled=!sharing || element(row.kind==='fixed'?'fixed-colors':'boss-colors').checked;
  if (row.kind==='break' && enabled) return '#153647';
  if (!enabled || !/^#[0-9a-f]{6}$/i.test(row.text_color || '')) return enabled?fallback:'#edf1f7';
  return row.text_color.toLowerCase();
}
function backgroundOf(row,sharing=false) {
  const enabled=!sharing || element(row.kind==='fixed'?'fixed-colors':'boss-colors').checked;
  if (row.kind==='break') return enabled?'#87ceeb':'';
  return enabled && /^#[0-9a-f]{6}$/i.test(row.bg_color || '') ? row.bg_color.toLowerCase() : '';
}
function applyBackground(target,row,sharing=false) {
  const background=row?backgroundOf(row,sharing):'';
  target.style.backgroundColor=background;
  target.classList.toggle('rest-highlight',row?.kind==='break' && background==='#87ceeb');
}
function bossKey(row) { return `${row.kind}:${row.name}`; }
function dayAlarmKey(row) { return `day:${dayKey(row.at)}:${bossKey(row)}`; }
function alarmOn(row) {
  const dated=dayAlarmKey(row), key=bossKey(row);
  if(Object.hasOwn(alarmPrefs.overrides,dated)) return alarmPrefs.overrides[dated];
  return Object.hasOwn(alarmPrefs.overrides,key)?alarmPrefs.overrides[key]:alarmPrefs.defaultOn;
}
function alarmTargets(now) {
  return (selectedFilter==='all'?data.events:mainEvents(now)).filter(isBoss);
}
function alarmView(now) { return [alarmScope,data.schedule_version,data.major_version,selectedFilter,selectedDay(now)].join('|'); }
function toggleMasterAlarms() {
  if(!data) return;
  const bosses=alarmTargets(serverNow());
  if(!bosses.length) return;
  const on=!bosses.every(alarmOn);
  if(selectedFilter==='all') alarmPrefs={defaultOn:on,overrides:{}};
  else for(const row of bosses) alarmPrefs.overrides[dayAlarmKey(row)]=on;
  saveAlarms(); refreshAlarms();
}
function readAlarms() {
  alarmPrefs={defaultOn:true,overrides:{}};
  try {
    const raw=localStorage.getItem(alarmScope);
    if (!raw || raw.length>200000) return;
    const saved=JSON.parse(raw);
    if (typeof saved.defaultOn!=='boolean' || !saved.overrides || typeof saved.overrides!=='object') return;
    alarmPrefs.defaultOn=saved.defaultOn;
    for (const [key,value] of Object.entries(saved.overrides).slice(0,2000)) {
      if (key.length<=240 && typeof value==='boolean') alarmPrefs.overrides[key]=value;
    }
  } catch (_) { /* Private browsing/storage restrictions do not block the schedule. */ }
}
function saveAlarms() {
  try { localStorage.setItem(alarmScope,JSON.stringify(alarmPrefs)); }
  catch (_) { toast('이 브라우저에서는 알람 선택을 저장할 수 없습니다. 현재 화면에서만 유지됩니다.'); }
}
function alarmButton(row) {
  const button=node('button','','alarm'); button.type='button'; button.dataset.alarm=bossKey(row); button.dataset.alarmId=row.id; updateAlarm(button,row); return button;
}
function updateAlarm(button,row) {
  const on=alarmOn(row); button.setAttribute('aria-pressed',String(on));
  button.setAttribute('aria-label',`${row.name} 웹 알람 ${on?'ON':'OFF'} · 실제 알림 미연결`);
  button.title='웹 알람 선택만 저장합니다. 실제 알림은 아직 연결되지 않습니다.';
  setText(button,on?'ON':'OFF');
}
function refreshAlarms() {
  if (!data) return;
  const now=serverNow(), bosses=alarmTargets(now), major=selectedFilter!=='all';
  alarmViewKey=alarmView(now);
  const count=bosses.filter(alarmOn).length, all=count>0 && count===bosses.length;
  const master=element('all-alarm'); master.disabled=bosses.length===0;
  master.setAttribute('aria-pressed',count && !all?'mixed':String(all));
  const label=major?'주요 알람':'전체 알람';
  setText(master,`${label} ${all?'ON':count?'일부 ON':'OFF'}`);
  master.title=major?'선택한 날짜의 주요 보스만 변경합니다.':'모든 날짜의 보스 알람을 변경합니다.';
  const byId=new Map(data.events.filter(isBoss).map(row=>[row.id,row]));
  document.querySelectorAll('[data-alarm]').forEach(button=>{ const row=byId.get(button.dataset.alarmId); if(row) updateAlarm(button,row); });
}
function nameWithTime(row,className,sharing=false) {
  const n=node('div','',className), name=node('span',row.name,'boss-name');
  name.style.color=colorOf(row,sharing); n.append(node('time',`[${clock(row.at)}] `),name); return n;
}
function withinHour(row,now) {
  return Boolean(row && row.kind!=='break' && row.at>=now && row.at-now<=3600000);
}
function preciseBossIds(now,sharing=false) {
  return new Set((sharing?data.events:mainEvents(now)).filter(row=>isBoss(row) && row.at>=now).slice(0,2).map(row=>row.id));
}
function renderSummary(now) {
  const bosses=mainEvents(now).filter(isBoss), past=bosses.filter(row=>row.at<now).slice(-2), future=bosses.filter(row=>row.at>=now);
  const first=future[0], second=future[1];
  const key=[data.schedule_version,data.major_version,selectedFilter,selectedDay(now),...past.map(row=>row.id),first?.id || '',second?.id || ''].join('|');
  if (key!==summaryKey) {
    summaryKey=key; element('recent').replaceChildren(...past.map(row=>node('s',`[${clock(row.at,true)}] ${row.name}`)));
    element('next-name').replaceChildren(...(first?[...nameWithTime(first,'').childNodes]:[node('span','다음 보스가 없습니다.')]));
    element('next-name').closest('.hero').classList.toggle('type-invasion',first?.kind==='invasion');
    element('following').classList.toggle('type-invasion',second?.kind==='invasion');
    applyBackground(element('next-name').closest('.hero'),first);
    applyBackground(element('following'),second);
    const button=element('next-alarm'); button.hidden=!first;
    element('following').hidden=!second;
    if (first) { button.dataset.alarm=bossKey(first); button.dataset.alarmId=first.id; updateAlarm(button,first); }
    else { delete button.dataset.alarm; delete button.dataset.alarmId; }
    followingState=null;
    if (second) {
      const main=node('div','','following-main'); followingState=node('div','','following-state');
      main.append(nameWithTime(second,'following-name'),followingState);
      element('following').replaceChildren(main,alarmButton(second));
    } else element('following').replaceChildren(node('span','그다음 일정이 없습니다.','muted'));
  }
  const value=first?countdown(first.at-now):'--:--:--';
  setText(element('next-countdown'),value); element('next-countdown').classList.toggle('long',value.includes('일'));
  element('next-countdown').classList.toggle('imminent',withinHour(first,now));
  element('following').classList.toggle('imminent',withinHour(second,now));
  if (second && followingState) setText(followingState,`(${countdown(second.at-now)})`);
}
function renderRows(now,preciseIds) {
  const bosses=mainEvents(now).filter(isBoss), pastIds=new Set(bosses.filter(row=>row.at<now).slice(-2).map(row=>row.id));
  const [first,second]=bosses.filter(row=>row.at>=now);
  // The next boss countdown already covers the leading rest; keep later rests between bosses.
  const rows=visibleRows(now).filter(row=>!pastIds.has(row.id) &&
    (row.kind!=='break' || !first || row.at>=first.at)), key=[data.schedule_version,data.major_version,selectedFilter,showRest,selectedDay(now),...rows.map(row=>row.id)].join('|');
  if (key!==renderedKey) {
    renderedKey=key; timingRows=[];
    const fragment=document.createDocumentFragment(), hero=element('next-name').closest('.hero'), following=element('following');
    let date=selectedDay(now);
    for (const row of rows) {
      const day=row.kind==='break' && row.at<dayStart(selectedDay(now))?selectedDay(now):dayKey(row.at);
      if (day!==date) { date=day; fragment.append(node('div',dateLabel(row.at),'day-heading')); }
      // Keep the two highlighted bosses in their actual positions among rests/events.
      if(row.id===first?.id) {fragment.append(hero);continue;}
      if(row.id===second?.id) {fragment.append(following);continue;}
      const item=node('div','',`event-row type-${row.kind}${row.important?' important':''}`);
      applyRestHeight(item,row);
      applyBackground(item,row);
      const main=node('div','','following-main event-details'), state=node('span','','event-state');
      main.append(nameWithTime(row,'following-name event-label'),state);item.append(main);
      if (isBoss(row)) item.append(alarmButton(row));
      fragment.append(item); timingRows.push({row,item,state});
    }
    if(!first) fragment.append(hero);
    if(!second) fragment.append(following);
    element('events').replaceChildren(fragment); element('empty').hidden=rows.length!==0 || bosses.length!==0;
  }
  for (const {row,item,state} of timingRows) {
    const ongoing=isOngoingRest(row,now);
    setText(state,`(${describe(row,now,preciseIds)})`); item.classList.toggle('elapsed',row.at<now && !ongoing);
    item.classList.toggle('ongoing',ongoing);
    if(row.kind==='break') item.title=`${ongoing?'휴식 중 · ':''}${clock(row.at,true)} ~ ${clock(row.end,true)}`;
    item.classList.toggle('imminent',withinHour(row,now));
  }
}
function renderCopy(now,preciseIds=preciseBossIds(now,true)) {
  if (!element('copy-dialog').open) return;
  const rows=visibleRows(now,true), key=[data.schedule_version,data.major_version,element('period').value,element('boss-colors').checked,
    element('fixed-colors').checked,element('include-fixed').checked,element('include-break').checked,...rows.map(row=>row.id)].join('|');
  if (key!==copyKey) {
    copyKey=key; copyTimingRows=[]; const fragment=document.createDocumentFragment(); let day='';
    for (const row of rows) {
      const date=dayKey(row.at); if(date!==day) {day=date;fragment.append(node('div',dateLabel(row.at),'day-heading'));}
      const item=node('div','',`event-row type-${row.kind}${row.important?' important':''}`), main=node('div','','following-main event-details');
      applyRestHeight(item,row);
      applyBackground(item,row,true);
      const state=node('span','','event-state');
      main.append(nameWithTime(row,'following-name event-label',true),state);item.append(main);
      fragment.append(item);copyTimingRows.push({row,item,state});
    }
    if(!rows.length) fragment.append(node('p','이 기간에 표시할 일정이 없습니다.','empty'));
    element('copy-events').replaceChildren(fragment);
  }
  for(const {row,item,state} of copyTimingRows) {
    setText(state,`(${describe(row,now,preciseIds)})`);item.classList.toggle('elapsed',row.at<now && !(row.kind==='break' && row.end>now));
    item.classList.toggle('imminent',withinHour(row,now));
  }
  setText(element('range'),`${clock(now,true)} ~ ${clock(periodEnd(now,true),true)}`);
}
function updateConnection() {
  if (!data) return;
  const warning=!connected || performance.now()-lastContact>pollInterval*3 || data.schedule_stale;
  element('connection').className=warning?'warning':'';
  setText(element('connection'),warning?'갱신 대기 · 마지막 일정으로 계산 중':`연결됨 · 변경 ${clock(instant(data.updated_at),true)}`);
}
function tick() {
  if (leaving || !data || document.hidden) return;
  const now=serverNow();
  setText(element('clock'),clock(now));
  setText(element('schedule-title'),dateLabel(dayStart(selectedDay(now))));
  refreshDays(now);
  const tomorrow=element('tomorrow-date'); tomorrow.hidden=!['tomorrow','after'].includes(selectedFilter);
  if(!tomorrow.hidden) setText(tomorrow,`(${selectedFilter==='after'?'모레':'내일'})`);
  const preciseIds=preciseBossIds(now);
  renderSummary(now); renderRows(now,preciseIds); renderCopy(now,preciseBossIds(now,true)); renderActive(now); updateConnection();
  if(alarmViewKey!==alarmView(now)) refreshAlarms();
}
function renderActive(now) {
  const key=[data.schedule_version,data.major_version,selectedFilter,selectedDay(now)].join('|');
  if(key===activeKey) return; activeKey=key;
  const rows=selectedDay(now)!==dayKey(now)?[]:data.active.filter(row=>selectedFilter==='all' || majorBoss(row));
  element('active').replaceChildren(...rows.map(row=>node('li',`${row.name} · ${row.status}`)));
  element('active-section').hidden=rows.length===0;
}
function applyMajorUpdates(status) {
  if(!status.major_version || status.major_version===data.major_version || !status.major_updates) return;
  data.events=data.events.map(row=>{
    const flags=status.major_updates[row.id];
    return flags?{...row,important:flags.important===true,major:flags.major===true}:row;
  });
  if(Array.isArray(status.major_active)) data.active=status.major_active;
  data.major_version=status.major_version;
  summaryKey=''; renderedKey=''; copyKey=''; activeKey='';
}
function selectFilter(value) {
  if(!['all','today','tomorrow','after'].includes(value) || value===selectedFilter) return;
  selectedFilter=value;
  document.querySelectorAll('[data-filter]').forEach(button=>{
    const active=button.dataset.filter===value;
    button.setAttribute('aria-selected',String(active)); button.tabIndex=active?0:-1;
    if(active) element('schedule-panel').setAttribute('aria-labelledby',button.id);
  });
  summaryKey=''; renderedKey=''; activeKey=''; tick(); refreshAlarms();
}
async function request(path) {
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),5000), start=performance.now();
  try {
    const response=await fetch(path,{cache:'no-store',signal:controller.signal});
    if (response.status===401 || response.status===403) { leaving=true; clearTimeout(pollTimer); location.replace('/?login=1'); throw new Error('authorization'); }
    if(response.status===429 || response.status===503) {
      const seconds=Number(response.headers.get('Retry-After'));
      if(Number.isFinite(seconds) && seconds>0) retryWait=Math.max(retryWait,Math.min(seconds,300)*1000);
    }
    const value=await response.json(); if (!response.ok) throw new Error('unavailable');
    const now=instant(value.server_time); if (!Number.isFinite(now)) throw new Error('clock');
    const end=performance.now(); serverTime=now+(end-start)/2; received=end; receivedWall=Date.now(); return value;
  } finally { clearTimeout(timer); }
}
async function poll() {
  if (leaving || polling || document.hidden) return;
  clearTimeout(pollTimer);
  const remaining=nextPollAt-performance.now();
  if(remaining>0) {pollTimer=setTimeout(poll,remaining);return;}
  nextPollAt=performance.now()+pollInterval;
  retryWait=0;
  polling=true;
  try {
    if (!data) {
      accept(await request('/api/schedule'));
    } else {
      const status=await request('/api/status?major_version='+encodeURIComponent(data.major_version || ''));
      if (!status.schedule_version || status.schedule_version!==data.schedule_version) accept(await request('/api/schedule'));
      else {
        data.schedule_stale=status.schedule_state!=='ready';
        if(status.schedule_updated_at) data.updated_at=status.schedule_updated_at;
        applyMajorUpdates(status);
      }
    }
    pollFailures=0; connected=true; lastContact=performance.now(); tick();
  } catch (_) {
    if (!leaving) {
      pollFailures++; connected=false;
      if (!data) { element('connection').textContent='스케줄 준비 중 · 자동으로 다시 확인합니다.'; element('connection').className='warning'; }
      tick();
    }
  } finally {
    polling=false;
    // Countdown ticks are local; only check for server changes here. Back off on failure.
    const delay=Math.max(retryWait,pollInterval*2**Math.max(0,Math.min(pollFailures-1,3)))+Math.floor(Math.random()*1000);
    nextPollAt=performance.now()+delay;
    if (!leaving && !document.hidden) pollTimer=setTimeout(poll,delay);
  }
}
function accept(payload) {
  if (!payload.schedule_ready || !Array.isArray(payload.events)) throw new Error('schedule');
  data=payload; data.events=data.events.map(row=>({...row,at:instant(row.scheduled_at),end:instant(row.end_at)}));
  const scope=`BossTimer.webAlarm.v1:${data.server_name}:${data.season}`;
  if (scope!==alarmScope) { alarmScope=scope; readAlarms(); }
  fitHeader();
  summaryKey=''; renderedKey=''; copyKey=''; activeKey='';
  for (const id of ['copy-open','text-copy','txt-save','image-open']) element(id).disabled=false;
  tick(); refreshAlarms();
}
function toast(message) {
  const host=element('image-dialog').open?element('image-dialog'):element('copy-dialog').open?element('copy-dialog'):document.querySelector('main');
  host.append(element('toast'));
  clearTimeout(toastTimer); element('toast').textContent=message; element('toast').hidden=false;
  toastTimer=setTimeout(()=>element('toast').hidden=true,5000);
}
function shareText() {
  const rows=visibleRows(serverNow(),true).filter(row=>row.kind!=='break'), seconds=element('text-seconds').checked;
  const lines=[];
  let day='';
  for (const row of rows) {
    const key=dayKey(row.at);
    if (key!==day) {
      day=key;
      if (lines.length) lines.push('');
      const date=localDate(row.at);
      lines.push(`${date.getUTCMonth()+1}월 ${date.getUTCDate()}일 ${['일','월','화','수','목','금','토'][date.getUTCDay()]}요일`);
    }
    const time=clock(row.at);
    lines.push(`${seconds?time:time.slice(0,5)} ${row.name}`);
  }
  return lines.join('\n');
}
function filename(extension) { return `BossTimer_${dayKey(serverNow())}.${extension}`; }
function download(blob,name) {
  const url=URL.createObjectURL(blob), link=document.createElement('a'); link.href=url; link.download=name;
  document.body.append(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),60000);
}
async function copyText() {
  const value=shareText();
  try { await navigator.clipboard.writeText(value); toast('스케줄 텍스트를 복사했습니다. 채팅창에 붙여넣으세요.'); }
  catch (_) {
    // Explicit selection remains available without clipboard permission.
    const area=document.createElement('textarea'); area.value=value; area.setAttribute('aria-label','복사할 스케줄');
    area.className='copy-fallback'; element('copy-events').before(area); area.focus(); area.select();
    toast('복사 권한이 없습니다. 열린 텍스트를 선택해 복사하거나 TXT 저장을 사용하세요.');
    area.addEventListener('blur',()=>area.remove(),{once:true});
  }
}
const IMAGE_ROWS=60;
function fit(ctx,value,width) {
  if (ctx.measureText(value).width<=width) return value;
  const chars=Array.from(value); while(chars.length && ctx.measureText(chars.join('')+'…').width>width) chars.pop();
  return chars.join('')+'…';
}
function drawImage(rows,info,page,pages) {
  const canvas=document.createElement('canvas'), ctx=canvas.getContext('2d');
  if (!ctx) throw new Error('canvas');
  let headers=0, last=''; for(const row of rows) { const day=dayKey(row.at); if(day!==last) {headers++;last=day;} }
  canvas.width=900; canvas.height=232+rows.length*64+headers*45;
  ctx.fillStyle='#151b24';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.font='700 28px system-ui';ctx.fillStyle='#edf1f7';ctx.fillText(fit(ctx,info.context,836),32,48);
  ctx.font='18px system-ui';ctx.fillStyle='#a0abbc';ctx.fillText('전체 스케줄',32,80);
  ctx.fillText(info.range,32,110);ctx.font='14px system-ui';ctx.fillText(`공유 시각 ${info.stamp} · ${page+1} / ${pages}${info.stale?' · 최신 여부 확인 필요':''}`,32,139);
  let y=158;last='';
  for(const row of rows) {
    const day=dayKey(row.at);
    if(day!==last) {last=day;y+=45;ctx.fillStyle='#93a9c5';ctx.font='600 17px system-ui';ctx.fillText(dateLabel(row.at),32,y-8);}
    const elapsed=row.at<info.now && !(row.kind==='break' && row.end>info.now);
    ctx.fillStyle=row.shareBackground || (row.kind==='invasion'?'#453047':'#2b3b4f');ctx.fillRect(24,y,852,58);
    const restHighlight=row.kind==='break' && row.shareBackground==='#87ceeb';
    ctx.font='600 22px system-ui';ctx.fillStyle=restHighlight?'#153647':elapsed?'#788595':'#b4c0d2';
    const time=clock(row.at), timeWidth=ctx.measureText(time).width, titleX=38+timeWidth+18;
    ctx.fillText(time,38,y+35);
    ctx.font='600 22px system-ui';ctx.fillStyle=elapsed?'#788595':row.shareColor;
    const title=fit(ctx,(row.important?'✦ ':'')+row.name,Math.min(490,730-titleX)), titleWidth=ctx.measureText(title).width;ctx.fillText(title,titleX,y+35);
    ctx.font='15px system-ui';ctx.fillStyle=restHighlight?'#153647':'#a0abbc';ctx.textAlign='right';
    ctx.fillText(row.kind==='break'?'('+restDuration(row)+')':elapsed?'경과':types[row.kind],858,y+35);ctx.textAlign='left';
    if(elapsed) {ctx.strokeStyle='#788595';ctx.beginPath();ctx.moveTo(38,y+28);ctx.lineTo(38+timeWidth,y+28);ctx.moveTo(titleX,y+28);ctx.lineTo(titleX+titleWidth,y+28);ctx.stroke();}
    y+=64;
  }
  ctx.font='13px system-ui';ctx.fillStyle='#a0abbc';ctx.fillText('조회 전용 · 공유 후 변경된 일정은 웹페이지에서 확인하세요.',32,canvas.height-26);
  return new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('image')),'image/png'));
}
function releaseImage() {
  imageJob++; if(imageURL) URL.revokeObjectURL(imageURL); imageURL=''; imageBlob=null;
  element('image-preview').removeAttribute('src');
}
async function renderImage() {
  releaseImage(); const job=imageJob, pages=Math.max(1,Math.ceil(imageRows.length/IMAGE_ROWS));
  const rows=imageRows.slice(imagePage*IMAGE_ROWS,(imagePage+1)*IMAGE_ROWS);
  for(const id of ['image-copy','image-save','image-share','image-prev','image-next']) element(id).disabled=true;
  element('image-caption').textContent='이미지를 준비하고 있습니다.';
  try {
    const blob=await drawImage(rows,imageInfo,imagePage,pages); if(job!==imageJob) return;
    imageBlob=blob;imageURL=URL.createObjectURL(blob);element('image-preview').src=imageURL;
    element('image-caption').textContent=`${imagePage+1} / ${pages}페이지 · ${rows.length}개 일정${pages>1?' · 긴 스케줄은 페이지별로 복사·저장합니다.':''}`;
    element('image-prev').disabled=imagePage===0;element('image-next').disabled=imagePage===pages-1;
    element('image-copy').disabled=false;element('image-save').disabled=false;
    const file=new File([blob],filename('png'),{type:'image/png'});
    let canShare=false;try {canShare=Boolean(navigator.canShare && navigator.canShare({files:[file]}));} catch(_) {}
    element('image-share').hidden=!canShare;element('image-share').disabled=false;
  } catch(_) {if(job===imageJob) {element('image-caption').textContent='이미지를 만들지 못했습니다. 텍스트 복사 또는 TXT 저장을 사용하세요.';}}
}
function openImage() {
  if(!data) return;
  const now=serverNow();imageRows=visibleRows(now,true).map(row=>({...row,shareColor:colorOf(row,true),shareBackground:backgroundOf(row,true)}));imagePage=0;
  imageInfo={now,context:contextTitle(),range:`${clock(now,true)} ~ ${clock(periodEnd(now,true),true)}`,stamp:clock(now,true),stale:!connected || data.schedule_stale};
  element('image-dialog').showModal();renderImage();
}
function refreshLogoutControls() {
  const button=element('logout'), now=performance.now(), wait=Math.max(0,logoutReadyAt-now);
  clearTimeout(logoutUnlockTimer);
  if(logoutUnlocked && !logoutPending && !element('logout-dialog').open) {
    const remaining=logoutUnlockUntil-now;
    if(remaining<=0) logoutUnlocked=false;
    else logoutUnlockTimer=setTimeout(refreshLogoutControls,Math.ceil(remaining));
  }
  element('logout-lock').hidden=logoutUnlocked;
  button.setAttribute('aria-pressed',String(logoutUnlocked));
  button.setAttribute('aria-label',logoutUnlocked?'로그아웃 확인창 열기':'로그아웃 잠금 해제');
  button.title=logoutUnlocked?'2초 안에 다시 누르면 로그아웃 여부를 확인합니다.':'한 번 누르면 2초 동안 로그아웃 잠금이 풀립니다.';
  button.disabled=logoutPending || leaving || wait>0;
  element('logout-confirm').disabled=button.disabled;
  clearTimeout(logoutDelayTimer);
  if(wait>0) logoutDelayTimer=setTimeout(refreshLogoutControls,Math.ceil(wait));
}
function acceptLogoutClick() {
  if(logoutPending || leaving || performance.now()<logoutReadyAt) return false;
  logoutReadyAt=performance.now()+200; refreshLogoutControls(); return true;
}
function openLogout() {
  refreshLogoutControls();
  if(element('logout-dialog').open || !acceptLogoutClick()) return;
  if(!logoutUnlocked) {logoutUnlocked=true;logoutUnlockUntil=performance.now()+2000;refreshLogoutControls();return;}
  element('logout-dialog').showModal(); refreshLogoutControls();
}
async function confirmLogout() {
  if(!element('logout-dialog').open || !logoutUnlocked || !acceptLogoutClick()) return;
  logoutPending=true; element('logout-dialog').close(); refreshLogoutControls();
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),5000);
  try {
    const response=await fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:controller.signal});
    if(!response.ok) throw new Error('logout');
    leaving=true; clearTimeout(pollTimer); releaseImage(); location.replace('/');
  } catch(_) {toast('로그아웃하지 못했습니다. 연결을 확인하고 다시 시도해주세요.');}
  finally {clearTimeout(timer);logoutPending=false;logoutUnlocked=false;refreshLogoutControls();}
}
document.addEventListener('click',event=>{
  const tab=event.target.closest('[data-filter]'); if(tab) {selectFilter(tab.dataset.filter);return;}
  const button=event.target.closest('[data-alarm]'); if(!button || !data) return;
  const row=data.events.find(row=>row.id===button.dataset.alarmId); if(!row) return;
  const dated=dayAlarmKey(row), key=Object.hasOwn(alarmPrefs.overrides,dated)?dated:bossKey(row);
  alarmPrefs.overrides[key]=!alarmOn(row);saveAlarms();refreshAlarms();
});
document.querySelector('.schedule-tabs').addEventListener('keydown',event=>{
  const buttons=[...document.querySelectorAll('[data-filter]')], index=buttons.indexOf(document.activeElement);
  if(index<0 || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault();
  const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:
    (index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
  buttons[next].focus();selectFilter(buttons[next].dataset.filter);
});
element('all-alarm').addEventListener('click',toggleMasterAlarms);
element('copy-open').addEventListener('click',()=>{if(data) {element('copy-dialog').showModal();copyKey='';renderCopy(serverNow());}});
element('day-today').addEventListener('click',()=>moveDay(false));
element('day-next').addEventListener('click',()=>moveDay(true));
element('schedule-top').addEventListener('click',()=>window.scrollTo({top:0,behavior:'auto'}));
element('day-next-bottom').addEventListener('click',()=>element('day-next').click());
element('rest-filter').addEventListener('click',()=>{
  showRest=!showRest;
  try {localStorage.setItem('BossTimer.webRest.visible',String(showRest));} catch (_) {}
  refreshRestFilter();renderedKey='';tick();fitHeader();
});
element('copy-close').addEventListener('click',()=>element('copy-dialog').close());
for(const id of ['period','boss-colors','fixed-colors','include-fixed','include-break']) element(id).addEventListener('change',()=>{copyKey='';if(data) renderCopy(serverNow());});
element('text-copy').addEventListener('click',copyText);
element('txt-save').addEventListener('click',()=>{download(new Blob(['\ufeff'+shareText()],{type:'text/plain;charset=utf-8'}),filename('txt'));toast('TXT 저장을 요청했습니다.');});
element('image-open').addEventListener('click',openImage);
element('image-close').addEventListener('click',()=>element('image-dialog').close());
element('image-dialog').addEventListener('close',releaseImage);
element('image-prev').addEventListener('click',()=>{if(imagePage>0) {imagePage--;renderImage();}});
element('image-next').addEventListener('click',()=>{if((imagePage+1)*IMAGE_ROWS<imageRows.length) {imagePage++;renderImage();}});
element('image-copy').addEventListener('click',async()=>{
  if(!imageBlob) return;
  try {await navigator.clipboard.write([new ClipboardItem({'image/png':imageBlob})]);toast('스케줄 이미지를 복사했습니다.');}
  catch(_) {toast('이 브라우저에서는 이미지 복사가 지원되지 않거나 허용되지 않았습니다. PNG 저장 또는 공유를 사용하세요.');}
});
element('image-save').addEventListener('click',()=>{if(imageBlob) download(imageBlob,filename(`p${imagePage+1}.png`));});
element('image-share').addEventListener('click',async()=>{
  if(!imageBlob) return;
  try {await navigator.share({files:[new File([imageBlob],filename(`p${imagePage+1}.png`),{type:'image/png'})],title:'BossTimer 스케줄'});}
  catch(error) {if(error.name!=='AbortError') toast('공유하지 못했습니다. PNG 저장을 사용하세요.');}
});
element('logout').addEventListener('click',openLogout);
element('logout-confirm').addEventListener('click',confirmLogout);
element('logout-cancel').addEventListener('click',()=>element('logout-dialog').close());
element('logout-dialog').addEventListener('close',()=>{logoutUnlocked=false;refreshLogoutControls();});
document.addEventListener('visibilitychange',()=>{
  clearTimeout(pollTimer); if(!document.hidden) {tick();poll();}
});
window.addEventListener('storage',event=>{
  if(event.key===alarmScope || event.key===null) {readAlarms();refreshAlarms();}
  if(event.key==='BossTimer.webRest.visible' || event.key===null) {
    showRest=event.newValue!=='false';refreshRestFilter();renderedKey='';tick();fitHeader();
  }
});
window.addEventListener('online',()=>poll());
window.addEventListener('pageshow',event=>{if(event.persisted) {leaving=false;connected=false;poll();}});
if (typeof ResizeObserver!=='undefined') new ResizeObserver(fitHeader).observe(element('context'));
window.addEventListener('resize',fitHeader);
if (document.fonts) document.fonts.ready.then(fitHeader);
refreshRestFilter();setInterval(tick,1000);poll();

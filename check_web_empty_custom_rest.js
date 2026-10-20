(async()=>{
  const checks=[];
  const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  const now=serverNow(),today=dayKey(now),tomorrow=nextDay(today),start=dayStart(today),next=dayStart(tomorrow);
  const [chosen,other]=[...customById.keys()];
  check(Boolean(chosen&&other),'real catalog is available');
  let serial=0;
  const boss=(id,at,extra={})=>({id:id.repeat(24),catalog_id:chosen,kind:'boss',name:'Selected boss',at,end:at,
    scheduled_at:new Date(at).toISOString(),end_at:new Date(at).toISOString(),important:false,major:false,
    text_color:'#fff',bg_color:'#142c48',...extra});
  const load=(events,day=today)=>{
    data.events=events;data.schedule_version='empty-custom-rest-'+(++serial);
    customDraft=new Set([chosen]);customSaved=new Set([chosen]);customEditing=false;
    selectedFilter='custom';allDay=day;showRest=true;showFixed=true;
    refreshRestFilter();refreshFixedFilter();tick();
  };
  const displayedRests=()=>document.querySelectorAll('#events .type-break').length;
  document.getElementById('theme-default').click();document.getElementById('tab-custom').click();
  customScope='';readCustomSelection('BossTimer.fixture.empty-user-entry');tick();
  check(customDraft.size===0&&!customEditing&&document.getElementById('custom-editor').hidden&&!document.getElementById('custom-actions').hidden,'fresh empty user shows settings entry button');
  check(document.querySelector('.hero').hidden&&displayedRests()===0&&document.querySelectorAll('#events .event-row').length===0,'empty user entry has no boss or orphan rest');
  for(let i=0;i<3;i++)tick();check(!customEditing,'clock ticks cannot auto-open empty settings');
  document.getElementById('custom-open').click();check(customEditing&&!document.getElementById('custom-close').disabled,'settings opens on click and allows back with no selection');
  document.getElementById('custom-close').click();check(!customEditing&&document.getElementById('custom-editor').hidden&&!document.getElementById('custom-actions').hidden,'back returns to settings-button entry without selections');
  document.getElementById('tab-all').click();document.getElementById('tab-custom').click();check(!customEditing,'returning to empty user tab keeps entry screen');
  load([boss('a',start-3600000),boss('b',next+3600000),boss('c',now+3600000,{catalog_id:other})]);
  check(mainEvents(now).some(r=>r.kind==='break'),'underlying selected gap spans empty day');
  check(visibleRows(now).length===0&&displayedRests()===0,'empty custom day has no standalone rest in real DOM');
  check(!document.getElementById('empty').hidden&&document.querySelector('.hero').hidden,'empty custom day shows empty state');
  for(let i=0;i<3;i++)tick();check(displayedRests()===0,'clock ticks cannot restore orphaned rest');
  document.getElementById('rest-filter').click();document.getElementById('rest-filter').click();check(displayedRests()===0,'rest toggle cannot restore orphaned rest');
  load([boss('d',next+6*3600000,{kind:'fixed'}),boss('e',next+8*3600000,{kind:'fixed'})],tomorrow);
  check(displayedRests()===1,'selected upcoming fixed bosses keep their actual gap');
  document.getElementById('fixed-filter').click();check(displayedRests()===0&&visibleRows(now).length===0,'fixed OFF leaves neither eligible bosses nor rests');
  document.getElementById('fixed-filter').click();check(displayedRests()===1,'fixed ON restores a valid actual gap');
  load([boss('f',now-2*3600000),boss('a',now-5*60000),boss('b',next+3600000)]);
  check(visibleRows(now).every(r=>r.kind!=='break')&&displayedRests()===0,'no next boss today means no rest toward tomorrow');
  check(document.getElementById('recent').children.length>0,'past selected bosses remain available as history');
  load([boss('c',next+6*3600000),boss('d',next+8*3600000)],tomorrow);
  check(displayedRests()===1,'normal future custom bosses still display rest');
  document.getElementById('rest-filter').click();check(displayedRests()===0,'normal rest OFF still works');document.getElementById('rest-filter').click();check(displayedRests()===1,'normal rest ON still works');
  load([{...boss('e',now),kind:'break',name:'Rest',end:now+3600000,rest_ms:3600000}]);
  document.getElementById('tab-all').click();check(displayedRests()===1,'all tab preserves original source-only rest');
  check(!window.previewErrors?.length,'no runtime errors');check(document.documentElement.scrollWidth<=innerWidth,'no mobile horizontal overflow');
  return {checks:checks.length};
})()

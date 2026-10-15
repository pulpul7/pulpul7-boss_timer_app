/* Offline regression checks against the production view functions. Parameter: source. */
const checks=[];
function check(value,message){if(!value)throw Error(message);checks.push(message);}
const dates=source.slice(source.indexOf('function offsetOf('),source.indexOf('function contextTitle('));
const views=source.slice(source.indexOf('function included('),source.indexOf('function colorOf('));
function fixture() {
  return new Function(`
    let now=Date.parse('2026-10-15T11:59:59+09:00'),selectedFilter='all',allDay='',showRest=true,showFixed=true;
    let viewKey='',viewEvents=[],mainKey='',mainRows=[],customDraft=new Set(['chosen']);
    const data={window_start:'2026-10-14T00:00:00+09:00',window_end:'2026-10-30T23:59:59+09:00',
      schedule_version:'1',major_version:'1',catalog_version:'1',events:[]};
    const pad=n=>String(n).padStart(2,'0'),serverNow=()=>now,instant=Date.parse;
    const isBoss=r=>['boss','fixed','invasion'].includes(r.kind);
    const selectionSignature=s=>[...s].sort().join(','),hasCustomSelection=()=>customDraft.size>0;
    const customRowSelected=r=>customDraft.has(r.catalog_id || r.id);
    const element=id=>({checked:id!=='major-only',value:'24'});
    const periodEnd=()=>now+86400000;
    ${dates}\n${views}
    return {data,mainEvents,visibleRows,viewRows,hasDay,viewEnd,selectedDay,
      time:s=>now=Date.parse(s),filter:s=>selectedFilter=s,day:s=>allDay=s,
      select:ids=>customDraft=new Set(ids),rest:b=>showRest=b,
      rows:rows=>{data.events=rows;data.schedule_version=String(Number(data.schedule_version)+1);}};
  `)();
}
const at=s=>Date.parse(s), date='2026-10-15',next='2026-10-16';
function row(id,time,kind='boss',end=time,extra={}) {
  return {id,catalog_id:id,name:id,kind,at:at(time),end:at(end),important:true,...extra};
}
const noon=fixture();
noon.rows([row('today',date+'T23:59:00+09:00'),row('midnight',next+'T00:00:00+09:00'),
  row('early',next+'T07:59:59+09:00'),row('eight',next+'T08:00:00+09:00'),
  row('late',next+'T08:00:01+09:00'),row('rest-eight',next+'T08:00:00+09:00','break',next+'T09:00:00+09:00')]);
check(noon.mainEvents(at(date+'T11:59:59+09:00')).map(r=>r.id).join(',')==='today','before noon today ends at midnight');
const extended=noon.mainEvents(at(date+'T12:00:00+09:00'));
check(extended.map(r=>r.id).join(',')==='today,midnight,early,eight','noon cache invalidates and includes tomorrow through exactly 08:00');
check(noon.data.events.length===6,'view range does not mutate source events');
noon.filter('today');
check(noon.mainEvents(at(date+'T18:00:00+09:00')).map(r=>r.id).join(',')==='today','major tab keeps its calendar-day range');
noon.filter('custom');noon.select(['today']);
check(noon.mainEvents(at(date+'T18:00:00+09:00')).map(r=>r.id).join(',')==='today','custom tab keeps its calendar-day range');
noon.filter('all');noon.day(next);
check(noon.mainEvents(at(date+'T18:00:00+09:00')).some(r=>r.id==='late'),'explicit next-day view still shows its full day');
noon.day('');noon.time(next+'T00:00:00+09:00');
check(noon.selectedDay(at(next+'T00:00:00+09:00'))===next,'midnight switches to the new current day');
check(noon.mainEvents(at(next+'T00:00:00+09:00')).some(r=>r.id==='late'),'new current day retains its normal full-day range');
const custom=fixture();custom.filter('custom');custom.select(['chosen']);
custom.rows([row('old',date+'T06:20:00+09:00','boss',undefined,{catalog_id:'chosen'}),
  row('old2',date+'T09:02:00+09:00','boss',undefined,{catalog_id:'chosen'}),
  row('rest1',date+'T10:00:00+09:00','break',date+'T12:42:00+09:00'),
  row('rest2',date+'T13:00:00+09:00','break',date+'T14:24:00+09:00'),
  row('rest3',date+'T15:00:00+09:00','break',next+'T05:00:00+09:00')]);
custom.time(date+'T12:00:00+09:00');
check(custom.visibleRows(at(date+'T12:00:00+09:00')).map(r=>r.id).join(',')==='old,old2','past selected bosses do not leave a stack of orphaned rests');
custom.rows([...custom.data.events,row('future',date+'T18:00:00+09:00','boss',undefined,{catalog_id:'chosen'})]);
const rows=custom.visibleRows(at(date+'T12:00:00+09:00'));
check(rows.filter(r=>r.kind==='break').length===3,'real rest rows remain while selected future bosses exist');
check(rows.find(r=>r.id==='rest3').end===at(next+'T05:00:00+09:00'),'rest duration is never synthesized or shortened');
check(custom.visibleRows(at(date+'T18:00:01+09:00')).every(r=>r.kind!=='break'),'passing the last selected boss hides remaining rests without a cache refresh');
custom.select([]);
check(custom.visibleRows(at(date+'T12:00:00+09:00')).length===0,'empty custom selection has no rest or boss rows');
custom.filter('all');
check(custom.visibleRows(at(date+'T18:00:01+09:00')).some(r=>r.kind==='break'),'all tab keeps source rest rows after its last boss');
custom.filter('today');
check(custom.visibleRows(at(date+'T18:00:01+09:00')).every(r=>r.kind!=='break'),'major tab receives the same orphaned-rest fix');
custom.filter('custom');custom.select(['chosen']);
check(custom.visibleRows(at(date+'T18:00:01+09:00'),true).some(r=>r.kind==='break'),'copy range remains independent of screen-only rest filtering');
return checks;

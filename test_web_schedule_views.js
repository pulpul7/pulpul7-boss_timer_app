/* Offline regression checks against the production view functions. Parameter: source. */
const checks=[];
function check(value,message){if(!value)throw Error(message);checks.push(message);}
const dates=source.slice(source.indexOf('function offsetOf('),source.indexOf('function contextTitle('));
const rests=source.slice(source.indexOf('function restDuration('),source.indexOf('function restRowScale('));
const views=source.slice(source.indexOf('function included('),source.indexOf('function colorOf('));
function fixture() {
  return new Function(`
    let now=Date.parse('2026-10-15T11:59:59+09:00'),selectedFilter='all',selectedTheme='default',allDay='',showRest=true,showFixed=true;
    let viewKey='',viewEvents=[],mainKey='',mainRows=[],customDraft=new Set(['chosen']);
    let copyViewKey='',copyViewRows=[];
    const data={window_start:'2026-10-14T00:00:00+09:00',window_end:'2026-10-30T23:59:59+09:00',
      schedule_version:'1',major_version:'1',catalog_version:'1',events:[]};
    const pad=n=>String(n).padStart(2,'0'),serverNow=()=>now,instant=Date.parse;
    const isBoss=r=>['boss','fixed','invasion'].includes(r.kind);
    const selectionSignature=s=>[...s].sort().join(','),hasCustomSelection=()=>customDraft.size>0;
    const customRowSelected=r=>customDraft.has(r.catalog_id || r.id);
    const options={'major-only':false,'include-fixed':true,'include-break':true};
    const element=id=>({checked:options[id]===true,value:'24'});
    const periodEnd=()=>now+86400000;
    ${dates}\n${rests}\n${views}
    return {data,restDuration,isOngoingRest,mainEvents,visibleRows,viewRows,hasDay,viewEnd,selectedDay,bossFinished,recentBosses,spawnBlink,
      time:s=>now=Date.parse(s),filter:s=>selectedFilter=s,theme:s=>selectedTheme=s,day:s=>allDay=s,
      select:ids=>customDraft=new Set(ids),rest:b=>showRest=b,
      fixed:b=>showFixed=b,
      copy:(id,value)=>options[id]=value,
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
check(rows.filter(r=>r.kind==='break').length===1,'custom rest is recalculated between the selected bosses');
check(rows.find(r=>r.kind==='break').rest_ms===538*60000,'custom rest uses the full actual gap instead of adding source rest durations');
check(rows.find(r=>r.kind==='break').end===at(date+'T18:00:00+09:00'),'custom rest ends at the next selected boss, not an unrelated source rest');
check(custom.visibleRows(at(date+'T18:00:03+09:00')).every(r=>r.kind!=='break'),'passing the last selected boss hides remaining rests without a cache refresh');
custom.select([]);
check(custom.visibleRows(at(date+'T12:00:00+09:00')).length===0,'empty custom selection has no rest or boss rows');
custom.filter('all');
check(custom.visibleRows(at(date+'T18:00:03+09:00')).some(r=>r.kind==='break'),'all tab keeps source rest rows after its last boss');
custom.filter('today');
check(custom.visibleRows(at(date+'T18:00:03+09:00')).every(r=>r.kind!=='break'),'major tab receives the same orphaned-rest fix');
custom.filter('custom');custom.select(['chosen']);
check(custom.visibleRows(at(date+'T18:00:03+09:00'),true).every(r=>r.kind!=='break'),'copy removes orphaned rests after its final included boss');
const joined=fixture();joined.filter('custom');joined.select(['chosen']);
const original=[row('selected',date+'T12:30:00+09:00','boss',undefined,{catalog_id:'chosen'}),
  row('r1',date+'T13:00:00+09:00','break',date+'T14:00:00+09:00'),
  row('hidden',date+'T14:00:00+09:00','boss'),
  row('r2',date+'T14:10:00+09:00','break',date+'T15:00:00+09:00'),
  row('overlap',date+'T14:30:00+09:00','break',date+'T15:30:00+09:00'),
  row('chosen',date+'T16:00:00+09:00'),
  row('r3',date+'T16:05:00+09:00','break',date+'T17:00:00+09:00')];
const before=JSON.stringify(original);original.forEach(Object.freeze);Object.freeze(original);joined.rows(original);
const joinedRows=joined.visibleRows(at(date+'T12:00:00+09:00')),parts=joinedRows.filter(r=>r.kind==='break');
check(parts.length===1,'there is one actual gap between the two selected bosses');
check(parts[0].rest_ms===210*60000,'unselected bosses and overlapping source rests do not subtract from selected rest');
check(joined.restDuration(parts[0])==='3시간 30분','filtered rest duration is calculated from its actual boundaries');
check(joined.isOngoingRest(parts[0],at(date+'T13:30:00+09:00')),'original rest interval remains ongoing');
check(joined.isOngoingRest(parts[0],at(date+'T14:05:00+09:00')),'a hidden boss does not interrupt filtered rest');
check(JSON.stringify(original)===before,'joining never mutates original snapshot rows');
check(joined.visibleRows(at(date+'T12:00:00+09:00'),true).filter(r=>r.kind==='break').length===2,'copy recalculates two gaps around its three included bosses');
joined.filter('all');
check(joined.visibleRows(at(date+'T12:00:00+09:00')).filter(r=>r.kind==='break').length===4,'all tab retains original rest rows');
joined.filter('today');
check(joined.visibleRows(at(date+'T12:00:00+09:00')).filter(r=>r.kind==='break').length===2,'major tab recalculates around each displayed major boss');
joined.filter('custom');joined.rest(false);
check(joined.visibleRows(at(date+'T12:00:00+09:00')).every(r=>r.kind!=='break'),'custom rest OFF remains effective');
const spawn=fixture();spawn.time(date+'T18:00:00+09:00');
const when=at(date+'T18:00:00+09:00');
spawn.rows(['a','b','c','d'].map(id=>row(id,date+'T18:00:00+09:00')));
check(spawn.visibleRows(when+2999).length===4,'all simultaneous spawns remain for the full three seconds');
check(spawn.visibleRows(when+3000).length===2,'simultaneous past bosses are strictly capped at two');
const pastIds=spawn.visibleRows(when+3000).map(r=>r.id);
check(new Set(pastIds).size===2,'limited simultaneous spawns never duplicate at the past/future boundary');
check(!spawn.bossFinished(spawn.data.events[0],when+2999)&&spawn.bossFinished(spawn.data.events[0],when+3000),
  'boss changes to past at exactly spawn plus three seconds');
check(spawn.recentBosses(spawn.data.events,when+2999).length===0&&spawn.recentBosses(spawn.data.events,when+3000).length===2,
  'the recent cap applies when simultaneous bosses complete their spawn hold');
spawn.theme('avengers');
check(spawn.recentBosses(spawn.data.events,when+3000).length===1&&spawn.visibleRows(when+3000).length===1,
  'Iron Man uses one shared recent limit in summary and schedule rows');
spawn.theme('winter');
check(spawn.visibleRows(when+3000).length===2,'leaving Iron Man restores the normal two-boss limit');
let lit=false;const target={classList:{toggle(name,value){if(name==='spawn-blink')lit=value;}}};
spawn.spawnBlink(target,spawn.data.events[0],when-5001);check(!lit,'no blinking before five seconds');
spawn.spawnBlink(target,spawn.data.events[0],when-5000);check(lit,'blinking begins at five seconds');
spawn.spawnBlink(target,spawn.data.events[0],when+2999);check(lit,'blinking lasts through the spawn hold');
spawn.spawnBlink(target,spawn.data.events[0],when+3000);check(!lit,'blinking ends when boss moves to recent');
check(spawn.visibleRows(when+1000,true).length===2,'copy view retains its original actual-time boundary');
const tied=fixture();
const tieAt=date+'T12:30:00+09:00';
const tieRows=[row('스카드',tieAt,'boss',tieAt,{input_order:1}),
  row('헤이드',tieAt,'boss',tieAt,{input_order:2}),
  row('프레이',tieAt,'boss',tieAt,{input_order:3})];
for(const filter of ['all','today','custom']) {
  tied.filter(filter);tied.select(tieRows.map(r=>r.id));
  for(let refresh=0;refresh<3;refresh++) {
    tied.rows([...tieRows].reverse()); // Stored ranks recover input order after a shuffled reload.
    check(tied.visibleRows(at(date+'T12:00:00+09:00')).map(r=>r.name).join(',')==='스카드,헤이드,프레이',
      filter+' tab retains equal-time input order after refresh '+refresh);
    check(tied.visibleRows(at(date+'T12:00:00+09:00'),true).map(r=>r.name).join(',')==='스카드,헤이드,프레이',
      filter+' copy view retains equal-time input order '+refresh);
  }
}
const precise=row('before',date+'T12:29:59.999+09:00','boss',undefined,{input_order:99});
tied.filter('all');tied.rows([...tieRows,precise]);
check(tied.visibleRows(at(date+'T12:00:00+09:00'))[0].id==='before','actual time wins over input order');
for(const filter of ['today','custom']) {
  const gaps=fixture();gaps.filter(filter);gaps.select(['left','right','fixed']);
  const sourceRows=[row('left',date+'T12:00:00+09:00','boss',date+'T12:10:00+09:00'),
    row('fixed',date+'T14:00:00+09:00','fixed',date+'T14:30:00+09:00',{important:false,major:false}),
    row('right',date+'T16:00:00+09:00'),
    row('bogus-rest',date+'T12:20:00+09:00','break',date+'T12:30:00+09:00')];
  const original=JSON.stringify(sourceRows), restRows=()=>gaps.visibleRows(at(date+'T12:15:00+09:00')).filter(r=>r.kind==='break');
  gaps.rows(sourceRows);
  check(restRows().map(r=>r.rest_ms/60000).join(',')==='110,90',filter+' fixed ON divides rest using the fixed event end');
  gaps.fixed(false);
  check(restRows().map(r=>r.rest_ms/60000).join(',')==='230',filter+' fixed OFF immediately recomputes the whole gap');
  gaps.fixed(true);
  check(restRows().map(r=>r.rest_ms/60000).join(',')==='110,90',filter+' fixed ON restores recalculated intervals without reload');
  gaps.rest(false);check(restRows().length===0,filter+' rest OFF hides derived rests immediately');
  gaps.rest(true);check(restRows().length===2,filter+' rest ON recalculates without stale cached totals');
  gaps.rows(JSON.parse(JSON.stringify(sourceRows)));
  check(restRows().map(r=>r.rest_ms/60000).join(',')==='110,90',filter+' reload retains actual rest intervals');
  if(filter==='custom') {
    gaps.select(['left','right']);
    check(restRows().map(r=>r.rest_ms/60000).join(',')==='230','changing custom boss selection invalidates recalculated rest cache');
    gaps.select(['left','right','fixed']);
  }
  check(JSON.stringify(sourceRows)===original,filter+' recalculation does not modify received schedule rows');
  gaps.data.rest_rules=[];gaps.rows(sourceRows);check(restRows().length===0,filter+' disabled administrator rest rules are respected');
  gaps.data.rest_rules=[{min_gap_minutes:100,max_gap_minutes:120,row_scale:2}];gaps.rows(sourceRows);
  check(restRows().length===1&&restRows()[0].row_scale===2,filter+' rule thresholds and height apply to the recalculated gap');
}
const overlap=fixture();overlap.filter('today');
overlap.rows([row('long',date+'T12:00:00+09:00','boss',date+'T13:00:00+09:00'),
  row('short',date+'T12:10:00+09:00','boss',date+'T12:30:00+09:00'),row('after',date+'T14:00:00+09:00')]);
const overlapRest=overlap.visibleRows(at(date+'T12:00:00+09:00')).filter(r=>r.kind==='break');
check(overlapRest.length===1&&overlapRest[0].at===at(date+'T13:00:00+09:00')&&overlapRest[0].rest_ms===3600000,
  'overlapping bosses use the latest effective end, not the last row end');
const overnight=fixture();overnight.filter('custom');overnight.select(['night','morning']);
const emptyDay=fixture();emptyDay.filter('custom');emptyDay.select(['chosen']);emptyDay.time(date+'T12:00:00+09:00');
emptyDay.rows([row('yesterday','2026-10-14T23:00:00+09:00','boss',undefined,{catalog_id:'chosen'}),
  row('unselected',date+'T14:00:00+09:00'),row('tomorrow',next+'T01:00:00+09:00','boss',undefined,{catalog_id:'chosen'})]);
check(emptyDay.mainEvents(at(date+'T12:00:00+09:00')).some(r=>r.kind==='break'),'fixture contains a derived rest spanning an empty selected day');
check(emptyDay.visibleRows(at(date+'T12:00:00+09:00')).length===0,'custom day without any displayed boss never shows a standalone spanning rest');
emptyDay.rest(false);emptyDay.rest(true);
check(emptyDay.visibleRows(at(date+'T12:00:00+09:00')).length===0,'rest OFF then ON cannot restore an orphaned custom-day rest');
emptyDay.rows([row('past',date+'T09:00:00+09:00','boss',undefined,{catalog_id:'chosen'}),
  row('tomorrow',next+'T01:00:00+09:00','boss',undefined,{catalog_id:'chosen'})]);
check(emptyDay.visibleRows(at(date+'T12:00:00+09:00')).map(r=>r.id).join(',')==='past','past selected boss remains history without a rest toward an undisplayed next-day boss');
emptyDay.rows([row('first',date+'T13:00:00+09:00','boss',undefined,{catalog_id:'chosen'}),
  row('last',date+'T15:00:00+09:00','boss',undefined,{catalog_id:'chosen'}),
  row('tomorrow',next+'T01:00:00+09:00','boss',undefined,{catalog_id:'chosen'})]);
check(emptyDay.visibleRows(at(date+'T12:00:00+09:00')).some(r=>r.kind==='break'),'valid upcoming custom bosses keep their recalculated rests');
check(emptyDay.visibleRows(at(date+'T15:00:02.999+09:00')).some(r=>r.kind==='break'),'last custom boss retains its normal three-second spawn hold');
check(emptyDay.visibleRows(at(date+'T15:00:03+09:00')).every(r=>r.kind!=='break'),'rest toward a hidden next-day boss disappears immediately after final spawn hold without cache reset');
emptyDay.select(['no-schedule']);
check(emptyDay.visibleRows(at(date+'T12:00:00+09:00')).length===0,'selected boss with no matching schedule never displays source rests');
emptyDay.filter('today');
check(emptyDay.visibleRows(at(date+'T15:00:03+09:00')).every(r=>r.kind!=='break'),'major tab also hides rests when no upcoming boss remains in its displayed period');
emptyDay.rows([row('source-rest',date+'T12:00:00+09:00','break',date+'T13:00:00+09:00')]);emptyDay.filter('all');
check(emptyDay.visibleRows(at(date+'T12:10:00+09:00')).some(r=>r.kind==='break'),'all tab continues to show original source rest rows independently');
overnight.rows([row('night',date+'T23:00:00+09:00','boss',date+'T23:30:00+09:00'),row('morning',next+'T01:00:00+09:00')]);
const nightRest=overnight.mainEvents(at(date+'T23:45:00+09:00')).find(r=>r.kind==='break');
check(nightRest?.rest_ms===90*60000,'underlying rest interval across midnight retains its full duration');
check(!overnight.visibleRows(at(date+'T23:45:00+09:00')).some(r=>r.kind==='break'),'after the last boss today a rest toward an undisplayed next-day boss stays hidden');
overnight.day(next);
check(overnight.visibleRows(at(next+'T00:30:00+09:00')).find(r=>r.kind==='break')?.id===nightRest.id,
  'day navigation retains the same actual rest interval without trimming its duration');
const barrier=fixture();barrier.filter('today');
barrier.rows([row('before',date+'T12:00:00+09:00'),row('maintenance',date+'T13:00:00+09:00','maintenance'),
  row('after',date+'T16:00:00+09:00')]);
check(!barrier.visibleRows(at(date+'T12:00:00+09:00')).some(r=>r.kind==='break'),'rest does not span a maintenance boundary');
check(barrier.visibleRows(at(date+'T12:00:00+09:00'),true).some(r=>r.id==='maintenance'),'copy preserves an included maintenance row while rebuilding rests');
check(!barrier.visibleRows(at(date+'T12:00:00+09:00'),true).some(r=>r.kind==='break'),'copy cannot bridge a maintenance boundary');
const sharing=fixture();sharing.rest(false);sharing.fixed(false);sharing.filter('custom');sharing.select([]);
sharing.rows([row('left',date+'T09:00:00+09:00','boss',date+'T09:30:00+09:00'),
  row('source-long',date+'T09:30:00+09:00','break',date+'T14:04:00+09:00'),
  row('fixed-middle',date+'T14:04:00+09:00','fixed',undefined,{important:false,major:false}),
  row('source-short',date+'T14:04:00+09:00','break',date+'T15:02:00+09:00'),
  row('right',date+'T15:02:00+09:00')]);
sharing.copy('include-fixed',false);
let shared=sharing.visibleRows(at(date+'T10:00:00+09:00'),true);
check(shared.filter(r=>r.kind==='break').length===1&&shared.find(r=>r.kind==='break').rest_ms===332*60000,
  'reported 4h34 and 58m source rests become one true gap when copy excludes the intervening fixed boss');
check(shared.some(r=>r.id==='right'),'copy options remain independent of custom selection and screen rest OFF');
sharing.copy('include-fixed',true);
check(sharing.visibleRows(at(date+'T10:00:00+09:00'),true).filter(r=>r.kind==='break').length===2,'including the real middle boss splits rest at that boss');
sharing.copy('major-only',true);
check(sharing.visibleRows(at(date+'T10:00:00+09:00'),true).filter(r=>r.kind==='break').length===1,'major-only copy recalculates rest after removing a non-major boss');
sharing.copy('include-break',false);
check(sharing.visibleRows(at(date+'T10:00:00+09:00'),true).every(r=>r.kind!=='break'),'copy rest OFF invalidates derived rest cache');
sharing.copy('include-break',true);
sharing.data.rest_rules=[];sharing.rows(sharing.data.events);
check(sharing.visibleRows(at(date+'T10:00:00+09:00'),true).every(r=>r.kind!=='break'),'copy honors disabled administrator rest rules');
const bounded=fixture();
bounded.rows([row('inside',date+'T12:00:00+09:00'),row('outside','2026-10-17T12:00:00+09:00')]);
check(bounded.visibleRows(at(date+'T11:00:00+09:00'),true).map(r=>r.id).join(',')==='inside',
  'copy does not leave a rest pointing to a boss outside its selected period');
// Reported source rests: 45 and 42 minutes become adjacent when ordinary bosses are hidden.
for(const filter of ['today','custom']) {
  const reported=fixture();reported.select(['major-left','major-right']);
  const sourceRows=[row('major-left',date+'T15:25:30+09:00'),
    row('ordinary-left',date+'T15:37:00+09:00','boss',date+'T15:38:03+09:00',{important:false,major:false}),
    row('source-45',date+'T15:38:03+09:00','break',date+'T16:23:29.732+09:00'),
    row('ordinary-right',date+'T16:23:29.732+09:00','boss',date+'T16:53:07+09:00',{important:false,major:false}),
    row('source-42',date+'T16:53:07+09:00','break',date+'T17:35:00+09:00'),
    row('major-right',date+'T17:35:00+09:00')];
  for(const fixed of [true,false]) {
    reported.rows(sourceRows);reported.filter('all');reported.fixed(fixed);
    check(reported.visibleRows(at(date+'T15:00:00+09:00')).filter(r=>r.kind==='break').length===2,
      filter+' source still contains both independent 45/42 minute rests');
    reported.filter(filter);
    const result=reported.visibleRows(at(date+'T15:00:00+09:00'));
    check(result.map(r=>r.kind).join(',')==='boss,break,boss' &&
      result[1].derived_rest===true && result[1].rest_ms===129.5*60000,
      filter+' fixed '+fixed+' replaces consecutive source rests with one actual selected-boss gap');
  }
}
return checks;

/* Saved copy mode, independent legacy rules and per-click end-time override. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
const section=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b));
const values=new Map(),nodes=new Map();
for(const id of ['major-only','include-fixed','include-break','text-seconds','copy-legacy-txt'])nodes.set(id,{checked:id==='major-only'||id==='copy-legacy-txt',disabled:false});
nodes.set('period',{value:'tomorrow-8'});
const now=Date.UTC(2026,9,21,9),data={window_start:'2026-10-20T00:00:00Z',window_end:'2026-10-25T00:00:00Z',schedule_version:'1',major_version:'1',catalog_version:'1',events:[],boss_catalog:[]};
const api=new Function('data','element','localStorage',`
let copyViewKey='',copyViewRows=[],selectedFilter='custom';
const isBoss=r=>['boss','fixed','invasion'].includes(r.kind),instant=Date.parse,localDate=t=>new Date(t),offsetOf=()=>0;
const mainEvents=()=>{throw Error('major tab source accessed');},customDefaultSelected=()=>{throw Error('major policy accessed');};
const recalculatedRests=rows=>rows,majorBoss=r=>r.important,recentBosses=()=>[];
${section('const COPY_OPTIONS_KEY=', 'function included(')}
${section('function included(', 'function selectedDay(')}
${section('function sharingEvents(', 'function bossRank(')}
${section('function legacyTxtIncluded(', 'function shareText(')}
return {rows:(now,period)=>visibleRows(now,true,period),end:(now,p)=>periodEnd(now,true,p),screenEnd:now=>periodEnd(now),
 load:readCopyOptions,save:saveCopyOptions,refresh:refreshCopyMode};
`)(data,id=>nodes.get(id),{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)});
const row=(id,name,time,extra={})=>({id,catalog_id:id,name,at:Date.parse(time),kind:'boss',...extra});
data.boss_catalog=[{id:'a',name:'ordinary',kind:'boss',group:'4챕'},
 {id:'b',name:'월드보스',kind:'boss',group:'고정보스'},
 {id:'c',name:'fixed',kind:'fixed',group:'고정보스'}];
data.events=[row('a','ordinary','2026-10-21T10:00:00Z'),row('b','world','2026-10-21T11:00:00Z',{important:true}),
 row('c','fixed','2026-10-21T12:00:00Z',{kind:'fixed'}),row('a','tomorrow late','2026-10-22T23:59:59Z'),
 row('a','too late','2026-10-23T00:00:00Z')];
api.refresh();check(nodes.get('major-only').disabled&&nodes.get('include-fixed').disabled,'legacy mode disables conflicting ordinary boss options without changing their checks');
check(api.rows(now).map(r=>r.name).join()==='ordinary,fixed','legacy category supplies its own boss rules despite stored major/fixed options');
check(api.rows(now,'tomorrow-end').map(r=>r.name).join()==='ordinary,fixed,tomorrow late','right-copy override includes the entire next day but excludes following midnight');
check(api.end(now,'tomorrow-end')===Date.UTC(2026,9,22,23,59,59,999),'right-copy cutoff is next-day 23:59:59.999');
check(nodes.get('period').value==='tomorrow-8'&&!values.size,'one-off cutoff does not mutate settings or storage');
check(api.rows(now).map(r=>r.name).join()==='ordinary,fixed','left-copy after right-copy restores saved period without stale cache');
nodes.get('copy-legacy-txt').checked=false;api.refresh();
check(!nodes.get('major-only').disabled&&!nodes.get('include-fixed').disabled,'ordinary options become editable when legacy mode is off');
check(api.rows(now).map(r=>r.name).join()==='world','ordinary mode uses existing major-only selection');
nodes.get('major-only').checked=false;nodes.get('include-fixed').checked=true;
check(api.rows(now).map(r=>r.name).join()==='ordinary,world,fixed','ordinary mode follows changed settings on the same row source');
check(api.rows(now,'tomorrow-end').map(r=>r.name).join()==='ordinary,world,fixed,tomorrow late','right-copy also obeys ordinary selection while only extending the period');
api.save();nodes.get('copy-legacy-txt').checked=true;nodes.get('period').value='48';api.load();api.refresh();
check(!nodes.get('copy-legacy-txt').checked&&nodes.get('period').value==='tomorrow-8','category choice and period survive save/reload');
const snapshot=values.get('BossTimer.webCopy.options');api.rows(now,'tomorrow-end');
check(values.get('BossTimer.webCopy.options')===snapshot,'right-copy cannot overwrite saved options');
nodes.get('copy-legacy-txt').checked=true;api.save();api.load();api.refresh();
check(nodes.get('copy-legacy-txt').checked&&api.rows(now).map(r=>r.name).join()==='ordinary,fixed','legacy mode returns cleanly after normal-mode cache use');
const cutoff=Date.UTC(2026,9,22,23,59,59,999);
check(api.end(now,'default')===Date.UTC(2026,9,23,8)&&api.end(Date.UTC(2026,9,21,20),'default')===Date.UTC(2026,9,23,8),'renamed old default always means day-after-tomorrow08:00 for copying');
check(api.end(now,'tomorrow-8')===Date.UTC(2026,9,22,8)&&api.end(Date.UTC(2026,9,21,20),'tomorrow-8')===Date.UTC(2026,9,22,8),'new first-choice default always means tomorrow08:00');
check(api.screenEnd(now)===Date.UTC(2026,9,22,8)&&api.screenEnd(Date.UTC(2026,9,21,20))===Date.UTC(2026,9,23,8),'ordinary schedule cutoff preserves its original evening extension');
data.events.push(row('a','exact TXT cutoff','2026-10-22T23:59:59.999Z'),row('a','after TXT cutoff','2026-10-23T00:00:00Z'));
check(api.end(now,'tomorrow-end')===cutoff,'TXT ends at the last millisecond of the next calendar day');
check(api.end(Date.UTC(2026,9,21,20),'tomorrow-end')===cutoff,'TXT cutoff does not switch with the evening default-copy rule');
nodes.get('period').value='all';
check(api.rows(now,'tomorrow-end').some(r=>r.name==='exact TXT cutoff')&&!api.rows(now,'tomorrow-end').some(r=>r.name==='after TXT cutoff'),'TXT includes the last next-day millisecond but excludes following midnight independently of the saved copy period');
check(api.rows(now).some(r=>r.name==='after TXT cutoff'),'normal copy retains its own saved all-period setting');
const kstData={window_start:'2026-10-20T00:00:00+09:00',window_end:'2026-10-25T00:00:00+09:00'};
const kstEnd=new Function('data','element','localDate','offsetOf',`const instant=Date.parse;${section('function periodEnd(', 'function included(')};return periodEnd;`)(kstData,()=>({value:'24'}),t=>new Date(t+9*3600000),()=>540);
for(const hour of ['02','20'])check(kstEnd(Date.parse('2026-10-21T'+hour+':00:00+09:00'),true,'tomorrow-end')===Date.parse('2026-10-22T23:59:59.999+09:00'),'KST TXT cutoff remains next-day 23:59:59 from hour '+hour);
nodes.get('period').value='default';nodes.get('copy-legacy-txt').checked=false;api.save();nodes.get('period').value='tomorrow-8';nodes.get('copy-legacy-txt').checked=true;api.load();
check(nodes.get('period').value==='default'&&!nodes.get('copy-legacy-txt').checked,'saved personal period and category override fresh release defaults');
return checks;

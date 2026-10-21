/* TXT deadline grouping and lifecycle, with a deterministic clock. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
const embedded=source.split('// BEGIN WEB SCHEDULE TXT')[1].split('// END WEB SCHEDULE TXT')[0];
function exercise(module,label){
 let now=1000000,sequence=0,writes=0,formats=0,reads=0,scans=0,widthWrites=0;
 const timers=new Map(),events={},periods=[],view={selectedFilter:'txt',data:{},hiddenMenuCollapsed:true};
 const rest={checked:true},restRequests=[],copied=[],option={checked:false},box={style:{},clientWidth:200,get value(){return this.current||'';},set value(value){writes++;this.current=value;},get scrollHeight(){reads++;return 100;}};
 const probe={get textContent(){return this.current||'';},set textContent(value){widthWrites++;this.current=value;}};
 const doc={hidden:false,addEventListener:(name,fn)=>events[name]=fn},win={addEventListener:(name,fn)=>events[name]=fn};
 let rows=[];
 const api=new Function('hooks','document','window',`
 let selectedFilter='txt',data={},hiddenMenuCollapsed=true;
 const serverNow=()=>hooks.now(),isBoss=row=>['boss','fixed','invasion'].includes(row.kind);
 const WebNotices={groupGap:121000};
 const COPY_CHECKBOXES=['option','include-break'],periodEnd=()=>99999999;
 const sharingEvents=(now,period,rests)=>hooks.rows(period,rests),shareText=rows=>hooks.format(rows),element=id=>id==='include-break'?hooks.rest:id==='option'?hooks.option:id==='txt-width'?hooks.probe:hooks.box;
 const setTimeout=(fn,delay)=>hooks.set(fn,delay),clearTimeout=id=>hooks.clear(id);
 const copyText=(...args)=>hooks.copy(args);
 ${module}
 return {refresh:WebScheduleTXT.refresh,stop:WebScheduleTXT.stop,fit:WebScheduleTXT.fit,copy:WebScheduleTXT.copy,invalidate:()=>data={},set:v=>{selectedFilter=v.selectedFilter;data=v.data;hiddenMenuCollapsed=v.hiddenMenuCollapsed;}};
 `)({now:()=>now,rows:(p,r)=>{scans++;periods.push(p);restRequests.push(r);return rows;},format:r=>{formats++;return r.map(x=>x.id).join(',');},copy:args=>copied.push(args),box,probe,option,rest,
 set:(fn,delay)=>{const id=++sequence;timers.set(id,{fn,delay});return id;},clear:id=>timers.delete(id)},doc,win);
 const boss=(id,at,kind='boss')=>({id,at,kind});
 const run=()=>{const [id,timer]=timers.entries().next().value;timers.delete(id);timer.fn();};
 const expect=(ok,message)=>check(ok,label+': '+message);
 rows=[boss('expired',now-500000),boss('a',now-1000),boss('b',now+120000),boss('c',now+241000),boss('single',now+362001),boss('rest',now,'break')];
 api.refresh();
 expect(box.value==='a,b,c,single','a transitive chain includes elapsed members until its last boss, excludes fully expired bosses and rests');
 api.copy();expect(copied[0][0]==='tomorrow-end'&&copied[0][1]===box.value&&copied[0][2]===99999999&&formats===1,'whole copy passes the displayed text and rendered cutoff without formatting again');
 expect(probe.textContent===box.value&&widthWrites===1,'the hidden width probe mirrors the full text for intrinsic widest-line sizing');
 expect(timers.size===1&&[...timers.values()][0].delay===241000,'one deadline targets the last boss, with inclusive 121-second adjacent gaps');
 const old=[...timers.values()][0].fn;api.refresh();old();
 expect(timers.size===1&&formats===1&&scans===1,'cached reuse rearms one deadline without formatting/scanning and invalidates queued stale callbacks');
 rest.checked=false;api.refresh();rest.checked=true;api.refresh();
 expect(formats===1&&scans===1,'copy rest toggles do not invalidate the TXT plan or reformat its text');
 now+=1000;run();
 expect(box.value==='a,b,c,single'&&timers.size===1&&[...timers.values()][0].delay===240000,'an early timer preserves future bosses and rearms only the remaining delay');
 now+=240000;run();
 expect(box.value==='single'&&timers.size===1&&[...timers.values()][0].delay===121001,'at the last boss instant the whole chain disappears, and a 121001ms gap splits groups');
 const savedWrites=writes,savedReads=reads,savedWidthWrites=widthWrites;api.refresh();
 expect(writes===savedWrites&&reads===savedReads&&widthWrites===savedWidthWrites,'unchanged text keeps selection and avoids width-probe writes or layout measurement');
 doc.hidden=true;events.visibilitychange();
 expect(timers.size===0,'hidden documents cancel the pending timer');
 const before=scans;now+=500000;doc.hidden=false;events.visibilitychange();
 expect(box.value===''&&scans===before+1&&timers.size===0,'returning removes all overdue rows once without background polling');
 rows=[boss('new',now+5000)];api.invalidate();api.refresh();view.selectedFilter='all';api.set(view);api.refresh();
 expect(timers.size===0,'leaving TXT cancels its expiry reservation');
 view.selectedFilter='txt';view.hiddenMenuCollapsed=false;api.set(view);api.refresh();
 const visibleHeight=box.style.height;box.clientWidth=0;api.fit();
 expect(box.style.height===visibleHeight,'theme-hidden layout cannot overwrite the retained height with zero');box.clientWidth=200;
 expect(timers.size===0,'theme settings do not run TXT expiry work');
 view.hiddenMenuCollapsed=true;api.set(view);api.refresh();events.pagehide();
 expect(timers.size===0,'pagehide cancels expiry');
 now+=5000;events.pageshow();
 expect(box.value===''&&timers.size===0,'pageshow refreshes expired text including an exact single-boss deadline');
 rows=[boss('newer',now+30000)];api.invalidate();api.refresh();const stale=[...timers.values()][0].fn;rows=[boss('replacement',now+60000)];api.invalidate();api.refresh();stale();
 expect(box.value==='replacement'&&timers.size===1&&[...timers.values()][0].delay===60000,'schedule replacement cancels the previous schedule deadline');
 expect(periods.every(p=>p==='tomorrow-end'),'the bulletin keeps its independent next-day 23:59:59 horizon');
 expect(restRequests.every(r=>r===false),'TXT always bypasses derived rest work without modifying copy settings');
 const oldFormats=formats,oldScans=scans,oldReads=reads;api.stop();api.refresh();
 expect(formats===oldFormats&&scans===oldScans&&reads===oldReads,'leaving and returning reuses text and layout when the width is unchanged');
 box.clientWidth=180;api.fit();expect(reads===oldReads+1&&formats===oldFormats&&scans===oldScans,'a changed visible width refits only height without rebuilding content');
 option.checked=true;api.refresh();expect(formats===oldFormats+1&&scans===oldScans+1,'changed copy selection options invalidate the cached plan');
 api.stop();
}
exercise(embedded,'embedded');
if(typeof txtSource==='string')exercise(txtSource,'readable');
const tick=source.slice(source.indexOf('function tick()'),source.indexOf('function renderActive('));
let formats=0,stamp=0;const nodes=new Map();
const api=new Function('element','hooks',`
const leaving=false,data={},document={hidden:false},hiddenMenuCollapsed=true,selectedFilter='txt';
const serverNow=()=>hooks.now(),shareText=()=>hooks.format(),clock=String,dateLabel=String,setText=(node,value)=>node.textContent=value;
const WebNotices={tick:()=>{}},WebLimitedSakura={update:()=>{}},renderCopy=()=>{},updateConnection=()=>{};
${tick};return {tick};
`)(id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);},{now:()=>stamp,format:()=>formats++});
for(let i=0;i<60;i++){stamp++;api.tick();}
check(formats===0&&!nodes.has('events')&&!nodes.has('overview'),'sixty clock ticks never format TXT or draw boss cards');
check(nodes.get('clock').textContent==='60','the visible clock still updates');
check(source.includes('if(open)WebScheduleTXT.stop();else if(wasOpen)renderScheduleTXT();'),'opening theme settings stops the TXT timer and closing restores it');
return checks;

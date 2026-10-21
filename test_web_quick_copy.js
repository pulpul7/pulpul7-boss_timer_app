/* Quick-copy owns its selection rules; only plain-text formatting is shared. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
const code=source.slice(source.indexOf('function legacyTxtIncluded('),source.indexOf('function filename('));
const isBoss=row=>['boss','fixed','invasion'].includes(row.kind);
const seconds={checked:false},data={events:[],boss_catalog:[]};
let ordinaryRows=[],majorCalls=0;
const api=new Function('data','isBoss','element','normalRows','called',`
const serverNow=()=>1000,periodEnd=()=>9000,dayKey=t=>t<5000?'first':'second',
 localDate=t=>new Date(Date.UTC(2026,9,t<5000?21:22)),clock=t=>'12:34:56',displayName=r=>r.name;
const visibleRows=normalRows,customDefaultSelected=()=>{called();throw Error('major policy coupled');};
${code}
return {included:legacyTxtIncluded,text:shareText};
`)(data,isBoss,()=>seconds,()=>ordinaryRows,()=>majorCalls++);
let serial=0;
const def=(name,group='4챕',extra={})=>({id:String(++serial),name,group,kind:'boss',...extra});
const catalog=[def('c2','2챕'),def('absolute2','2챕',{absolute:true}),def('c3','3챕'),def('normal'),
 def('future','10챕'),def('4 층','던전'),def('upper','던전'),def('월 드 보 스','고정보스',{kind:'fixed'}),
 def('발할라 대전','고정보스',{kind:'fixed'}),def('황금 고블린','고정보스',{kind:'fixed'}),
 def('지옥성채 정예','고정보스',{kind:'fixed'}),def('other fixed','고정보스',{kind:'fixed'}),
 def('길던','특수 이벤트')];
const excluded=new Set([0,2,5,7,8,9,10]);
check(catalog.every((d,i)=>api.included({kind:d.kind,name:'alias'},d)===!excluded.has(i)),'quick copy has its own chapter, absolute, dungeon and whitespace-insensitive exclusions');
check(api.included({kind:'invasion'},catalog[3])&&!api.included({kind:'invasion'},catalog[0]),'checked invasions follow the quick-copy home-boss rules');
check(!api.included({kind:'invasion'},catalog[11])&&!api.included({kind:'invasion'},catalog[12]),'fixed and special-event identities do not create checked invasions');
check(!api.included({kind:'invasion',name:'unknown'}),'unresolved invasion cannot bypass checked-home selection');
check(!api.included({kind:'break',name:'rest'})&&!api.included({kind:'maintenance',name:'maintenance'}),'quick text includes boss schedules only');
data.boss_catalog=catalog;
data.events=[0,1,2,3,4].map(i=>({id:'past'+i,catalog_id:catalog[3].id,kind:'boss',name:'past'+i,at:i*100}));
data.events.push({id:'future',catalog_id:catalog[4].id,kind:'boss',name:'future',at:6000},
 {id:'excluded',catalog_id:catalog[0].id,kind:'boss',name:'excluded',at:7000},
 {id:'late',catalog_id:catalog[3].id,kind:'boss',name:'late',at:10000});
const rows=[data.events[3],data.events[4],data.events[5]],text=api.text(rows);
check(text==='▣ 보스 타임테이블 공지(수요일)\n\n12:34 past3\n12:34 past4\n\n10월 22일 목요일 ======\n12:34 future','text uses the notice heading and six equals on subsequent dates');
seconds.checked=true;check(api.text(rows).includes('12:34:56 future'),'seconds preference is shared with the normal formatter');
ordinaryRows=[{name:'dialog selection',kind:'boss',at:1000}];
check(api.text().includes('dialog selection')&&!api.text().includes('future'),'settings copy retains its original row source');
check(api.text(rows).includes('future')&&!api.text(rows).includes('dialog selection'),'explicit filtered rows use the same formatter independently of the main tab');
check(majorCalls===0,'quick copy never invokes the major-tab default policy');
check(api.text([])==='','empty quick-copy result uses the normal empty-text format');
return checks;

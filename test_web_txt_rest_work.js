/* Real copy/TXT row generation must agree without deriving unused rest rows. */
const checks=[];
const check=(ok,message)=>{if(!ok)throw Error(message);checks.push(message);};
const section=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b));
const data={schedule_version:'rest-work',major_version:'1',catalog_version:'1',events:[],rest_rules:[{min_gap_minutes:40,max_gap_minutes:Infinity,row_scale:1}]};
const options={'include-break':{checked:true},'copy-legacy-txt':{checked:false},'major-only':{checked:false},'include-fixed':{checked:true}};
const api=new Function('data','element',`
 let copyViewKey='',copyViewRows=[],derivations=0;
 const isBoss=row=>['boss','fixed','invasion'].includes(row.kind),periodEnd=()=>1e12;
 ${section('function included(', 'function selectedDay(')}
 ${section('function recalculatedRests(', 'function viewRows(').replace('function recalculatedRests(', 'function deriveRests(')}
 function recalculatedRests(...args){derivations++;return deriveRests(...args);}
 ${section('function sharingEvents(', 'function bossRank(')}
 return {rows:rests=>sharingEvents(0,'tomorrow-end',rests),defaultRows:()=>sharingEvents(0,'tomorrow-end'),count:()=>derivations};
`)(data,id=>options[id]);
for(let i=0;i<2000;i++)data.events.push({id:'boss-'+i,name:'boss-'+i,at:i*2700000,end:i*2700000,kind:i%3===0?'fixed':i%3===1?'invasion':'boss',input_order:i});
data.events.push({id:'maintenance',kind:'maintenance',at:500000,end:800000,input_order:2001},{id:'open',kind:'server_open',at:800000,end:800000,input_order:2002});
data.events.sort((a,b)=>a.at-b.at);
const normal=api.defaultRows(),before=api.count(),txt=api.rows(false);
check(normal.some(r=>r.kind==='break')&&before===1,'copy settings still derive real rest rows');
check(api.count()===before&&!txt.some(r=>r.kind==='break'),'TXT performs zero rest derivations for a 2000-boss schedule');
check(JSON.stringify(txt.map(r=>r.id))===JSON.stringify(normal.filter(r=>r.kind!=='break').map(r=>r.id)),'bosses, invasions, fixed bosses and maintenance/open controls remain identical');
check(options['include-break'].checked===true,'TXT does not alter saved copy rest selection');
const restored=api.defaultRows();
check(api.count()===before+1&&restored.some(r=>r.kind==='break'),'opening copy after TXT still restores its own rest rows');
options['include-break'].checked=false;
check(JSON.stringify(api.defaultRows().map(r=>r.id))===JSON.stringify(txt.map(r=>r.id)),'copy without rests and TXT use the same raw row selection');
return checks;

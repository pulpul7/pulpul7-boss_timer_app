/* Production selection module; configured catalog, explicit save, per-nickname scope. */
const checks=[],check=(v,s)=>{if(!v)throw Error(s);checks.push(s);};
const body=source.split('// BEGIN CINEMATIC BOSS PREFERENCES')[1].split('// END CINEMATIC BOSS PREFERENCES')[0];
function fixture(initial={}){
 const values=new Map(Object.entries(initial)),events={},nodes=new Map();let enabled=true,fail=false,writes=0,changes=0,builds=0;
 const make=tag=>({tag,children:[],dataset:{},hidden:false,disabled:false,checked:false,indeterminate:false,textContent:'',listeners:{},
  set id(id){this._id=id;nodes.set(id,this);},get id(){return this._id;},
  setAttribute(){},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;++builds;},
  addEventListener(type,fn){this.listeners[type]=fn;},fire(type='click'){this.listeners[type]?.({target:this});}});
 nodes.set('cinematic-settings-host',make('div'));
 const api=new Function('document','window','localStorage','selectionChanged',`
 let enabled=true;${body};return {...selectionPrefs,enabled(value){enabled=value;selectionPrefs.visibility();}};
 `)({getElementById:id=>nodes.get(id),createElement:make,createTextNode:text=>({textContent:text})},
 {addEventListener:(type,fn)=>events[type]=fn},{getItem:k=>values.get(k)??null,setItem(k,v){if(fail)throw Error('storage denied');values.set(k,v);++writes;}},()=>++changes);
 const inputs=()=>nodes.get('cinematic-boss-settings').children[2].children.map(label=>label.children[0]);
 const input=id=>inputs().find(i=>i.dataset.cinematicChoice===id);
 return {api,values,nodes,events,input,fail:v=>fail=v,writes:()=>writes,changes:()=>changes,builds:()=>builds,
  toggle(id,on){const i=input(id);i.checked=on;i.fire('change');},save(){nodes.get('cinematic-boss-save').fire();}};
}
const majors=b=>b.major===true||b.important===true;
const catalog=[2,3,4,5,6,7,8].map(n=>({id:'chapter'+n,kind:'boss',name:'Boss'+n,group:n+'챕'}));
catalog.push({id:'absolute',kind:'boss',name:'Tyr',group:'2챕',absolute:true},
 {id:'world',kind:'fixed',name:'월드 보스',group:'고정보스'},
 {id:'valhalla',kind:'fixed',name:'발할라 대전',group:'고정보스'},
 ...[4,7,10].map(n=>({id:'floor'+n,kind:'boss',name:n+'층',group:'던전'})),
 ...['굴베','강글','스네르'].map(n=>({id:n,kind:'boss',name:'최하층'+n,group:'던전'})),
 {id:'guild',kind:'boss',name:'길던',group:'특수 이벤트',major:true},
 {id:'siege',kind:'fixed',name:'공성전',group:'고정보스',major:true},
 {id:'anchor',kind:'fixed',name:'알브하임 닻',group:'고정보스'},
 {id:'alias',legacy_ids:['old-alias'],kind:'boss',name:'원래 이름',label:'약칭',group:'5챕'});
const row=id=>({id:'schedule:'+id,catalog_id:id,kind:catalog.find(b=>b.id===id)?.kind||'boss',name:'Displayed alias',at:100000});
const key='BossTimer.cinematic.bosses.v1:alice',f=fixture();f.api.refresh('alice',catalog,majors);
for(const id of ['chapter4','chapter5','chapter6','chapter7','chapter8','absolute','world','floor10','굴베','강글','스네르','guild','siege'])check(f.api.allows(row(id)),'default includes '+id);
for(const id of ['chapter2','chapter3','valhalla','floor4','floor7','anchor'])check(!f.api.allows(row(id)),'default excludes '+id);
check(f.input('chapter:2')&&!f.input('chapter:9')&&f.input('all').indeterminate,'options come from current catalog and all is partially checked');
const builds=f.builds(),changes=f.changes();for(let i=0;i<50;i++)f.api.refresh('alice',catalog,majors);
check(f.builds()===builds&&f.changes()===changes&&f.writes()===0,'unchanged refresh does not rebuild, write or trigger player');
f.toggle('all',false);check(f.api.dirty()&&f.api.allows(row('chapter4'))&&f.writes()===0,'draft does not alter playback or save automatically');
f.save();check(!f.api.dirty()&&!f.api.allows(row('absolute'))&&f.writes()===1,'explicit empty save disables all automatic movies');
f.toggle('chapter:4',true);f.fail(true);f.save();check(f.api.dirty()&&!f.api.allows(row('chapter4')),'storage failure preserves active saved filter and retryable draft');
f.fail(false);f.save();check(f.api.allows(row('chapter4'))&&!f.api.allows(row('chapter5')),'only saved chapter matches');
f.toggle('chapter:4',false);f.nodes.get('cinematic-boss-undo').fire();check(f.input('chapter:4').checked&&!f.api.dirty(),'undo restores saved selection');
const reload=fixture(Object.fromEntries(f.values));reload.api.refresh('alice',catalog,majors);check(reload.api.allows(row('chapter4'))&&!reload.api.allows(row('absolute')),'fresh page restores saved subset');
reload.api.refresh('bob',catalog,majors);check(reload.api.allows(row('absolute'))&&!reload.values.has('BossTimer.cinematic.bosses.v1:bob'),'nickname switch uses independent defaults without a write');
reload.api.enabled(false);check(reload.nodes.get('cinematic-boss-settings').hidden,'section hidden outside Iron Man');reload.api.enabled(true);check(!reload.nodes.get('cinematic-boss-settings').hidden,'section returns with Iron Man');
const expanded=[...catalog,{id:'chapter9',kind:'boss',name:'New boss',group:'9챕'}];
reload.api.refresh('bob',expanded,majors);check(reload.input('chapter:9')&&reload.input('chapter:9').checked&&reload.api.allows(row('chapter9')),'new configured chapter appears without code changes and follows fresh default');
reload.toggle('all',true);reload.save();reload.api.refresh('bob',[...expanded,{id:'chapter10',kind:'boss',name:'Next boss',group:'10챕'}],majors);
check(reload.input('chapter:10').checked&&reload.api.allows(row('chapter10')),'saved all includes future chapters');
check(!reload.api.allows({id:'break',kind:'break'}),'all never selects rest/non-boss events');
const legacy=fixture({[key]:JSON.stringify({schema:1,all:false,selected:['chapter:5']})});legacy.api.refresh('alice',catalog,majors);
check(legacy.api.allows(row('old-alias')),'catalog aliases retain category identity');
legacy.events.storage({key,newValue:null});check(legacy.api.allows(row('alias')),'same-origin storage refresh restores current saved data');
legacy.values.set(key,JSON.stringify({schema:1,all:false,selected:['world']}));legacy.events.storage({key});
check(legacy.api.allows(row('world'))&&!legacy.api.allows(row('alias')),'other tab saved changes apply when no draft is pending');
legacy.toggle('world',false);legacy.values.set(key,JSON.stringify({schema:1,all:true,selected:[]}));legacy.events.storage({key});check(legacy.api.dirty()&&!legacy.api.allows(row('alias')),'other tab cannot overwrite pending draft');
const major=fixture({[key]:JSON.stringify({schema:1,all:false,selected:['major']})});major.api.refresh('alice',expanded,majors);
check(major.api.allows(row('chapter9'))&&major.api.allows(row('siege'))&&major.api.allows(row('guild'))&&!major.api.allows(row('anchor')),'major covers chapter 7+ and configured major fixed bosses/guild dungeon');
check(!major.api.allows(row('absolute'))&&!major.api.allows(row('굴베')),'absolute and lowest bosses use their separate categories below chapter 7');
const invalid=fixture({[key]:JSON.stringify({schema:1,all:'yes',selected:['evil']})});invalid.api.refresh('alice',catalog,majors);
check(invalid.api.allows(row('absolute'))&&!invalid.api.dirty(),'invalid stored data falls back without mutation');
const planBody=source.slice(source.indexOf('function automaticPlan('),source.indexOf('function canPresent('));
let picks=0;
const plan=new Function('selectionPrefs','choose',`let scheduledRow={id:'x',kind:'boss',catalog_id:'chapter3',at:100000};const library=[{}],completed=new Set(),failed=new Set(),skipped=new Set(),groupedRows=[],leaders=new Set(),rowKey=r=>r.id;let preview=null,paused=false;${planBody};return automaticPlan;`)(f.api,()=>{++picks;return {duration:20};});
check(plan()===null&&picks===0,'excluded scheduled boss cannot allocate an automatic movie or takeover paused preview');
check(source.includes('if(!preview&&!selectionPrefs.allows(row))'),'manual previews remain independent of automatic filter');
return {checks:checks.length};

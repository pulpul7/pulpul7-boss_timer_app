/* Production theme-tab/idle logic with a deterministic clock, no waiting. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
function fixture(){
  let now=0,serial=0;const timers=new Map(),events={},nodes=new Map();
  const element=id=>{
    if(!nodes.has(id))nodes.set(id,{id,hidden:['hidden-menu-content','theme-panel'].includes(id),dataset:{},style:{},textContent:'',
      setAttribute(k,v){this[k]=v;},getBoundingClientRect:()=>({height:240}),contains:()=>false,focus(){}});
    return nodes.get(id);
  };
  const tabs=['tab-all','tab-today','tab-custom','tab-theme'].map(element);
  tabs.slice(0,3).forEach((tab,i)=>tab.dataset.filter=['all','today','custom'][i]);
  const document={activeElement:null,addEventListener:(name,handler)=>events[name]=handler,
    querySelectorAll:()=>tabs,querySelector:()=>tabs.find(tab=>tab['aria-selected']==='true')};
  const code=source.slice(source.indexOf('const HIDDEN_MENU_IDLE_MS='),source.indexOf('function applyTheme('));
  const listeners=source.split('\n').find(line=>line.includes("['click','keydown','input','change','scroll','wheel'"));
  const api=new Function('element','document','window','Date','setTimeout','clearTimeout','getComputedStyle',`
    let nicknameValue='ordinary',selectedFilter='all';
    const setText=(n,v)=>n.textContent=v,WebHiddenHelp={close(){}},WebLimitedSakura={refresh(){}},tick=()=>{};
    const localStorage={getItem:()=> 'false',setItem(){throw Error('activity must not write storage');}};
    ${code}\n${listeners}
    refreshHiddenMenuFold();return {toggle:toggleHiddenMenuFold,refresh:refreshHiddenMenuFold,
      owner(name){nicknameValue=name;refreshHiddenMenuFold();},state:()=>hiddenMenuCollapsed};`)(
    element,document,{PointerEvent:class {},matchMedia:()=>({matches:true})},class {static now(){return now;}},
    (fn,delay)=>{timers.set(++serial,{fn,at:now+delay});return serial;},id=>timers.delete(id),()=>({opacity:'1'}));
  return {api,element,events,timers,serial:()=>serial,advance(t){
    while(true){const entry=[...timers].filter(([,v])=>v.at<=t).sort((a,b)=>a[1].at-b[1].at)[0];
      if(!entry)break;now=entry[1].at;timers.delete(entry[0]);entry[1].fn();}now=t;
  }};
}
const f=fixture();
check(f.api.state()&&f.element('hidden-menu-content').hidden&&f.timers.size===0,
  'initial settings are closed even when a previous version saved an open state');
check(f.element('tab-all')['aria-selected']==='true'&&f.element('theme-panel').hidden,'initial selection remains the schedule tab');
f.api.toggle();
check(!f.api.state()&&!f.element('hidden-menu-content').hidden&&f.timers.size===1,'opening settings starts exactly one idle timer');
check(f.element('schedule-view').hidden&&!f.element('theme-panel').hidden&&f.element('tab-theme')['aria-selected']==='true',
  'theme tab shows settings and hides the entire schedule including its clock controls');
f.api.toggle();check(!f.api.state()&&f.timers.size===1,'clicking the selected theme tab again keeps settings open');
f.advance(59999);check(!f.api.state(),'settings remain open until sixty seconds have elapsed');
f.advance(60000);check(f.api.state()&&f.element('hidden-menu-content').hidden&&f.timers.size===0,'sixty seconds without input closes settings');
check(!f.element('schedule-view').hidden&&f.element('theme-panel').hidden&&f.element('tab-all')['aria-selected']==='true',
  'idle close returns to the preceding schedule tab');
f.api.toggle();f.advance(90000);f.events.click({});const timerSerial=f.serial();
for(let i=0;i<200;i++)f.events.scroll({});
check(f.serial()===timerSerial&&f.timers.size===1,'continuous activity reuses the timer without layout or storage churn');
f.advance(120000);check(!f.api.state()&&f.timers.size===1,'a click outside settings extends the idle deadline');
f.advance(149999);check(!f.api.state(),'renewed deadline does not close early');
f.advance(150000);check(f.api.state(),'settings close sixty seconds after the last interaction');
for(const type of ['pointerdown','keydown','input','change','wheel']){
  const g=fixture();g.api.toggle();g.advance(50000);g.events[type]({});g.advance(60000);
  check(!g.api.state(),type+' activity prevents the original deadline from closing settings');
  g.advance(110000);check(g.api.state(),type+' activity allows closing after its own sixty-second deadline');
}
const switched=fixture();switched.api.toggle();switched.api.owner('other');
check(switched.api.state()&&switched.timers.size===0,'changing nickname starts with closed settings and clears the old timer');
check(html.includes('class="hidden-menu-content" data-folded="true" hidden inert'),
  'HTML is already collapsed before the main script runs');
return checks;

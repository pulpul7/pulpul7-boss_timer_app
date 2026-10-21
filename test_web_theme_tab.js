/* Theme page navigation without changing stored schedule filters or tab order. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
function section(a,b){return source.slice(source.indexOf(a),source.indexOf(b));}
function orderFixture(saved){
  const buttons=['all','custom','txt'].map(filter=>({id:'tab-'+filter,dataset:{filter}}));
  const theme={id:'tab-theme',dataset:{}},items=[...buttons,theme],values=new Map();
  if(saved)values.set('order',JSON.stringify(saved));
  for(const button of buttons)button.after=(node)=>{items.splice(items.indexOf(node),1);items.splice(items.indexOf(button)+1,0,node);};
  const bar={querySelectorAll:()=>items.filter(button=>button.dataset.filter),append(...nodes){
    for(const node of nodes){const i=items.indexOf(node);if(i>=0)items.splice(i,1);items.push(node);}
  }};
  const api=new Function('document','element','localStorage',`
    const TAB_ORDER_KEY='order';${section('function scheduleTabs()', 'function updateTabDrag(')}
    return {restore:restoreTabOrder,move:reorderScheduleTab};`)(
    {querySelector:()=>bar},id=>items.find(n=>n.id===id),{getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)});
  return {api,buttons,theme,items,values};
}
const o=orderFixture(['custom','all','today']);o.api.restore();
check(o.items.map(x=>x.id).join(',')==='tab-custom,tab-txt,tab-all,tab-theme','old order inserts the new text tab beside major bosses and ignores removed tab');
o.api.move(o.buttons[0],o.buttons[1]);
check(o.items[3]===o.theme,'dragging a schedule tab left keeps theme at the far right');
o.api.move(o.items[0],o.items[1]);
check(o.items[3]===o.theme,'swapping a schedule tab right keeps theme at the far right');
check(!o.api.move(o.buttons[0],o.theme),'theme tab cannot be a schedule drag target');
check(!o.values.get('order').includes('theme'),'theme tab does not enter saved schedule filter order');
const tabs=['all','custom','txt'].map(filter=>({id:'tab-'+filter,dataset:{filter},setAttribute(k,v){this[k]=v;}}));
let folds=0,ticks=0;
const nav=new Function('document','element','onFold','onTick',`
 let selectedFilter='all',hiddenMenuCollapsed=false,allDay='tomorrow',customEditing=false,
   showRest=false,showFixed=false,mainKey='',summaryKey='',renderedKey='',activeKey='';
 const restPrefs={all:true,today:false,custom:true},fixedPrefs={all:true,today:false,custom:true};
 const resetMasterLock=()=>{},refreshRestFilter=()=>{},refreshFixedFilter=()=>{},refreshAlarms=()=>{},renderScheduleTXT=()=>{};
 const WebHiddenHelp={close(){}},refreshHiddenMenuFold=onFold,tick=onTick;
 ${section('function selectFilter(', 'async function loginExpired(')}
 return {select:selectFilter,open:()=>hiddenMenuCollapsed=false,
   state:()=>({selectedFilter,hiddenMenuCollapsed,allDay,showRest,showFixed})};`)(
  {querySelectorAll:()=>tabs},()=>({dataset:{},setAttribute(){}}),()=>folds++,()=>ticks++);
nav.select('all');
check(nav.state().hiddenMenuCollapsed&&ticks===1,'clicking the previous schedule tab leaves theme even when its filter did not change');
check(nav.state().allDay===''&&nav.state().showRest&&nav.state().showFixed,'returning to schedule resets today and restores that tab’s filters');
nav.select('all');check(ticks===1,'clicking the active schedule tab does not redraw it again');
nav.open();nav.select('today');
check(nav.state().selectedFilter==='custom'&&nav.state().showRest&&nav.state().showFixed&&folds===2,'legacy major tab restores the renamed personal tab and its own preferences');
check(tabs.filter(tab=>tab['aria-selected']==='true').length===1,'schedule return leaves exactly one selected schedule tab');
check(tabs[1]['aria-selected']==='true','restored major tab selects the existing custom tab node');
nav.select('txt');check(nav.state().selectedFilter==='txt'&&tabs[2]['aria-selected']==='true','new text tab participates in ordinary tab navigation');
let calls=[];
const themeTick=new Function('calls',`
 const leaving=false,data={},document={hidden:false},hiddenMenuCollapsed=false,serverNow=()=>123;
 const WebNotices={tick:()=>calls.push('notice')},WebLimitedSakura={update:()=>calls.push('butterfly')},updateConnection=()=>calls.push('connection');
 ${section('function tick()', 'function renderActive(')};return tick;`)(calls);
themeTick();
check(calls.join(',')==='notice,butterfly,connection','open theme page retains alerts and connection status without touching schedule rows or clocks');
const spectrumCode=section('// BEGIN WEB BOSS SPECTRUM MODULE','// END WEB BOSS SPECTRUM MODULE');
const spectrum=new Function('document',spectrumCode+';return WebBossSpectrum;')({});
const yellows=spectrum.palettes.forsythia;
check(yellows.length===30&&new Set(yellows).size===30,'forsythia keeps thirty distinct slots and existing slot numbers');
check(yellows.every(color=>spectrum.ink(color)==='#000000'),'all pale-yellow spectrum fills retain contrasting dark text');
check(yellows.every(color=>parseInt(color.slice(1,3),16)>235&&parseInt(color.slice(3,5),16)>215),'forsythia spectrum stays bright instead of dark amber');
const fill=new Function('hooks',section('function gradient(color)', 'let numbers=' )+';return gradient;')({theme:()=> 'forsythia'});
check(fill('#fff2aa')==='linear-gradient(90deg,#fff2aab3,#fff2aab3)','forsythia assigns thirty-percent transparency to spectrum fills rather than text');
return checks;

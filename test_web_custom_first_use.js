/* Offline first-use and saved-selection checks. Parameter: source. */
const checks=[],values=new Map(),nodes=new Map();
function check(value,message){if(!value)throw Error(message);checks.push(message);}
function el(id){
  if(!nodes.has(id))nodes.set(id,{hidden:false,disabled:false,textContent:'',setAttribute(){},closest:()=>el('hero'),
    append(child){child.parentElement=this;}});
  return nodes.get(id);
}
const state=source.slice(source.indexOf("let customScope=''"),source.indexOf('function readCustomSelection('));
const read=source.slice(source.indexOf('function readCustomSelection('),source.indexOf('function customRowSelected('));
const ui=source.slice(source.indexOf('function refreshCustomUI('),source.indexOf('function readRestPreferences('));
const api=new Function('element','localStorage',`
 let selectedFilter='custom',data=null,fixedPrefs={custom:true},viewKey='',mainKey='',renderedKey='',activeKey='';
 let stamp=100;const performance={now:()=>stamp},setText=(node,text)=>node.textContent=text;
 let paints=0;const WebBossSpectrum={refresh(){paints++;}},renderCustomEditor=()=>{},tick=()=>{},refreshAlarms=()=>{};
 ${state}\n${read}\n${ui}
 const id='a'.repeat(24);customById=new Map([[id,{kind:'boss'}]]);customCatalogReady=true;
 return {load(){customScope='';readCustomSelection('prefs');refreshCustomUI();},
  refresh(){refreshCustomUI();},select(){customDraft.add(id);refreshCustomUI();},time(value){stamp=value;refreshCustomUI();},
  save(){saveCustomSelection();},back(){customEditing=false;refreshCustomUI();},
  tab(value){selectedFilter=value;if(value!=='custom')customEditing=false;refreshCustomUI();},
  state(){return {count:customDraft.size,editing:customEditing,paints};}};
`)(el,{getItem:key=>values.get(key) ?? null,setItem:(key,value)=>values.set(key,value)});
api.load();
check(api.state().count===0&&api.state().editing,'first use has zero selections and opens the editor');
check(!el('custom-editor').hidden&&el('events').hidden&&el('overview').hidden&&el('empty').hidden,'first use shows settings instead of empty schedule or rests');
check(el('custom-close').disabled,'empty selection cannot close to an empty schedule');
check(values.size===0,'opening settings does not save fabricated defaults');
const firstPaints=api.state().paints;api.refresh();api.refresh();
check(api.state().paints===firstPaints,'unchanged editor state does not repaint choices or spectrum on clock ticks');
api.select();
check(api.state().editing&&!el('custom-close').disabled,'selecting the first boss keeps editing and enables back');
const changedPaints=api.state().paints;api.time(1100);api.time(2100);
check(api.state().paints===changedPaints&&el('custom-save-status').textContent.includes('5초'),
  'autosave countdown updates its text without repainting unchanged choices');
api.save();api.back();
check(!api.state().editing&&el('custom-editor').hidden,'selected user can return to the schedule');
api.tab('all');api.tab('custom');
check(!api.state().editing,'returning with a selection keeps the normal schedule view');
api.load();
check(api.state().count===1&&!api.state().editing,'saved personal selections survive reload');
values.set('prefs',JSON.stringify({schema:1,selected:[]}));api.load();
check(api.state().count===0&&api.state().editing,'explicitly saved empty selection opens settings too');
return checks;

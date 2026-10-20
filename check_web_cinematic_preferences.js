(async()=>{
 const api=WebIronmanCinematic,preferences=api.preferences.bind(api),update=api.update.bind(api),el=id=>document.getElementById(id);
 const checks=[],check=(v,s)=>{if(!v)throw Error(s);checks.push(s);},sleep=ms=>new Promise(r=>setTimeout(r,ms));
 const wait=async(test,label)=>{const end=performance.now()+10000;while(!test()){if(performance.now()>end)throw Error(label+' '+JSON.stringify({mode:document.querySelector('.hero').dataset.cinematic,src:el('ironman-cinematic-video')?.getAttribute('src'),errors:window.previewErrors}));await sleep(30);}};
 const owner=nicknameValue+'-cinematic-fixture',key='BossTimer.cinematic.bosses.v1:'+owner,old=localStorage.getItem(key);
 const input=id=>document.querySelector(`[data-cinematic-choice="${id}"]`),toggle=(id,value)=>{input(id).checked=value;input(id).dispatchEvent(new Event('change',{bubbles:true}));};
 const catalog=[2,3,4,5,6,7,8,9].map(n=>({id:'fixture-chapter-'+n,kind:'boss',name:'Fixture '+n,group:n+'챕'}));
 catalog.push({id:'fixture-absolute',kind:'boss',name:'절대자',absolute:true,group:'2챕'},{id:'fixture-siege',kind:'fixed',name:'공성전',major:true,group:'고정보스'});
 const row={id:'filtered-fixture',catalog_id:'fixture-chapter-3',kind:'boss',name:'Fixture 3'};
 api.preferences=()=>{};api.update=()=>{};
 try{
  localStorage.removeItem(key);preferences(owner,catalog,majorBoss);
  el('tab-theme').click();
  check(!el('cinematic-boss-settings').hidden&&!el('theme-panel').hidden&&el('custom-editor').hidden,'selected Iron Man exposes options directly in theme tab');
  check(input('chapter:9')&&input('chapter:9').checked,'chapter 9 comes from configured catalog');
  check(!input('chapter:2').checked&&!input('chapter:3').checked&&input('absolute').checked&&input('world').checked&&input('floor:10').checked&&!input('floor:4').checked&&!input('floor:7').checked&&!input('valhalla').checked,'requested default checkboxes');
  check(el('cinematic-boss-settings').parentElement.id==='cinematic-settings-host'&&el('cinematic-settings-host').previousElementSibling.id==='theme-controls','cinematic section follows theme choices');
  check([...el('theme-controls').children].map(n=>n.querySelector('.theme-family-label')?.textContent).join('|')==='컬러|배경|리미티드 스페셜 에디션','background precedes limited special edition');
  for(const label of el('cinematic-boss-settings').querySelectorAll('label')){
   const r=label.getBoundingClientRect(),i=label.querySelector('input').getBoundingClientRect();
   check(r.width>0&&r.left>=0&&r.right<=innerWidth+1&&r.height>=40&&i.width>=18&&i.height>=18,'checkbox label fits '+label.textContent);
  }
  check(document.documentElement.scrollWidth<=innerWidth,'no horizontal overflow');
  toggle('all',false);check(api.selectionDirty()&&!localStorage.getItem(key),'checkbox draft requires explicit save');el('cinematic-boss-save').click();
  check(!api.selectionDirty()&&JSON.parse(localStorage.getItem(key)).selected.length===0,'empty choice saves explicitly');
  const library=(await(await fetch('/cinematic/catalog.json')).json()).clips;
  el('tab-all').click();window.scrollTo(0,0);WebMenuIdle.activity();
  api.enable(false);const at=Date.now();row.at=at+library[0].duration*1000+5000-500;update(at,row,[row],'preference-fixture');api.enable(true,()=>false);
  const video=el('ironman-cinematic-video');await sleep(250);
  check(!video.hasAttribute('src')&&video.paused&&video.hidden,'unselected boss creates no decoder source or sound');
  toggle('chapter:3',true);await sleep(50);check(!video.hasAttribute('src'),'unsaved checkbox does not change active playback policy');
  el('cinematic-boss-save').click();await wait(()=>!video.paused&&!video.hidden&&document.querySelector('.hero').dataset.cinematic==='playing','selected boss did not start automatically');
  check(video.currentSrc.startsWith('blob:')&&video.muted,'saved category starts local automatic playback');
  toggle('all',false);el('cinematic-boss-save').click();await wait(()=>video.paused&&!video.hasAttribute('src'),'exclusion did not release active decoder');
  check(video.hidden,'unchecking active boss stops video and removes picture');
  await api.previewNext();await wait(()=>!video.paused&&!video.hidden,'manual preview should bypass filter');check(true,'manual preview remains available with no automatic categories');
  api.enable(false);preferences(owner+'-other',catalog,majorBoss);api.enable(true,()=>false);
  check(input('absolute').checked&&!input('chapter:3').checked,'nickname switch gets isolated defaults');
  el('tab-theme').click();el('theme-default').click();check(el('cinematic-boss-settings').hidden,'other themes hide cinematic section');
  el('theme-avengers').click();check(!el('cinematic-boss-settings').hidden,'returning to Iron Man restores section');
  check(!window.previewErrors.length,'no runtime or CSP errors');
  return {checks:checks.length,width:innerWidth};
 }finally{
  api.enable(false);if(old===null)localStorage.removeItem(key);else localStorage.setItem(key,old);
  api.preferences=preferences;api.update=update;preferences(nicknameValue,customCatalog,majorBoss);
 }
})()

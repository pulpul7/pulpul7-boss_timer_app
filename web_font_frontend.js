// Browser-owned font choices. Imported into schedule.js; no OS installation or server writes.
const WebFonts=(()=>{
  const el=id=>document.getElementById(id),system="system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";
  const state={owner:null,scope:0,serial:0,selected:'system',loading:'',default:'system',catalog:[],custom:[],catalogPromise:null,dbPromise:null,cache:new Map(),adding:false,sizeOffset:0,sizeDefaults:{},canSaveDefault:false,savingDefault:false};
  const MAX_BYTES=8*1024*1024,MAX_PERSONAL=20,MAX_TOTAL=64*1024*1024;
  if(!el('web-font-controls') || !el('web-font-select')) return {refreshScope:async()=>{},onStorage:()=>{},family:()=>system,canSaveDefaults:()=>false};
  function key(owner=state.owner) {return 'BossTimer.webFont.v1:'+owner;}
  function bumpKey(owner=state.owner) {return 'BossTimer.webFont.files.v1:'+owner;}
  function sizeKey(owner=state.owner) {return 'BossTimer.webFont.size.v2:'+owner;}
  function baseSize(id=state.selected) {return state.sizeDefaults[id] ?? 0;}
  function sizeOverrides() {
    try {
      const data=JSON.parse(localStorage.getItem(sizeKey()) || '{}'),result={};
      for(const [id,value] of Object.entries(data.overrides || {}).slice(0,121)) {
        if(/^[a-z0-9-]{1,80}$/.test(id) && Number.isInteger(value) && value>=-4 && value<=6) result[id]=value;
      }
      return result;
    } catch(_) {return {};}
  }
  function readSize(id=state.selected) {
    try {
      const personal=sizeOverrides();if(Object.hasOwn(personal,id)) return personal[id];
      const old=localStorage.getItem('BossTimer.webFont.size.v1:'+state.owner);
      if(!localStorage.getItem(sizeKey()) && old!==null && id===(localStorage.getItem(key()) || state.default)) {
        const value=Number(old);if(Number.isInteger(value) && value>=-4 && value<=6) return value;
      }
    } catch(_) {}
    return baseSize(id);
  }
  function renderSize() {
    const value=state.sizeOffset;
    const busy=state.owner===null || !!state.loading || state.adding || state.savingDefault;
    if(el('web-font-size-minus')) el('web-font-size-minus').disabled=busy || value<=-4;
    if(el('web-font-size-plus')) el('web-font-size-plus').disabled=busy || value>=6;
    if(el('web-font-size-default')) el('web-font-size-default').disabled=busy || value===baseSize();
    const save=el('web-font-size-save');
    if(save) {
      save.hidden=!state.canSaveDefault;
      save.disabled=busy || value===baseSize() || !fontRows().find(row=>row.id===state.selected)?.protected;
      save.textContent=state.savingDefault?'저장 중':'기본저장';
    }
  }
  function applySize(value,persist=false) {
    value=Math.max(-4,Math.min(6,Number.isInteger(value)?value:0));
    state.sizeOffset=value;
    document.documentElement.style.setProperty('--web-font-size-offset',value+'px');
    document.documentElement.classList.toggle('has-font-size-adjustment',value!==0);
    renderSize();
    if(persist) {
      try {
        const overrides=sizeOverrides();
        if(value===baseSize()) delete overrides[state.selected];else overrides[state.selected]=value;
        localStorage.setItem(sizeKey(),JSON.stringify({schema:1,overrides}));
        localStorage.removeItem('BossTimer.webFont.size.v1:'+state.owner);
      }
      catch(_) {note('이 브라우저에서는 글자 크기를 저장할 수 없습니다. 현재 화면에서만 적용됩니다.');}
    }
    window.dispatchEvent(new Event('bosstimer-font-changed'));
  }
  function note(message) {
    el('web-font-note').textContent=message;
    const help=el('font-help-button'),warning=/못|실패|저장할 수 없|지원하지/.test(message);
    help?.setAttribute('data-warning',String(warning));
    help?.setAttribute('aria-label',warning?'글꼴 설명 · 확인 필요':'글꼴 설명');
  }
  function resetFamily() {document.documentElement.style.setProperty('--web-font-family',system);}
  function fontRows() {return [{id:'system',number:0,name:'기기 기본 폰트',protected:true},...state.catalog,...state.custom];}
  function render() {
    const select=el('web-font-select');select.replaceChildren();
    for(const row of fontRows()) {
      const option=document.createElement('option');option.value=row.id;
      option.textContent=String(row.number).padStart(2,'0')+'. '+row.name+(row.protected?'':' · 개인 추가');select.appendChild(option);
    }
    select.value=state.loading || state.selected;select.disabled=state.adding || state.savingDefault;
    const row=fontRows().find(item=>item.id===state.selected);
    const source=el('web-font-source'),license=el('web-font-license');
    source.hidden=!(row?.protected && typeof row.source==='string' && /^https:\/\//.test(row.source));
    license.hidden=!(row?.protected && /^\/fonts\/licenses\/[a-z0-9-]+\.txt$/.test(row.license_url || ''));
    if(!source.hidden) source.href=row.source;else source.removeAttribute('href');
    if(!license.hidden) license.href=row.license_url;else license.removeAttribute('href');
    el('web-font-delete').disabled=!row || row.protected || state.adding || Boolean(state.loading);
    el('web-font-delete').title=row?.protected?'기본 배포 폰트는 삭제할 수 없습니다.':'이 닉네임으로 직접 추가한 폰트만 삭제합니다.';
    el('web-font-add').disabled=state.adding || typeof FontFace!=='function' || !window.indexedDB;
    renderSize();
  }
  function openDb() {
    if(state.dbPromise) return state.dbPromise;
    state.dbPromise=new Promise((resolve,reject)=>{
      try {
        if(!window.indexedDB) throw Error('storage unsupported');
        const request=window.indexedDB.open('BossTimer.webFonts.v1',1);
        request.onupgradeneeded=()=>{const store=request.result.createObjectStore('fonts',{keyPath:'key'});store.createIndex('owner','owner');};
        request.onerror=()=>reject(Error('storage unavailable'));request.onblocked=()=>reject(Error('storage blocked'));
        request.onsuccess=()=>{const db=request.result;db.onversionchange=()=>db.close();resolve(db);};
      } catch(error) {reject(error);}
    });
    return state.dbPromise;
  }
  async function dbAction(mode,action) {
    const db=await openDb();return new Promise((resolve,reject)=>{
      try {
        const tx=db.transaction('fonts',mode),request=action(tx.objectStore('fonts'));
        tx.oncomplete=()=>resolve(request.result);tx.onerror=tx.onabort=()=>reject(Error('storage failed'));
      } catch(error) {reject(error);}
    });
  }
  async function loadCatalog() {
    if(!state.catalogPromise) state.catalogPromise=(async()=>{
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
      try {
        const response=await fetch('/fonts/catalog.json',{cache:'no-cache',signal:controller.signal});
        if(!response.ok) throw Error('catalog unavailable');
        const data=await response.json();
        if(data.schema!==1 || !Array.isArray(data.fonts) || data.fonts.length>100) throw Error('bad catalog');
        const seen=new Set();
        for(const row of data.fonts) {
          if(!row || typeof row.id!=='string' || !/^[a-z0-9-]{1,80}$/.test(row.id) || seen.has(row.id) || !Number.isInteger(row.number) || row.number<1 || row.number>10000 ||
             typeof row.name!=='string' || row.name.length>80 || typeof row.url!=='string' || !/^\/fonts\/[a-z0-9-]+\.(?:woff2?|ttf|otf)\?v=[a-f0-9]{16}$/.test(row.url)) throw Error('bad font row');
          seen.add(row.id);row.protected=true;
        }
        state.catalog=data.fonts;state.default=seen.has(data.default)?data.default:'system';
        state.sizeDefaults={};
        for(const [id,value] of Object.entries(data.size_defaults || {}).slice(0,101)) {
          if((id==='system' || seen.has(id)) && Number.isInteger(value) && value>=-4 && value<=6) state.sizeDefaults[id]=value;
        }
        state.canSaveDefault=data.can_save_default===true;
        window.dispatchEvent(new Event('bosstimer-font-catalog-ready'));
      } catch(_) {state.catalog=[];state.default='system';}
      finally {clearTimeout(timer);}
    })();
    return state.catalogPromise;
  }
  async function loadPersonal(owner) {
    try {
      const rows=await dbAction('readonly',store=>store.index('owner').getAll(owner));
      return rows.filter(row=>row.owner===owner && /^user-[a-f0-9]{32}$/.test(row.id) && typeof row.name==='string' &&
        Number.isInteger(row.number) && row.number>0 && row.blob instanceof Blob && row.blob.size<=MAX_BYTES).slice(0,MAX_PERSONAL);
    } catch(_) {return [];}
  }
  function chooseSaved() {
    let selected=state.default;
    try {const saved=localStorage.getItem(key());if(fontRows().some(row=>row.id===saved)) selected=saved;} catch(_) {}
    return selected;
  }
  function family() {return document.documentElement.style.getPropertyValue('--web-font-family') || system;}
  async function loadFace(row) {
    const cacheKey=row.protected?row.id:state.owner+'|'+row.id;
    if(state.cache.has(cacheKey)) return state.cache.get(cacheKey);
    if(typeof FontFace!=='function') throw Error('font unsupported');
    const fontName='BossTimerFont_'+row.id.replaceAll('-','_');
    const source=row.protected?'url("'+row.url+'")':await row.blob.arrayBuffer();
    const face=await new FontFace(fontName,source,{weight:'400',style:'normal',display:'swap'}).load();
    const item={face,family:'"'+fontName+'",'+system};state.cache.set(cacheKey,item);
    for(const [id,entry] of state.cache) {
      if(state.cache.size<=3) break;
      if(entry.family!==family() && id!==cacheKey) {document.fonts.delete(entry.face);state.cache.delete(id);}
    }
    return item;
  }
  async function select(id,persist=true) {
    const serial=++state.serial,owner=state.owner,row=fontRows().find(item=>item.id===id);
    if(!row) return;
    state.loading=id;render();
    note(row.id==='system'?'기기의 기본 글꼴을 사용합니다.':'폰트를 준비합니다…');
    try {
      const loaded=row.id==='system'?null:await loadFace(row);
      if(serial!==state.serial || owner!==state.owner) return;
      if(loaded) {document.fonts.add(loaded.face);document.documentElement.style.setProperty('--web-font-family',loaded.family);}
      else resetFamily();
      state.selected=row.id;
      applySize(readSize(row.id));
      if(persist) {try {localStorage.setItem(key(),row.id);} catch(_) {note('이 브라우저에서는 선택을 저장할 수 없습니다.');render();return;}}
      note(row.id==='system'?'브라우저가 제공하는 현재 기기의 시스템 글꼴입니다.':row.name+' · '+(row.protected?'배포 폰트':'이 기기·브라우저의 개인 폰트'));
      render();window.dispatchEvent(new Event('bosstimer-font-changed'));
    } catch(_) {
      if(serial!==state.serial || owner!==state.owner) return;
      state.selected='system';resetFamily();applySize(readSize('system'));render();note('폰트를 불러오지 못해 기기 기본 글꼴로 표시합니다. 다시 선택해주세요.');
    } finally {if(serial===state.serial && owner===state.owner) {state.loading='';render();}}
  }
  async function refreshScope(owner,visible) {
    el('web-font-controls').hidden=!visible;el('hidden-menu-title').hidden=!visible;
    if(state.owner===owner) return;
    state.owner=owner;const scope=++state.scope;++state.serial;state.selected='system';state.loading='';state.custom=[];resetFamily();render();
    applySize(0);
    await loadCatalog();const custom=await loadPersonal(owner);
    if(scope!==state.scope || owner!==state.owner) return;
    state.custom=custom;render();await select(chooseSaved(),false);
  }
  async function reloadPersonal() {
    const owner=state.owner,scope=state.scope,rows=await loadPersonal(owner);
    if(owner!==state.owner || scope!==state.scope) return;
    state.custom=rows;render();await select(chooseSaved(),false);
  }
  function signalPersonal(owner) {try {localStorage.setItem(bumpKey(owner),String(Date.now())+Math.random());} catch(_) {}}
  async function add(file) {
    if(!file || state.adding) return;
    const owner=state.owner,scope=state.scope;state.adding=true;render();note('폰트 파일을 확인하고 이 브라우저에 저장합니다…');
    try {
      if(!/\.(woff2?|ttf|otf)$/i.test(file.name) || file.size<=0 || file.size>MAX_BYTES) throw Error('8MB 이하의 TTF·OTF·WOFF·WOFF2 폰트를 선택해주세요.');
      if(state.custom.length>=MAX_PERSONAL || state.custom.reduce((n,row)=>n+row.blob.size,0)+file.size>MAX_TOTAL) throw Error('개인 폰트는 20개·총 64MB까지 보관할 수 있습니다.');
      const data=await file.arrayBuffer(),bytes=new Uint8Array(data.slice(0,4));
      const signature=Array.from(bytes).map(v=>v.toString(16).padStart(2,'0')).join('');
      if(!['00010000','4f54544f','774f4646','774f4632'].includes(signature)) throw Error('올바른 폰트 파일이 아닙니다.');
      const random=Array.from(crypto.getRandomValues(new Uint8Array(16))).map(v=>v.toString(16).padStart(2,'0')).join('');
      const id='user-'+random,name=file.name.replace(/\.(woff2?|ttf|otf)$/i,'').replace(/[\u0000-\u001f\u007f]/g,'').slice(0,80) || '추가 폰트';
      let lastNumber=1000;
      try {const saved=Number(localStorage.getItem('BossTimer.webFont.number.v1:'+owner));if(Number.isInteger(saved) && saved>=1000 && saved<1000000) lastNumber=saved;} catch(_) {}
      const row={key:owner+'|'+id,owner,id,name,number:Math.max(lastNumber,...state.custom.map(row=>row.number))+1,protected:false,blob:file};
      await loadFace(row);
      if(owner!==state.owner || scope!==state.scope) return;
      await dbAction('readwrite',store=>store.put(row));
      try {localStorage.setItem('BossTimer.webFont.number.v1:'+owner,String(row.number));} catch(_) {}
      signalPersonal(owner);
      if(owner!==state.owner || scope!==state.scope) return;
      state.custom.push(row);render();await select(id);
    } catch(error) {if(owner===state.owner) note(error.message==='storage failed' || error.message==='storage unavailable' || error.message==='storage unsupported' || error.message==='storage blocked'?
      '브라우저 저장공간에 폰트를 보관하지 못했습니다. 저장 권한과 용량을 확인해주세요.':error.message || '폰트 추가 실패');}
    finally {state.adding=false;el('web-font-file').value='';render();}
  }
  async function remove() {
    const owner=state.owner,id=state.selected,row=state.custom.find(item=>item.id===id);
    if(!row || row.protected || state.adding || state.loading) return;
    state.adding=true;render();
    try {
      await dbAction('readwrite',store=>store.delete(row.key));signalPersonal(owner);
      if(owner!==state.owner) return;
      ++state.serial;state.custom=state.custom.filter(item=>item.id!==id);
      const cached=state.cache.get(owner+'|'+id);if(cached) {document.fonts.delete(cached.face);state.cache.delete(owner+'|'+id);}
      await select(state.default);note('개인 추가 폰트를 삭제했습니다.');
    } catch(_) {if(owner===state.owner) note('폰트를 삭제하지 못했습니다. 브라우저 저장 권한을 확인해주세요.');}
    finally {state.adding=false;render();}
  }
  function onStorage(event) {
    if(event.key===sizeKey() || event.key===null) applySize(readSize());
    if(event.key===key() || event.key===bumpKey() || event.key===null) reloadPersonal();
  }
  async function saveSizeDefault() {
    if(!state.canSaveDefault || state.savingDefault || state.loading || state.adding || state.sizeOffset===baseSize()) return;
    const owner=state.owner,id=state.selected,value=state.sizeOffset;
    if(!fontRows().find(row=>row.id===id)?.protected) return;
    state.savingDefault=true;renderSize();render();
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
    try {
      const response=await fetch('/api/fonts/default-size',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({font_id:id,size:value}),signal:controller.signal});
      const result=await response.json();
      if(!response.ok || result.ok!==true) throw Error(result.message || '기본 크기를 저장하지 못했습니다.');
      if(owner!==state.owner) return;
      state.sizeDefaults={...state.sizeDefaults,[id]:value};
      if(state.selected===id) applySize(value,true);
      note(result.message || '폰트 기본 크기를 저장했습니다.');
    } catch(error) {if(owner===state.owner) note(error.message || '기본 크기를 저장하지 못했습니다.');}
    finally {clearTimeout(timer);state.savingDefault=false;render();}
  }
  el('web-font-select').addEventListener('change',()=>select(el('web-font-select').value));
  el('web-font-add').addEventListener('click',()=>el('web-font-file').click());
  el('web-font-file').addEventListener('change',()=>add(el('web-font-file').files[0]));
  el('web-font-delete').addEventListener('click',remove);
  el('web-font-size-minus')?.addEventListener('click',()=>{if(state.owner!==null) applySize(state.sizeOffset-1,true);});
  el('web-font-size-plus')?.addEventListener('click',()=>{if(state.owner!==null) applySize(state.sizeOffset+1,true);});
  el('web-font-size-default')?.addEventListener('click',()=>{if(state.owner!==null) applySize(baseSize(),true);});
  el('web-font-size-save')?.addEventListener('click',saveSizeDefault);
  return {refreshScope,onStorage,family,canSaveDefaults:()=>state.canSaveDefault};
})();

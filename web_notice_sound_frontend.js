// Browser-owned banner sounds. Personal files never leave this browser.
const WebNoticeSound=(()=>{
  const el=id=>document.getElementById(id),MAX_FILE=4*1024*1024,MAX_TOTAL=20*1024*1024,MAX_FILES=10;
  const defaults=[{id:'marimba',name:'01. 마림바 · 또롱'},{id:'water',name:'02. 물방울 · 톡또옥'},
    {id:'piano',name:'03. 피아노 · 띵'},{id:'air-raid',name:'04. 공습경보',url:'/sounds/air-raid.mp3'},
    {id:'nuclear',name:'05. 핵경보',url:'/sounds/nuclear.mp3',level:.28}];
  const state={owner:null,scope:0,selected:'water',enabled:true,volume:35,custom:[],busy:false,context:null,buffer:null,bufferId:'',pending:null,db:null,playing:null,serial:0,last:-Infinity};
  const key=()=> 'BossTimer.webNoticeSound.v1:'+state.owner;
  const signalKey=()=>key()+':files';
  const rows=()=>[...defaults,...state.custom];
  const row=()=>rows().find(item=>item.id===state.selected) || defaults[1];
  const note=message=>{if(el('notice-sound-note'))el('notice-sound-note').textContent=message;};
  function save(){try{localStorage.setItem(key(),JSON.stringify({selected:state.selected,enabled:state.enabled,volume:state.volume}));}catch(_){note('이 브라우저에서는 알림음 설정을 저장할 수 없습니다.');}}
  function read(){
    state.selected='water';state.enabled=true;state.volume=35;
    try{const value=JSON.parse(localStorage.getItem(key()) || '{}');
      if(typeof value.selected==='string' && rows().some(r=>r.id===value.selected))state.selected=value.selected;
      if(typeof value.enabled==='boolean')state.enabled=value.enabled;
      if(Number.isInteger(value.volume) && value.volume>=0 && value.volume<=100)state.volume=value.volume;
    }catch(_){}
  }
  function render(){
    const select=el('notice-sound-select');if(!select)return;
    select.replaceChildren(...rows().map(item=>{const option=document.createElement('option');option.value=item.id;option.textContent=item.name+(item.blob?' · 개인':'');return option;}));
    select.value=state.selected;select.disabled=state.busy;
    el('notice-sound-enabled').checked=state.enabled;
    el('notice-sound-volume').value=String(state.volume);el('notice-sound-volume-value').textContent=state.volume+'%';
    el('notice-sound-preview').disabled=state.busy || !audioSupported();
    el('notice-sound-add').disabled=state.busy || !window.indexedDB || !audioSupported();
    el('notice-sound-delete').disabled=state.busy || !row().blob;
    el('notice-sound-delete').title=row().blob?'이 닉네임으로 직접 추가한 음원만 삭제합니다.':'기본 알림음은 삭제할 수 없습니다.';
  }
  function audioSupported(){return typeof (window.AudioContext || window.webkitAudioContext)==='function';}
  function stop(){state.serial++;if(state.playing){try{state.playing.stop();}catch(_){}state.playing=null;}}
  function resetBuffer(){state.buffer=null;state.bufferId='';state.pending=null;}
  function openDb(){
    if(state.db)return state.db;
    state.db=new Promise((resolve,reject)=>{
      let expired=false;const timer=setTimeout(()=>{expired=true;reject(Error('개인 음원 저장소를 열지 못했습니다.'));},5000);
      const request=window.indexedDB.open('BossTimer.webNoticeSounds.v1',1);
      request.onupgradeneeded=()=>{request.result.createObjectStore('sounds',{keyPath:'key'}).createIndex('owner','owner');};
      request.onerror=request.onblocked=()=>{clearTimeout(timer);reject(Error('개인 음원 저장소를 사용할 수 없습니다.'));};
      request.onsuccess=()=>{clearTimeout(timer);const db=request.result;if(expired){db.close();return;}db.onversionchange=()=>{db.close();state.db=null;};resolve(db);};
    }).catch(error=>{state.db=null;throw error;});return state.db;
  }
  async function transaction(mode,action){
    const db=await openDb();return new Promise((resolve,reject)=>{
      const tx=db.transaction('sounds',mode),request=action(tx.objectStore('sounds'));
      tx.oncomplete=()=>resolve(request.result);tx.onerror=tx.onabort=()=>reject(Error('개인 음원을 저장하지 못했습니다.'));
    });
  }
  async function personal(owner){
    if(!window.indexedDB)return [];
    const result=await transaction('readonly',store=>store.index('owner').getAll(owner));
    return result.filter(item=>item.owner===owner && /^user-[a-f0-9-]{16,40}$/.test(item.id) && item.key===JSON.stringify([owner,item.id]) && item.blob instanceof Blob && item.blob.size>0 && item.blob.size<=MAX_FILE && typeof item.name==='string').slice(0,MAX_FILES);
  }
  async function refresh(owner,visible){
    if(el('notice-sound-settings'))el('notice-sound-settings').hidden=!visible;
    if(state.owner===owner)return;
    const scope=++state.scope;stop();resetBuffer();state.owner=owner;state.custom=[];state.busy=true;read();render();
    try{const custom=await personal(owner);if(scope!==state.scope)return;state.custom=custom;read();note('PC 배너와 함께 재생합니다. 미리듣기로 소리를 확인하세요.');}
    catch(_){if(scope===state.scope)note('개인 음원 저장소를 사용할 수 없습니다. 기본 알림음은 사용할 수 있습니다.');}
    finally{if(scope===state.scope){state.busy=false;render();}}
  }
  function context(){
    if(!audioSupported())throw Error('이 브라우저는 알림음 재생을 지원하지 않습니다.');
    if(!state.context || state.context.state==='closed'){const Audio=window.AudioContext || window.webkitAudioContext;state.context=new Audio();resetBuffer();}
    return state.context;
  }
  function unlock(){
    if(state.owner===null || !state.enabled)return;
    try{const ctx=context();if(ctx.state!=='running')ctx.resume().catch(()=>{});}catch(_){}
  }
  function synth(ctx,id){
    const rate=22050,buffer=ctx.createBuffer(1,Math.ceil(rate*.95),rate),samples=buffer.getChannelData(0);
    for(let i=0;i<samples.length;i++){
      const t=i/rate;let value=0;
      for(const [offset,freq] of (id==='piano'?[[0,659.25]]:[[0,id==='water'?1050:523.25],[.18,id==='water'?780:783.99]])){
        const age=t-offset;if(age<0)continue;
        const attack=Math.min(1,age/.006),decay=Math.exp(-age*(id==='piano'?5:id==='water'?11:9));
        const phase=2*Math.PI*freq*age;
        const wave=id==='water'?Math.sin(phase+5*(1-Math.exp(-age*20))):
          id==='piano'?Math.sin(phase)+.35*Math.sin(phase*2)+.16*Math.sin(phase*3):Math.sin(phase)+.25*Math.sin(phase*3.99)+.08*Math.sin(phase*10);
        value+=wave*attack*decay*.48;
      }
      samples[i]=value*Math.min(1,(.95-t)/.03);
    }return buffer;
  }
  async function loadBuffer(ctx,item){
    if(state.buffer && state.bufferId===item.id)return state.buffer;
    if(state.pending?.id===item.id)return state.pending.promise;
    const scope=state.scope;
    const promise=(async()=>{
      let buffer;
      if(!item.url && !item.blob)buffer=synth(ctx,item.id);
      else{
        let bytes;
        if(item.blob)bytes=await item.blob.arrayBuffer();
        else{
          const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
          try{const response=await fetch(item.url,{signal:controller.signal,cache:'force-cache'});if(!response.ok)throw Error('기본 음원을 불러오지 못했습니다. 프로그램을 최신 수정본으로 실행해주세요.');bytes=await response.arrayBuffer();if(bytes.byteLength>256*1024)throw Error('기본 음원 형식이 올바르지 않습니다.');}
          finally{clearTimeout(timer);}
        }
        buffer=await ctx.decodeAudioData(bytes);
        if(!Number.isFinite(buffer.duration) || buffer.duration<=0 || buffer.duration>31 || buffer.numberOfChannels>2)throw Error('30초 이하의 모노·스테레오 음원을 선택해주세요.');
      }
      if(scope===state.scope && item.id===state.selected){state.buffer=buffer;state.bufferId=item.id;}
      return buffer;
    })();state.pending={id:item.id,promise};
    try{return await promise;}finally{if(state.pending?.promise===promise)state.pending=null;}
  }
  async function play(preview=false){
    if(state.owner===null || (!preview && (!state.enabled || state.volume===0)))return;
    if(!preview && performance.now()-state.last<2000)return;
    if(!preview)state.last=performance.now();
    stop();const serial=state.serial,scope=state.scope,item=row();
    try{
      const ctx=context();
      if(ctx.state!=='running'){
        if(!preview){note('알림음을 사용하려면 페이지를 한 번 클릭하거나 미리듣기를 눌러주세요.');return;}
        await ctx.resume();
      }
      if(ctx.state!=='running')throw Error('페이지를 한 번 클릭한 뒤 다시 미리듣기를 눌러주세요.');
      const buffer=await loadBuffer(ctx,item);
      if(serial!==state.serial || scope!==state.scope || item.id!==state.selected)return;
      const source=ctx.createBufferSource(),gain=ctx.createGain(),duration=Math.min(8,buffer.duration),now=ctx.currentTime;
      const volume=state.volume/100*(item.level || 1); // Match the louder nuclear recording to the air-raid sample.
      source.buffer=buffer;source.loop=false;
      gain.gain.setValueAtTime(0,now);gain.gain.linearRampToValueAtTime(volume,now+.015);
      gain.gain.setValueAtTime(volume,now+Math.max(.015,duration-.1));gain.gain.linearRampToValueAtTime(0,now+duration);
      source.connect(gain);gain.connect(ctx.destination);state.playing=source;
      source.onended=()=>{source.disconnect();gain.disconnect();if(state.playing===source)state.playing=null;};
      source.start(now,0,duration);
      if(preview)note('미리듣기 중 · '+item.name+(state.volume===0?' · 음량 0%':''));
    }catch(error){console.warn('[BossTimer] web_notice_sound_failed',error);note(error.message || '알림음을 재생하지 못했습니다.');if(preview)toast(error.message || '알림음 재생 실패');}
  }
  function validateFile(file){
    if(!file || !/\.(mp3|wav|ogg|m4a|aac|webm)$/i.test(file.name) || file.size<=0 || file.size>MAX_FILE)throw Error('4MB 이하의 MP3·WAV·OGG·M4A·AAC·WebM 음원을 선택해주세요.');
    return new Promise((resolve,reject)=>{
      const audio=new Audio(),url=URL.createObjectURL(file);let done=false;
      const finish=error=>{if(done)return;done=true;clearTimeout(timer);audio.onloadedmetadata=audio.onerror=null;audio.removeAttribute('src');audio.load();URL.revokeObjectURL(url);error?reject(error):resolve();};
      const timer=setTimeout(()=>finish(Error('음원 정보를 확인하지 못했습니다. 다른 파일을 선택해주세요.')),8000);
      audio.preload='metadata';audio.onloadedmetadata=()=>finish(!Number.isFinite(audio.duration) || audio.duration<=0 || audio.duration>30?Error('30초 이하의 알림음을 선택해주세요.'):null);
      audio.onerror=()=>finish(Error('이 브라우저에서 재생할 수 없는 음원입니다.'));audio.src=url;
    });
  }
  async function add(file){
    if(!file || state.busy)return;
    const owner=state.owner,scope=state.scope;state.busy=true;render();
    try{
      if(state.custom.length>=MAX_FILES || state.custom.reduce((n,r)=>n+r.blob.size,0)+file.size>MAX_TOTAL)throw Error('개인 음원은 10개·총 20MB까지 저장할 수 있습니다.');
      await validateFile(file);if(scope!==state.scope)return;
      const id='user-'+Array.from(crypto.getRandomValues(new Uint8Array(12))).map(n=>n.toString(16).padStart(2,'0')).join('');
      const item={id,key:JSON.stringify([owner,id]),owner,name:file.name.replace(/\.[^.]+$/,'').replace(/[\u0000-\u001f\u007f]/g,'').slice(0,60) || '개인 알림음',blob:file};
      await transaction('readwrite',store=>store.put(item));if(scope!==state.scope)return;
      stop();resetBuffer();state.custom.push(item);state.selected=id;save();signal();note('개인 음원을 저장하고 선택했습니다. 미리듣기로 확인하세요.');
    }catch(error){if(scope===state.scope)note(error.message || '개인 음원을 추가하지 못했습니다.');}
    finally{if(scope===state.scope){state.busy=false;el('notice-sound-file').value='';render();}}
  }
  async function remove(){
    const item=row(),scope=state.scope;if(!item.blob || state.busy)return;
    state.busy=true;render();
    try{await transaction('readwrite',store=>store.delete(item.key));if(scope!==state.scope)return;stop();resetBuffer();state.custom=state.custom.filter(r=>r.id!==item.id);state.selected='water';save();signal();note('개인 음원을 삭제했습니다. 기본 물방울을 선택했습니다.');}
    catch(error){if(scope===state.scope)note(error.message || '개인 음원을 삭제하지 못했습니다.');}
    finally{if(scope===state.scope){state.busy=false;render();}}
  }
  function signal(){try{localStorage.setItem(signalKey(),String(Date.now())+Math.random());}catch(_){}}
  el('notice-sound-select')?.addEventListener('change',()=>{stop();resetBuffer();state.selected=rows().some(r=>r.id===el('notice-sound-select').value)?el('notice-sound-select').value:'water';save();render();unlock();note('선택한 알림음을 적용했습니다.');});
  el('notice-sound-enabled')?.addEventListener('change',()=>{state.enabled=el('notice-sound-enabled').checked;stop();save();unlock();note(state.enabled?'알림음을 켰습니다.':'알림음을 껐습니다. 배너는 계속 표시됩니다.');});
  el('notice-sound-volume')?.addEventListener('input',()=>{state.volume=Math.max(0,Math.min(100,Number(el('notice-sound-volume').value)));stop();el('notice-sound-volume-value').textContent=state.volume+'%';save();});
  el('notice-sound-preview')?.addEventListener('click',()=>play(true));
  el('notice-sound-add')?.addEventListener('click',()=>el('notice-sound-file').click());
  el('notice-sound-file')?.addEventListener('change',()=>add(el('notice-sound-file').files[0]));
  el('notice-sound-delete')?.addEventListener('click',remove);
  document.addEventListener('pointerdown',unlock,{capture:true,passive:true});document.addEventListener('keydown',unlock,{capture:true});
  window.addEventListener('pagehide',stop);
  window.addEventListener('storage',async event=>{
    if(state.owner===null || state.busy)return;
    if(event.key===key()){stop();resetBuffer();read();render();}
    else if(event.key===signalKey()){
      const scope=state.scope;try{const custom=await personal(state.owner);if(scope===state.scope){stop();resetBuffer();state.custom=custom;read();render();}}catch(_){}
    }
  });
  return {refresh,notify:()=>play(false),stop};
})();

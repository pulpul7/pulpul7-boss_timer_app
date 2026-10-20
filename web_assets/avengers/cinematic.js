/* One native video decoder, one final-frame bitmap. No animation/frame loop. */
(()=>{
'use strict';
// Re-evaluating the runtime must not leave a second, unmanaged decoder alive.
if(globalThis.WebIronmanCinematic)return;
let enabled=false,row=null,key='',anchor=0,anchorClock=0,library=null,request=null,catalogueLoading=false,canSound=()=>false,audioBlocked=false;
let hero,video,still,stage,arrival,observer,visible=true,timer=0,due=0,loaded='',clip=null,holding=false,playing=false,serial=0;
let panelTimer=0,compactDeadline=0,restoreDeadline=0;
let transportTimer=0,transportActivity=0;
let frameCallback=0,frameTimer=0,framePresented=false,unmuteUntil=0;
let lastPresented=0,manualCursor=-1;
let foreignAudio=false,audioChannel=null,audioClaim=null,audioClaimSerial=-1;
const audioIdentity=globalThis.crypto?.randomUUID?.()||String(Date.now())+Math.random();
try{audioChannel=new BroadcastChannel('BossTimer.cinematic.audio.v1');}catch(_){}
const newerClaim=(a,b)=>!b||a.at>b.at||(a.at===b.at&&a.id>b.id);
if(audioChannel)audioChannel.onmessage=event=>{
 const claim=event.data;
 if(claim?.type!=='claim'||typeof claim.id!=='string'||!Number.isFinite(claim.at)||claim.id===audioIdentity||!newerClaim(claim,audioClaim))return;
 audioClaim=claim;foreignAudio=true;if(video&&!video.muted)video.muted=true;
};
let scheduledRow=null,preview=null,previewSerial=0,running=false,busyStart=0,groupVersion=null;
let groupedRows=[],leaders=new Set(),byGroup=new Map(),holdGroup=null,holdOwner='';
let latestRows=[],latestVersion;
let currentIndex=-1,transport,statusLine,playToggle,drawer,expand,paused=false,pausedAt=0,localURL='',controlsReady=false;
let transportDisabled=null,transportPlaying=null;
let automaticCursor=-1;
const automaticMovies=new Map();
const rowKey=r=>r?String(r.id)+'|'+r.at:'';
const completed=new Set(),skipped=new Set(),failed=new Set();
const now=()=>anchor+performance.now()-anchorClock;
// BEGIN CINEMATIC BOSS PREFERENCES
const selectionPrefs=(()=>{
 const defaults=['absolute','major','world','floor:10','lowest:gullveig','lowest:gang','lowest:sner'];
 const fixed=[['absolute','절대자'],['major','주요보스'],['world','월드보스'],['valhalla','발할라대전'],
  ['floor:4','4층'],['floor:7','7층'],['floor:10','10층'],['lowest:gullveig','최하층굴베'],['lowest:gang','최하층강글'],['lowest:sner','최하층스네르']];
 let owner=null,catalog=null,byId=new Map(),options=[],saved=null,draft=null,major=()=>false;
 let panel,list,status,save,undo,allInput,inputs=new Map();
 const storageKey=()=> 'BossTimer.cinematic.bosses.v1:'+owner;
 const copy=p=>p?{all:p.all,selected:[...p.selected]}:null;
 const signature=p=>JSON.stringify(p?{all:p.all,selected:[...p.selected].sort()}:null);
 const dirty=()=>signature(saved)!==signature(draft);
 function read(){
  saved=null;
  try{
   const raw=localStorage.getItem(storageKey());
   if(raw&&raw.length<=16000){const p=JSON.parse(raw);
    if(p?.schema===1&&typeof p.all==='boolean'&&Array.isArray(p.selected)&&p.selected.length<=200&&p.selected.every(v=>typeof v==='string'&&/^(?:chapter:\d{1,3}|absolute|major|world|valhalla|floor:\d{1,3}|lowest:(?:gullveig|gang|sner))$/.test(v)))saved={all:p.all,selected:[...new Set(p.selected)]};
   }
  }catch(_){}
  draft=copy(saved);
 }
 function defaultKeys(){return [...defaults,...options.filter(([id])=>id.startsWith('chapter:')&&Number(id.slice(8))>=4).map(([id])=>id)];}
 function chosen(p){return new Set(p?.selected||defaultKeys());}
 function chapter(b){return Number(String(b.group||'').match(/(\d+)\s*(?:챕|장|chapter)/i)?.[1]||0);}
 function categories(row){
  const b=byId.get(row.catalog_id||row.id)||row,n=String(b.name||row.name||'').replace(/\s|★/g,''),keys=[];
  const ch=chapter(b);if(ch)keys.push('chapter:'+ch);
  if(b.absolute===true||b.group==='절대자')keys.push('absolute');
  if(ch>=7||typeof b.name==='string'&&(b.kind==='fixed'||/^(길던|길드던전)/.test(n))&&major(b))keys.push('major');
  if(/^월드보스/.test(n))keys.push('world');
  if(/^발할라(?:대전)?/.test(n))keys.push('valhalla');
  const floor=n.match(/^(\d+)층(?:$|\()/);if(floor)keys.push('floor:'+Number(floor[1]));
  for(const [prefix,id] of [['최하층굴베','gullveig'],['최하층강글','gang'],['최하층스네르','sner']])if(n.startsWith(prefix))keys.push('lowest:'+id);
  return keys;
 }
 function allows(row){
  if(!row||!['boss','fixed','invasion'].includes(row.kind))return false;
  if(saved?.all)return true;
  const selected=chosen(saved);return categories(row).some(id=>selected.has(id));
 }
 function paint(){
  if(!panel)return;
  panel.hidden=!enabled;const selected=chosen(draft),all=draft?.all===true;
  let count=0;for(const [id,input] of inputs){input.checked=all||selected.has(id);if(input.checked)++count;}
  allInput.checked=all||count===inputs.size;allInput.indeterminate=!all&&count>0&&count<inputs.size;
  save.disabled=undo.disabled=!dirty();
 }
 function persist(){
  if(!dirty())return;
  try{localStorage.setItem(storageKey(),JSON.stringify({schema:1,...draft}));saved=copy(draft);status.textContent='시네마틱 설정을 저장했습니다.';paint();selectionChanged();}
  catch(_){status.textContent='저장하지 못했습니다. 브라우저 저장 권한을 확인해주세요.';}
 }
 function build(){
  if(!enabled||!document.getElementById('cinematic-settings-host'))return;
  if(!panel){
   panel=document.createElement('section');panel.id='cinematic-boss-settings';panel.className='cinematic-boss-settings';
   const title=document.createElement('h3');title.textContent='시네마틱 재생 보스';panel.setAttribute('aria-labelledby','cinematic-boss-title');title.id='cinematic-boss-title';
   const help=document.createElement('p');help.textContent='체크한 분류 중 하나에 해당하는 보스만 자동 재생합니다. 주요보스는 7챕 이상과 주요 고정보스·길던(공성전 등)을 포함합니다. 연속 보스 생략 규칙은 유지됩니다.';
   list=document.createElement('div');list.className='cinematic-boss-options';
   const actions=document.createElement('div');actions.className='cinematic-boss-actions';
   save=document.createElement('button');save.type='button';save.textContent='시네마틱 설정 저장';save.id='cinematic-boss-save';save.addEventListener('click',persist);
   undo=document.createElement('button');undo.type='button';undo.textContent='되돌리기';undo.id='cinematic-boss-undo';undo.addEventListener('click',()=>{draft=copy(saved);status.textContent='저장된 시네마틱 설정으로 되돌렸습니다.';paint();});
   status=document.createElement('p');status.id='cinematic-boss-status';status.setAttribute('role','status');status.textContent='이 기기·브라우저·닉네임의 개인 설정입니다. 체크 후 시네마틱 설정 저장을 눌러주세요.';
   actions.append(undo,save);panel.append(title,help,list,actions,status);document.getElementById('cinematic-settings-host').append(panel);
  }
  list.replaceChildren();inputs=new Map();
  for(const [id,text] of [['all','전부'],...options]){
   const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.dataset.cinematicChoice=id;
   label.append(input,document.createTextNode(text));list.append(label);if(id==='all')allInput=input;else inputs.set(id,input);
   input.addEventListener('change',()=>{
    if(id==='all')draft={all:input.checked,selected:[]};
    else{const selected=draft?.all?new Set(options.map(([key])=>key)):chosen(draft);if(input.checked)selected.add(id);else selected.delete(id);draft={all:false,selected:[...selected]};}
    status.textContent='변경사항 있음 · 시네마틱 설정 저장을 눌러주세요.';paint();
   });
  }
  paint();
 }
 function refresh(nextOwner,rows,isMajor){
  const changed=owner!==nextOwner,renew=catalog!==rows;
  if(typeof isMajor==='function')major=isMajor;
  if(changed){owner=nextOwner;read();if(status)status.textContent='이 기기·브라우저·닉네임의 개인 설정입니다.';}
  if(renew){
   catalog=rows;byId=new Map();for(const b of rows){byId.set(b.id,b);for(const id of b.legacy_ids||[])byId.set(id,b);}
   const chapters=[...new Set(rows.map(chapter).filter(Boolean))].sort((a,b)=>a-b);
   options=[...chapters.map(n=>['chapter:'+n,n+'챕']),...fixed];
  }
  if(changed||renew||enabled&&!panel)build();
  if(changed||renew)selectionChanged();
 }
 window.addEventListener('storage',event=>{if(owner!==null&&(event.key===storageKey()||event.key===null)&&!dirty()){read();paint();selectionChanged();}});
 return {refresh,allows,dirty,visibility(){if(enabled&&!panel)build();else paint();}};
})();
// END CINEMATIC BOSS PREFERENCES
function selectionChanged(){
 if(!enabled)return;
 if(!preview&&row&&!selectionPrefs.allows(row)){cancel();if(running||loaded||holding||hero?.dataset.cinematic)release(false);}
 pump();
}
// Disk-backed browser cache; only the currently playing clip gets a blob URL.
const localMedia=(()=>{
 const saved=new Set(),pending=new Map();let storagePromise,active=false,worker=null,revision=0,scanned='';
 async function storage(){
  if(!storagePromise)storagePromise=(async()=>{
   try{
    const caches=globalThis.caches;if(!caches)return null;
    const current='BossTimer.cinematic.v2';
    for(const name of await caches.keys())if(/^BossTimer\.cinematic\.v\d+$/.test(name)&&name!==current)await caches.delete(name);
    return await caches.open(current);
   }catch(_){return null;}
  })();
  return storagePromise;
 }
 async function blob(url){
  if(pending.has(url))return pending.get(url).promise;
  const record={controller:new AbortController()};
  record.promise=(async()=>{
   const store=await storage();
   try{const cached=await store?.match(url);if(cached?.status===200){const content=await cached.blob();if(content.size){saved.add(url);paintTransport();return content;}}}catch(_){}
   saved.delete(url);
   const response=await fetch(url,{credentials:'same-origin',cache:store?'reload':'force-cache',signal:record.controller.signal});
   if(response.status!==200)throw Error('cinematic download');
   const content=await response.blob();if(!content.size)throw Error('empty cinematic');
   try{await store?.put(url,new Response(content,{headers:response.headers}));}catch(_){/* Browser HTTP cache remains a fallback. */}
   saved.add(url);paintTransport();return content;
  })().finally(()=>{if(pending.get(url)===record)pending.delete(url);});
  pending.set(url,record);return record.promise;
 }
 function warm(){
  if(!active||worker||document.hidden||!library?.length)return;
  const pass=revision;
  worker=(async()=>{
   const store=await storage();
   if(pass!==revision)return;
   const signature=library.map(c=>c.url).join('|');
   if(scanned!==signature){
    try{
     const allowed=new Map(library.map(c=>[new URL(c.url,location.href).href,c.url])),keys=await store?.keys()||[];
     if(pass!==revision)return;
     saved.clear();
     for(const request of keys){const url=allowed.get(request.url);if(url)saved.add(url);else await store.delete(request);}
     scanned=signature;paintTransport();
    }catch(_){/* Storage restrictions retain sequential HTTP-cache fallback. */}
   }
   for(const c of library){
    if(!active||pass!==revision||document.hidden)break;
    if(saved.has(c.url))continue;
    try{await blob(c.url);}catch(error){if(error.name==='AbortError')break;}
    // Yield between files; preloading never creates a video decoder.
    await new Promise(resolve=>setTimeout(resolve,80));
   }
  })().finally(()=>{worker=null;if(active&&pass!==revision)warm();});
 }
 function start(){active=true;warm();}
 function stop(){active=false;++revision;for(const r of pending.values())r.controller.abort();pending.clear();}
 return {blob,start,stop,warm,ready:()=>library?.filter(c=>saved.has(c.url)).length||0};
})();
const cancel=()=>{clearTimeout(timer);timer=0;due=0;};
function arm(at){
 if(timer&&due===at)return;
 cancel();due=at;timer=setTimeout(()=>{timer=0;due=0;pump();},Math.max(10,Math.min(at-now(),2147483000)));
}
function clearCompaction(){clearTimeout(panelTimer);panelTimer=0;compactDeadline=restoreDeadline=0;}
function restorePanel(){clearCompaction();if(!running&&!preview&&hero?.dataset.cinematic==='still')clearStill();}
function compactPanel(){
 const at=compactDeadline||performance.now();clearCompaction();
 if(hero)hero.dataset.cinematicSize='compact';
 if(hero?.dataset.cinematic==='still'&&!still.hidden){
  restoreDeadline=at+5000;
  if(performance.now()>=restoreDeadline)restorePanel();
  else panelTimer=setTimeout(settlePanel,Math.max(1,Math.ceil(restoreDeadline-performance.now())));
 }
}
function settlePanel(){
 if(!compactDeadline&&!restoreDeadline)return;
 const at=performance.now();
 if(restoreDeadline&&at>=restoreDeadline)restorePanel();
 else if(compactDeadline&&at>=compactDeadline)compactPanel();
}
function compactAfterEnd(){clearCompaction();compactDeadline=performance.now()+5000;panelTimer=setTimeout(settlePanel,5000);}
function setTransportOpen(open){
 if(!drawer||!expand)return;
 open=Boolean(open&&enabled);
 const restoreFocus=!open&&drawer.contains(document.activeElement);
 clearTimeout(transportTimer);transportTimer=0;
 drawer.hidden=!open;expand.setAttribute('aria-expanded',String(open));
 expand.setAttribute('aria-label',open?'시네마틱 조작부 접기':'시네마틱 조작부 펼치기');
 if(open){transportActivity=performance.now();transportTimer=setTimeout(checkTransportIdle,3000);}
 else if(restoreFocus)expand.focus({preventScroll:true});
}
function checkTransportIdle(){
 transportTimer=0;
 if(!drawer||drawer.hidden)return;
 const remaining=3000-(performance.now()-transportActivity);
 if(remaining>0)transportTimer=setTimeout(checkTransportIdle,remaining);
 else setTransportOpen(false);
}
function recordTransportActivity(){
 if(enabled&&!document.hidden&&drawer&&!drawer.hidden&&!transport.hidden)transportActivity=performance.now();
}
function automaticPlan(){
 if(!scheduledRow||!library?.length||!selectionPrefs.allows(scheduledRow))return null;
 const id=rowKey(scheduledRow);
 if(completed.has(id)||failed.has(id)||(skipped.has(id)&&!(preview&&paused))||(groupedRows.length&&!leaders.has(id)))return null;
 const movie=choose(id),end=scheduledRow.at-5000;
 return {row:scheduledRow,key:id,movie,start:end-movie.duration*1000,end};
}
function canPresent(){
 return enabled&&controlsReady&&!document.hidden&&visible&&hero&&!hero.closest('[hidden]')&&document.documentElement.getAttribute('data-theme')==='avengers';
}
function clearFrameCheck(){
 if(frameCallback)video?.cancelVideoFrameCallback?.(frameCallback);
 frameCallback=0;clearTimeout(frameTimer);frameTimer=0;
}
function currentPlayback(){
 if(!running||!clip||loaded!==key||!localURL||video?.currentSrc!==localURL)return false;
 const t=now(),end=row?.at-5000;
 return Number.isFinite(end)&&t>=end-clip.duration*1000&&t<end;
}
function videoVisible(){
 if(!canPresent()||!video?.isConnected||!stage?.isConnected||video.hidden||!still.hidden||!video.getClientRects().length)return false;
 const v=getComputedStyle(video),s=getComputedStyle(stage),h=getComputedStyle(hero);
 return v.display!=='none'&&v.visibility==='visible'&&Number(v.opacity)>0&&s.display!=='none'&&s.visibility==='visible'&&Number(s.opacity)>0&&Number(h.opacity)>0;
}
function claimAudio(){
 if(!audioChannel||(audioClaimSerial===serial&&audioClaim?.id===audioIdentity))return;
 audioClaim={type:'claim',id:audioIdentity,at:Math.max(Date.now(),(audioClaim?.at||0)+1)};
 audioClaimSerial=serial;audioChannel.postMessage(audioClaim);
}
function clearStill(){
 clearCompaction();
 if(!still)return;
 still.hidden=true;still.width=still.height=0;holdGroup=null;holdOwner='';
 if(hero?.dataset.cinematic==='still'){hero.dataset.cinematicSize='compact';delete hero.dataset.cinematic;}
}
function failPlayback(){
 failed.add(key);cancel();release(false);preview=null;paused=false;pausedAt=0;
 row=scheduledRow;key=rowKey(row);paintButton();
}
function verifyPresentedFrame(refresh=false){
 if((framePresented&&!refresh)||frameCallback)return;
 if(typeof video.requestVideoFrameCallback!=='function'){framePresented=true;return;}
 const token=serial;
 frameCallback=video.requestVideoFrameCallback(()=>{
  if(token!==serial)return;
  frameCallback=0;
  if(!videoVisible()||!currentPlayback()||!video.videoWidth||!video.videoHeight){pauseDecoder();return;}
  framePresented=true;lastPresented=performance.now();clearFrameCheck();audio();
 });
 frameTimer=setTimeout(()=>{frameTimer=0;if(token===serial&&canPresent()&&!paused&&!holding)failPlayback();},2000);
}
function release(keepStill=true){
 if(!video)return;
 pauseDecoder();
 clearFrameCheck();framePresented=false;unmuteUntil=0;video.muted=true;
 ++serial;holding=false;playing=false;running=false;loaded='';
 video.onloadedmetadata=video.onloadeddata=video.onseeked=video.onended=video.onerror=null;
 video.hidden=true;if(video.hasAttribute('src')){video.removeAttribute('src');video.load();}
 if(localURL){URL.revokeObjectURL(localURL);localURL='';}
 if(!keepStill)clearStill();
 if(keepStill&&still.width){still.hidden=false;hero.dataset.cinematic='still';}else delete hero.dataset.cinematic;
 paintTransport();
}
function mount(){
 if(video)return true;
 hero=document.getElementById('next-name')?.closest('.hero');
 if(!hero)return false;
 video=document.createElement('video');still=document.createElement('canvas');
 stage=document.createElement('div');stage.className='ironman-cinematic-stage';stage.setAttribute('aria-hidden','true');hero.append(stage);
 arrival=document.createElement('span');arrival.className='ironman-cinematic-arrival';arrival.textContent='곧 보스가 출연 합니다.';arrival.setAttribute('aria-hidden','true');hero.append(arrival);
 still.width=still.height=0;
 video.id='ironman-cinematic-video';still.id='ironman-cinematic-still';
 for(const n of [video,still]){n.className='ironman-cinematic';n.hidden=true;n.setAttribute('aria-hidden','true');stage.append(n);}
 video.muted=true;video.defaultMuted=true;video.playsInline=true;
 video.setAttribute('muted','');video.setAttribute('playsinline','');video.preload='auto';
 video.disablePictureInPicture=true;video.controls=false;video.loop=false;
 video.addEventListener('play',()=>{if(!videoVisible()||paused||!currentPlayback()){pauseDecoder();return;}busyStart=now();if(!preview){manualCursor=-1;advanceAutomatic(key,clip);discardOverlaps(busyStart,busyStart);}setTransportOpen(true);});
 video.addEventListener('pause',()=>{
  if(performance.now()<unmuteUntil&&framePresented&&!video.muted&&!video.ended&&!video.seeking&&running&&!paused&&!holding&&canPresent()&&!video.hidden){
   audioBlocked=true;video.muted=true;pump();
  }
 });
 video.addEventListener('waiting',()=>{video.muted=true;clearFrameCheck();framePresented=false;});
 video.addEventListener('playing',()=>{if(canPresent()&&!video.hidden&&!paused&&!holding){pump();if(!video.hidden&&!holding){verifyPresentedFrame();audio();}}});
 video.addEventListener('timeupdate',()=>{
  if(!running||paused||holding||video.paused)return;
  if(!currentPlayback()){video.muted=true;pump();return;}
  if(performance.now()-lastPresented<1000)return;
  if(!videoVisible()){pauseDecoder();return;}
  verifyPresentedFrame(true);
 });
 transport=document.createElement('section');transport.id='ironman-media-dock';transport.className='ironman-transport';transport.hidden=true;transport.setAttribute('aria-label','시네마틱 재생');
 // The page CSS is an immutable snapshot; keep runtime UI and its CSS together.
 const skin=document.createElement('link');skin.rel='stylesheet';skin.href='/cinematic/player.css';
 skin.addEventListener('load',()=>{controlsReady=true;paintTransport();if(!video.paused&&!video.hidden)setTransportOpen(true);pump();},{once:true});
 document.head.append(skin);
 expand=document.createElement('button');expand.type='button';expand.id='cinematic-expand';expand.setAttribute('aria-label','시네마틱 조작부 펼치기');expand.setAttribute('aria-expanded','false');expand.setAttribute('aria-controls','cinematic-drawer');
 const tab=document.createElementNS('http://www.w3.org/2000/svg','svg');tab.setAttribute('viewBox','0 0 72 26');tab.setAttribute('aria-hidden','true');
 tab.innerHTML='<defs><linearGradient id="cinematic-metal" x2="0" y2="1"><stop stop-color="#fff0b4"/><stop offset=".3" stop-color="#986329"/><stop offset=".55" stop-color="#e9c67a"/><stop offset="1" stop-color="#674014"/></linearGradient><linearGradient id="cinematic-red" x2="0" y2="1"><stop stop-color="#681408"/><stop offset="1" stop-color="#160906"/></linearGradient></defs><path d="M1 1H71L60 12H49L36 25 23 12H12Z" fill="#100d09" stroke="url(#cinematic-metal)" stroke-width="1.5"/><path d="M6 3H66L58 10H47L36 21 25 10H14Z" fill="url(#cinematic-red)" stroke="#d2a45d" stroke-width=".6"/><path d="M28 9H44L36 17Z" fill="#6dd8f3" stroke="#e6fbff" stroke-width=".6"/><path d="M11 5H23M49 5H61" stroke="#ecbd68" stroke-width="1"/><g fill="#211a12" stroke="#d4b46d" stroke-width=".5"><circle cx="15" cy="7" r="1.4"/><circle cx="57" cy="7" r="1.4"/></g>';
 expand.append(tab);drawer=document.createElement('div');drawer.id='cinematic-drawer';drawer.className='ironman-transport-drawer';drawer.hidden=true;
 expand.addEventListener('click',()=>setTransportOpen(drawer.hidden));
 const keys=[['cinematic-previous','이전','M8 5V19M19 5 10 12 19 19Z'],['cinematic-toggle','재생','M8 5 19 12 8 19Z'],['cinematic-next','다음','M16 5V19M5 5 14 12 5 19Z']];
 const controls=document.createElement('div');controls.className='ironman-transport-buttons';
 for(const [id,label,path] of keys){
  const b=document.createElement('button');b.type='button';b.id=id;b.title=label+' 영상';b.setAttribute('aria-label',label+' 영상');
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const shape=document.createElementNS(svg.namespaceURI,'path');shape.setAttribute('d',path);svg.append(shape);
  b.append(svg);controls.append(b);
 }
 statusLine=document.createElement('p');statusLine.className='ironman-transport-status';statusLine.id='cinematic-status';statusLine.setAttribute('role','status');
 drawer.append(controls);transport.append(expand,drawer,statusLine);hero.after(transport);
 playToggle=document.getElementById('cinematic-toggle');
 document.getElementById('cinematic-previous').addEventListener('click',()=>previewStep(-1));
 document.getElementById('cinematic-next').addEventListener('click',()=>previewStep(1));
 playToggle.addEventListener('click',togglePause);
 paintTransport();
 if(typeof IntersectionObserver==='function'){
  observer=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;hero.dataset.cinematicVisible=String(visible);pump();});observer.observe(hero);
 }
 return true;
}
function catalogue(refresh=false){
 if(request&&(!refresh||catalogueLoading))return;
 catalogueLoading=true;
 request=fetch('/cinematic/catalog.json',{credentials:'same-origin'}).then(r=>{
  if(!r.ok)throw Error('cinematic catalogue');return r.json();
 }).then(v=>{
  const previous=library?.map(c=>c.url).join('|');
  const nextURL=library?.[automaticCursor]?.url;
  library=(v.clips||[]).filter(c=>/^\/cinematic\/[0-9a-f]{64}\.mp4$/.test(c.url)&&Number.isFinite(c.duration)&&c.duration>0&&c.duration<=600&&Number.isFinite(c.fps)&&c.fps>0);
  if(nextURL){const next=library.findIndex(c=>c.url===nextURL);automaticCursor=next>=0?next:automaticCursor%Math.max(1,library.length);}
  if(previous!==library.map(c=>c.url).join('|'))failed.clear();
  paintButton();if(enabled)localMedia.start();
 }).catch(()=>{library=library||[];paintButton();}).finally(()=>{catalogueLoading=false;if(enabled)pump();});
}
function paintButton(){
 const button=document.getElementById('cinematic-play');if(!button)return;
 button.hidden=true;button.disabled=!library?.length;
 const next=(currentIndex+1)%library?.length,c=library?.[next];
 button.title=c?`${next+1}/${library.length} · ${c.name||'시네마틱'}`:library?'시네마틱 폴더에 재생 가능한 MP4가 없습니다.':'시네마틱 영상을 준비하는 중';paintTransport();
}
function paintTransport(){
 if(!transport)return;
 const hidden=!controlsReady||!enabled||hero.hidden;
 if(transport.hidden!==hidden)transport.hidden=hidden;
 if(transport.hidden&&drawer&&!drawer.hidden)setTransportOpen(false);
 const disabled=!library?.length;
 if(transportDisabled!==disabled){transportDisabled=disabled;for(const b of transport.querySelectorAll('button'))b.disabled=disabled;}
 const active=Boolean((running||preview)&&!paused),label=active?'일시정지':'재생';
 if(playToggle&&transportPlaying!==active){transportPlaying=active;playToggle.setAttribute('aria-label',label+' 영상');playToggle.title=label+' 영상';playToggle.setAttribute('aria-pressed',String(active));
  playToggle.querySelector('path').setAttribute('d',active?'M6 5H10V19H6ZM14 5H18V19H14Z':'M8 5 19 12 8 19Z');}
 const total=library?.length||0,ready=localMedia.ready(),name=library?.[currentIndex]?.name;
 const message=(total?`영상 준비 ${ready}/${total}`:'영상 목록 준비 중')+(name?' · '+name:'');
 if(statusLine.textContent!==message)statusLine.textContent=message;
}
function togglePause(){
 if(paused){
  if(preview)row.at+=Math.max(0,now()-pausedAt);
  paused=false;pausedAt=0;busyStart=now();audio(true);paintTransport();pump();
 }else if(running||preview){
  if(running)discardOverlaps(busyStart||now(),now());
  paused=true;pausedAt=now();video.pause();
  if(!video.hidden)hero.dataset.cinematic='paused';
  if(preview)pump();else arm(row.at-5000);
  paintTransport();
 }else previewStep(0);
}
function choose(forKey=key){
 if(automaticMovies.has(forKey))return automaticMovies.get(forKey);
 if(automaticCursor<0)automaticCursor=Math.floor(Math.random()*library.length);
 return library[automaticCursor];
}
function advanceAutomatic(id,movie){
 if(automaticMovies.has(id))return;
 automaticMovies.set(id,movie);if(automaticMovies.size>64)automaticMovies.delete(automaticMovies.keys().next().value);
 const index=library.findIndex(c=>c.url===movie.url);
 automaticCursor=((index>=0?index:automaticCursor)+1)%library.length;
}
function panelIndex(step=0){
 const wrap=index=>(index%library.length+library.length)%library.length;
 if((preview||running)&&currentIndex>=0)return wrap(currentIndex+step);
 if(manualCursor>=0)return wrap(manualCursor+step);
 const movie=automaticPlan()?.movie||choose('panel-start'),base=Math.max(0,library.indexOf(movie));
 return wrap(base+(step<0?-1:0));
}
function groups(rows,version){
 const stamp=version??rows;if(stamp===groupVersion)return;
 groupVersion=stamp;leaders=new Set();byGroup=new Map();groupedRows=[...rows].filter(r=>['boss','fixed','invasion'].includes(r.kind)&&Number.isFinite(r.at)).sort((a,b)=>a.at-b.at);
 let previous=-Infinity,group;
 for(const r of groupedRows){const id=rowKey(r);if(r.at-previous>150000){leaders.add(id);group={members:new Set(),end:r.at};}group.members.add(id);group.end=r.at;byGroup.set(id,group);previous=r.at;}
 if(holdOwner&&byGroup.has(holdOwner))holdGroup=byGroup.get(holdOwner);
}
function expireStill(at,next){
 if(!holdGroup||running||preview)return;
 if(at<holdGroup.end||holdGroup.members.has(rowKey(next)))return;
 clearStill();
}
function remember(id=key,cache=completed){
 cache.add(id);if(cache.size>64)cache.delete(cache.values().next().value);
}
function discardOverlaps(start,end){
 if(!library?.length)return;
 for(const r of groupedRows){const id=rowKey(r);if(id===key||!leaders.has(id)||!selectionPrefs.allows(r))continue;
  const finish=r.at-5000,begin=finish-choose(id).duration*1000;
  if(begin<=end&&finish>=start)remember(id,skipped);
 }
}
function pauseDecoder(){
 if(!video)return;
 if(running&&!paused&&!video.paused&&busyStart)discardOverlaps(busyStart,now());
 busyStart=0;
 if(!video.muted)video.muted=true;
 if(!video.paused)video.pause();
 clearFrameCheck();framePresented=false;
}
function audio(gesture=false){
 if(!video)return;
 if(gesture){audioBlocked=false;foreignAudio=false;}
 const target=preview?scheduledRow:row;
 const allowed=target&&!foreignAudio&&currentPlayback()&&framePresented&&!video.paused&&!video.seeking&&!paused&&!holding&&hero.dataset.cinematic==='playing'&&videoVisible()&&canSound(target)&&document.getElementById('notice-sound-enabled')?.checked;
 const volume=Math.max(0,Math.min(1,Number(document.getElementById('notice-sound-volume')?.value??35)/100));
 const muted=!allowed||!volume||audioBlocked;
 if(!muted)claimAudio();
 if(video.muted&&!muted)unmuteUntil=performance.now()+1000;
 if(video.volume!==volume)video.volume=volume;
 if(video.muted!==muted)video.muted=muted;
}
function capture(token){
 if(token!==serial||!holding||video.readyState<2)return;
 if(!video.videoWidth||!video.videoHeight){failPlayback();return;}
 try{
  if(running&&!paused)discardOverlaps(busyStart||now(),now());
  const scale=Math.min(1,960/video.videoWidth);
  still.width=Math.max(1,Math.round(video.videoWidth*scale));still.height=Math.max(1,Math.round(video.videoHeight*scale));
  still.getContext('2d',{alpha:false}).drawImage(video,0,0,still.width,still.height);
  holdOwner=preview?rowKey(scheduledRow):key;
  hero.dataset.cinematicSize='expanded';
  holdGroup=byGroup.get(holdOwner)||{members:new Set([holdOwner]),end:(preview?scheduledRow:row)?.at??now()};
  still.hidden=false;hero.dataset.cinematic='still';remember();cancel();release();
  preview=null;paused=false;pausedAt=0;row=scheduledRow;key=rowKey(row);compactAfterEnd();paintButton();
 }catch(_){failPlayback();}
}
function freeze(){
 cancel();video.pause();
 if(holding||video.readyState<1||!Number.isFinite(video.duration))return;
 holding=true;const token=serial,target=Math.max(0,Math.min(video.duration,clip.duration)-.5/clip.fps);
 // Seek into the final frame, never to duration (which can display black).
 if(Math.abs(video.currentTime-target)<.005&&video.readyState>=2)capture(token);
 else video.currentTime=target;
}
function prepare(){
 if(loaded===key)return;
 release(false);loaded=key;foreignAudio=false;const token=serial;
 const current=()=>token===serial&&video.currentSrc===localURL;
 video.onloadedmetadata=()=>{if(current())pump();};
 video.onloadeddata=()=>{if(current()){if(holding&&!video.seeking)capture(token);else pump();}};
 video.onseeked=()=>{if(current()){if(holding)capture(token);else pump();}};
 video.onended=()=>{if(current())freeze();};
 video.onerror=()=>{if(token===serial)failPlayback();};
 video.dataset.clipUrl=clip.url;
 localMedia.blob(clip.url).then(content=>{
  if(token!==serial||!enabled)return;
  localURL=URL.createObjectURL(content);video.src=localURL;video.load();
 }).catch(error=>{if(token!==serial)return;if(error.name!=='AbortError')failPlayback();else{loaded='';paintTransport();}});
}
function pump(){
 settlePanel();
 if(!enabled||!row||document.hidden||!visible||hero?.hidden){cancel();pauseDecoder();return;}
 if(!mount())return;
 if(!canPresent()){cancel();pauseDecoder();return;}
 if(!preview&&!selectionPrefs.allows(row)){cancel();if(running||loaded||holding||hero?.dataset.cinematic)release(false);return;}
 if(!library){catalogue();return;}
 if(catalogueLoading)return;
 const automatic=automaticPlan(),at=now();
 if(preview&&paused&&automatic&&at>=automatic.start&&at<automatic.end){
  cancel();release();preview=null;paused=false;pausedAt=0;row=automatic.row;key=automatic.key;skipped.delete(key);paintButton();
 }
 if(!library.length||completed.has(key)||skipped.has(key)||failed.has(key)||(!preview&&groupedRows.length&&!leaders.has(key)))return;
 clip=preview?.clip||choose();
 const end=row.at-5000,start=end-clip.duration*1000,t=now();
 if(t<start-10000){arm(start-10000);return;}
 prepare();
 if(paused&&preview){if(automatic&&automatic.start>t)arm(automatic.start);else cancel();video.pause();if(video.readyState>=2){video.hidden=false;still.hidden=true;hero.dataset.cinematic='paused';}return;}
 if(t<start){video.pause();arm(start);return;}
 if(t>=end){freeze();return;}
 arm(end);
 if(paused){video.pause();return;}
 if(video.readyState<2||holding||video.seeking)return;
 if(!video.videoWidth||!video.videoHeight){failPlayback();return;}
 const target=Math.max(0,(t-start)/1000);
 if(Math.abs(video.currentTime-target)>.3){video.currentTime=Math.min(target,Math.max(0,video.duration-.5/clip.fps));return;}
 if(video.hidden)video.hidden=false;if(!still.hidden)still.hidden=true;
 if(hero.dataset.cinematic!=='playing')hero.dataset.cinematic='playing';
 if(!running){clearCompaction();running=true;busyStart=t;hero.dataset.cinematicSize='expanded';currentIndex=library.indexOf(clip);if(preview)discardOverlaps(t,t);paintButton();}
 const hideArrival=!scheduledRow||t>=scheduledRow.at;if(arrival.hidden!==hideArrival)arrival.hidden=hideArrival;
 verifyPresentedFrame();
 audio();
 if(video.paused&&!playing){
  if(!video.getClientRects().length){video.muted=true;clearFrameCheck();framePresented=false;return;}
  playing=true;const token=serial;
  Promise.resolve(video.play()).catch(()=>{
   if(token!==serial||!canPresent()||video.hidden||holding||paused)return;
   // Browsers may reject sound before a user gesture; keep the movie on time.
   audioBlocked=true;video.muted=true;return video.play().catch(()=>{if(token===serial)freeze();});
  }).finally(()=>{if(token===serial)playing=false;});
 }
}
async function previewAt(index){
 if(!enabled||!mount())return;
 if(!library){catalogue();await request;}
 if(!enabled||!library?.length)return;
 cancel();release(false);clearCompaction();paused=false;pausedAt=0;currentIndex=(index%library.length+library.length)%library.length;
 manualCursor=currentIndex;
 const selected=library[currentIndex];preview={clip:selected};key='preview:'+ ++previewSerial;
 busyStart=now();row={id:key,at:busyStart+selected.duration*1000+5000};paintButton();pump();
}
async function previewStep(step){
 if(!enabled||!mount())return;
 if(!library){catalogue();await request;}
 if(enabled&&library?.length)return previewAt(panelIndex(step));
}
globalThis.WebIronmanCinematic={
  enable(value,alarm,owner,catalog,isMajor){const entering=Boolean(value)&&!enabled;enabled=Boolean(value);if(Array.isArray(catalog))selectionPrefs.refresh(owner,catalog,isMajor);selectionPrefs.visibility();if(typeof alarm==='function')canSound=alarm;paintButton();if(enabled){mount();groups(latestRows,latestVersion);expireStill(now(),scheduledRow);catalogue(entering);if(!catalogueLoading)localMedia.start();pump();}else{localMedia.stop();cancel();release(false);preview=null;paused=false;pausedAt=0;row=scheduledRow;key=rowKey(row);paintTransport();}},
 preferences(owner,catalog,isMajor){selectionPrefs.refresh(owner,catalog,isMajor);},
 selectionDirty(){return selectionPrefs.dirty();},
 refreshAudio(){audio();},
 previewNext(){return previewStep(1);},
 update(at,next,rows=next?[next]:[],version){
  const sameNext=rowKey(next)===rowKey(scheduledRow),sameGroups=groupVersion===(version??rows),correction=Math.abs(at-now());
  anchor=at;anchorClock=performance.now();
  latestRows=rows;latestVersion=version;scheduledRow=next;
  if(!enabled){row=next;key=rowKey(next);return;}
  groups(rows,version);expireStill(at,next);
  if(arrival){const hidden=!next||at>=next.at;if(arrival.hidden!==hidden)arrival.hidden=hidden;}
  settlePanel();
  if(correction>=250)cancel();
  // Native playback and existing deadlines do not need a per-second pump.
  if(sameNext&&sameGroups&&timer&&due>at&&correction<250&&
    ((!running&&!preview)||(preview&&paused)||(running&&!paused&&framePresented&&!video.paused&&!video.seeking&&canPresent()))){if(!preview&&rowKey(next)===key)row=next;return;}
  if(preview||running){if(preview||rowKey(next)!==key){pump();return;}}
  const nextKey=rowKey(next);
  if(nextKey!==key){cancel();release();key=nextKey;}
  row=next;pump();
 }
};
document.addEventListener('visibilitychange',()=>{if(document.hidden){localMedia.stop();setTransportOpen(false);}else if(enabled)localMedia.start();pump();});
const soundInput=event=>{if(enabled&&['notice-sound-enabled','notice-sound-volume'].includes(event.target?.id))audio();};
document.addEventListener('change',soundInput);
document.addEventListener('input',soundInput,{passive:true});
function gesture(){
 if(!enabled||!video||holding||paused||!running)return;
 if(!canPresent()||video.hidden||!video.getClientRects().length){pauseDecoder();return;}
 if(!video.videoWidth)return;
 const token=serial;verifyPresentedFrame();audio(true);
 if(video.paused)video.play().catch(()=>{if(token===serial){audioBlocked=true;video.muted=true;}});
}
document.addEventListener('pointerdown',gesture,{passive:true});
document.addEventListener('keydown',gesture);
for(const event of ['pointerdown','pointermove','click','keydown','scroll'])document.addEventListener(event,recordTransportActivity,{passive:true,capture:event==='scroll'});
 window.addEventListener('pagehide',()=>{setTransportOpen(false);localMedia.stop();cancel();release(false);});
window.addEventListener('pageshow',()=>{if(enabled)localMedia.start();pump();});
})();

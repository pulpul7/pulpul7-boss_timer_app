/* TXT expiry follows the web alarm's adjacent-gap grouping, without polling. */
const WebScheduleTXT=(()=>{
  let timer=0,generation=0;
  let payload=null,stamp='',madeAt=0,deadline=Infinity,width=0;
  function stop(){clearTimeout(timer);timer=0;generation++;}
  function fit(){
    if(selectedFilter!=='txt'||!hiddenMenuCollapsed||document.hidden)return;
    const box=element('schedule-txt'),current=box.clientWidth;
    if(!current||current===width)return;
    width=current;box.style.height='auto';box.style.height=box.scrollHeight+'px';
  }
  function arm(){
    if(!Number.isFinite(deadline))return;
    const current=generation;
    timer=setTimeout(()=>{if(current===generation)refresh();},Math.max(1,deadline-serverNow()));
  }
  function refresh(){
    stop();
    if(selectedFilter!=='txt'||!data||!hiddenMenuCollapsed||document.hidden)return;
    const now=serverNow();
    const key=[data.major_version,data.catalog_version,periodEnd(now,true,'tomorrow-end'),...COPY_CHECKBOXES.filter(id=>id!=='include-break').map(id=>element(id).checked)].join('|');
    if(payload===data&&stamp===key&&now>=madeAt&&now<deadline){fit();arm();return;}
    payload=data;stamp=key;madeAt=now;
    const eligible=sharingEvents(now,'tomorrow-end',false).filter(row=>row.kind!=='break'&&Number.isFinite(row.at));
    const bosses=eligible.filter(isBoss).sort((a,b)=>a.at-b.at);
    const groups=eligible.filter(row=>!isBoss(row)).map(row=>[row]);
    let last;
    for(const row of bosses){
      if(last==null||row.at-last>WebNotices.groupGap)groups.push([]);
      groups.at(-1).push(row);last=row.at;
    }
    const rows=[];
    deadline=Infinity;
    for(const group of groups){
      const end=group.at(-1).at;
      if(end>now){rows.push(...group);deadline=Math.min(deadline,end);}
    }
    rows.sort((a,b)=>a.at-b.at||(a.input_order||0)-(b.input_order||0));
    const text=shareText(rows),box=element('schedule-txt');
    if(box.value!==text){element('txt-width').textContent=text;box.value=text;width=0;}
    fit();arm();
  }
  document.addEventListener('visibilitychange',refresh);
  window.addEventListener('pagehide',stop);
  window.addEventListener('pageshow',refresh);
  return{refresh,stop,fit,copy:()=>copyText('tomorrow-end',element('schedule-txt').value,periodEnd(madeAt,true,'tomorrow-end'))};
})();

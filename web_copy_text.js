/* Capture the copied range before awaiting clipboard permission. */
function copyUntil(now,end){
  const time=localDate(end),gap=(Date.parse(dayKey(end))-Date.parse(dayKey(now)))/86400000;
  return (['오늘','내일','모레'][gap]||dateLabel(end))+' '+time.getUTCHours()+'시'+
    (time.getUTCMinutes()?' '+time.getUTCMinutes()+'분':'')+
    (time.getUTCSeconds()?' '+time.getUTCSeconds()+'초':'');
}
async function copyText(period='',value,end,now=serverNow()){
  period=typeof period==='string'?period:'';
  end??=periodEnd(now,true,period);
  value??=shareText(visibleRows(now,true,period));
  try {
    await navigator.clipboard.writeText(value);
    toast(node('strong',copyUntil(now,end)),'까지 복사됐습니다.');
  }catch(_){
    if(!element('copy-dialog').open)openCopyDialog();
    const area=document.createElement('textarea');area.value=value;area.setAttribute('aria-label','복사할 스케줄');
    area.className='copy-fallback';element('copy-events').before(area);area.focus();area.select();
    toast('복사 권한이 없습니다. 열린 텍스트를 선택해 복사해주세요.');
    area.addEventListener('blur',()=>area.remove(),{once:true});
  }
}
const cooling=new WeakSet();
document.addEventListener('click',event=>{
  const button=event.target.closest('#copy-left,#copy-right,#txt-copy,#text-copy');
  if(!button)return;
  if(cooling.has(button)){button.disabled=true;event.stopImmediatePropagation();return;}
  cooling.add(button);button.disabled=true;
  setTimeout(()=>{cooling.delete(button);button.disabled=!data;},1000);
},true);

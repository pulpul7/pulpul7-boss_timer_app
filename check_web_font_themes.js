(async()=>{
 const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const el=id=>document.getElementById(id),key='BossTimer.webFont.themes.v1:'+nicknameValue;
 const wait=async test=>{const until=performance.now()+5000;while(!test()){if(performance.now()>until)throw Error('font operation timeout: '+el('web-font-note').textContent);await new Promise(r=>setTimeout(r,20));}};
 const size=()=>parseInt(document.documentElement.style.getPropertyValue('--web-font-size-offset'));
 const ready=()=>wait(()=>!el('web-font-size-save').disabled);
 const theme=async id=>{el('theme-'+id).click();await ready();};
 const pick=async id=>{el('web-font-select').value=id;el('web-font-select').dispatchEvent(new Event('change',{bubbles:true}));await ready();};
 const old=localStorage.getItem(key);localStorage.removeItem(key);
 try{
  el('tab-theme').click();await theme('default');await pick('system');
  while(size()<0)el('web-font-size-plus').click();while(size()>0)el('web-font-size-minus').click();
  el('web-font-size-plus').click();el('web-font-size-plus').click();
  check(!localStorage.getItem(key),'font and size preview do not save silently');
  el('web-font-size-save').click();
  check(JSON.parse(localStorage.getItem(key)).themes.default.font==='system'&&size()===2,'current theme stores system font and chosen size');
  await theme('avengers');await pick('cafe24-dongdong');el('web-font-size-minus').click();el('web-font-size-save').click();
  const ironSize=size();check(el('web-font-select').value==='cafe24-dongdong'&&getComputedStyle(document.body).fontFamily.includes('BossTimerFont_cafe24_dongdong'),'real Cafe24 face loads for Iron Man');
  await theme('default');check(el('web-font-select').value==='system'&&size()===2,'dark restores its independent font and size');
  await theme('avengers');check(el('web-font-select').value==='cafe24-dongdong'&&size()===ironSize,'Iron Man restores its independent font and size');
  el('web-font-all-save').click();await theme('default');
  check(el('web-font-select').value==='cafe24-dongdong'&&size()===ironSize,'all-theme apply replaces previous dark choice');
  check(Object.keys(JSON.parse(localStorage.getItem(key)).themes).length===0,'all-theme apply clears old per-theme exceptions');
  el('web-font-size-default').click();await ready();
  check(el('web-font-select').value==='cafe24-dongdong'&&size()===0&&JSON.parse(localStorage.getItem(key)).themes.default.size===0,'default resets and saves current theme catalog pair');
  await theme('avengers');check(size()===ironSize,'default reset does not overwrite other themes');
  for(const id of ['web-font-size-default','web-font-size-save','web-font-all-save']){
   const button=el(id),s=getComputedStyle(button),bounds=button.getBoundingClientRect(),range=document.createRange();range.selectNodeContents(button);
   const text=range.getBoundingClientRect();
   check(!button.hidden&&bounds.height>=32&&s.alignItems==='center'&&text.left>=bounds.left&&text.right<=bounds.right&&text.top>=bounds.top&&text.bottom<=bounds.bottom,'button text fits '+id+' '+JSON.stringify({height:bounds.height,display:s.display,hidden:el('theme-panel').hidden,text:{top:text.top,bottom:text.bottom,left:text.left,right:text.right},button:{top:bounds.top,bottom:bounds.bottom,left:bounds.left,right:bounds.right}}));
  }
  check(document.documentElement.scrollWidth<=innerWidth,'font buttons create no horizontal overflow');
  check(!window.previewErrors.length,'no font or page errors');return {checks:checks.length,width:innerWidth};
 }finally{if(old===null)localStorage.removeItem(key);else localStorage.setItem(key,old);WebFonts.onStorage({key});}
})()

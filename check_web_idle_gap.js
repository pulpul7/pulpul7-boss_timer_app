(async()=>{
 const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const wait=ms=>new Promise(r=>setTimeout(r,ms));
 const gap=document.querySelector('.idle-menu-gap');
 const wheel=deltaY=>document.dispatchEvent(new WheelEvent('wheel',{deltaY,cancelable:true,bubbles:true}));
 document.getElementById('theme-default').click();window.scrollTo(0,0);WebMenuIdle.activity();
 check(!document.getElementById('idle-clock'),'compact date/time element is removed');
 check(getComputedStyle(gap).display==='none','expanded menus have no extra gap');
 for(const theme of ['default','sakura','forsythia','avengers']){
  document.getElementById('theme-'+theme).click();WebMenuIdle.activity();wheel(100);
  const style=getComputedStyle(gap),bounds=gap.getBoundingClientRect();
  check(style.display==='block'&&bounds.height===13.75&&parseFloat(style.marginBottom)===1,'previous clock spacing preserved '+theme);
  check(bounds.top<=4&&gap.textContent==='','empty top gap retains minimal outer padding '+theme);
  await wait(350);wheel(-100);
  check(getComputedStyle(gap).display==='none','menu restore removes spacer '+theme);
 }
 check(!window.previewErrors.length,'no runtime errors');return {checks:checks.length};
})()

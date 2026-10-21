/* No collapsed-menu gap remains after switching to scroll alignment. */
(async()=>{
 const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 check(!document.querySelector('.idle-menu-gap'),'obsolete collapsed-menu spacer removed');
 for(const theme of ['default','sakura','forsythia','avengers']){
  document.getElementById('theme-'+theme).click();window.scrollTo(0,0);
  await new Promise(r=>setTimeout(r,350));document.dispatchEvent(new WheelEvent('wheel',{deltaY:100,cancelable:true,bubbles:true}));
  check(!document.documentElement.classList.contains('menus-idle'),'scroll never hides menus '+theme);
  check(Math.abs(document.querySelector('.hero').getBoundingClientRect().top)<=1,'next boss aligned '+theme);
 }
 window.scrollTo(0,0);check(!window.previewErrors.length,'no runtime errors');return{checks:checks.length};
})()

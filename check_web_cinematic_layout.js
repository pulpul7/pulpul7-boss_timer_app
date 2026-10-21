/* Visible runtime controls and caption layering in the isolated page. */
(async()=>{
 const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const wait=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 document.getElementById('theme-avengers').click();selectFilter('all');window.scrollTo(0,0);
 const hero=document.getElementById('next-name').closest('.hero'),player=WebIronmanCinematic;
 const end=performance.now()+3000;
 while(document.getElementById('ironman-media-dock')?.hidden&&performance.now()<end)await new Promise(r=>setTimeout(r,20));
 const dock=document.getElementById('ironman-media-dock'),expand=document.getElementById('cinematic-expand'),drawer=document.getElementById('cinematic-drawer'),caption=hero.querySelector('.ironman-cinematic-arrival'),video=document.getElementById('ironman-cinematic-video');
 const next={id:'cinematic-layout-future',at:Date.now()+600000},at=Date.now();player.update(at,next,[next],'layout-fixture');await wait();
 check(!dock.hidden&&getComputedStyle(dock).display==='flex','dock is displayed after companion style loads');
 const svg=expand.querySelector('svg'),r=svg.getBoundingClientRect();
 check(r.width>=70&&r.height>=25&&svg.querySelectorAll('path').length>=3,'metal triangle artwork is visible at full size');
 const h=hero.getBoundingClientRect(),d=dock.getBoundingClientRect();check(Math.abs(d.top-h.bottom)<=12,'triangle dock stays attached beneath next boss');
 check(caption.hidden&&getComputedStyle(caption).display==='none','waiting caption never appears behind the helmet');
 const disabled=expand.disabled;expand.disabled=false;expand.click();await wait();
 check(!drawer.hidden&&drawer.getBoundingClientRect().height>25,'triangle opens visible transport panel');
 check(drawer.querySelectorAll('button svg path').length===3&&getComputedStyle(drawer).borderImageSource.includes('blob:'),'three media icons retain the matching metal frame');
 expand.click();expand.disabled=disabled;
 const links=document.querySelectorAll('link[href="/cinematic/player.css"]').length;
 dock.remove();player.enable(true);await wait();
 check(hero.nextElementSibling===dock&&dock.isConnected,'existing dock is restored after detachment');
 check(document.getElementById('ironman-cinematic-video')===video&&document.querySelectorAll('link[href="/cinematic/player.css"]').length===links,'repair reuses one decoder and one stylesheet');
 hero.dataset.cinematic='playing';caption.hidden=false;await wait();
 check(getComputedStyle(caption).display==='block'&&getComputedStyle(caption).position==='absolute'&&getComputedStyle(caption).zIndex==='3','playing caption sits on the foreground layer');
 check(getComputedStyle(hero,'::before').display==='none'&&getComputedStyle(hero,'::after').display==='none','helmet and reactor cannot cover the playing caption');
 hero.dataset.cinematic='paused';player.update(at+1,next,[next],'layout-fixture');await wait();check(caption.hidden,'paused video hides arrival caption');
 delete hero.dataset.cinematic;tick();check(!window.previewErrors?.length,'no production page errors');return{checks:checks.length};
})()

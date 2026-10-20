(async()=>{
 const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const wait=ms=>new Promise(r=>setTimeout(r,ms));
 document.getElementById('theme-avengers').click();WebMenuIdle.activity();
 const section=document.createElement('section');section.className='schedule-section';document.querySelector('main').append(section);
 const cases=[['regular','boss',false,false],['fixed','fixed',false,false],['star','boss',true,true],['major-fixed','fixed',false,true]];
 const rows=[];
 try{
  for(const [id,kind,important,major] of cases){
   const row={id,kind,name:id,important,major,at:serverNow()+3600000,text_color:'#ffffff',bg_color:'#26374b'};
   const host=document.createElement('div');host.className='event-row type-'+kind;
   const main=document.createElement('div');main.className='following-main';main.append(nameWithTime(row,'following-name'));
   const state=document.createElement('span');state.className='event-state';state.textContent='(01:00:00)';main.append(state);
   host.append(main,alarmButton(row));applyBackground(host,row);section.append(host);rows.push({row,host});
  }
  await wait(80);
  for(const {row,host} of rows){
   const major=row.major,expected=major?'rgb(255, 113, 91)':row.kind==='fixed'?'rgb(121, 232, 255)':'rgb(255, 242, 174)';
   const name=host.querySelector('.boss-name'),s=getComputedStyle(name),alarm=host.querySelector('.alarm');
   check(s.color===expected&&s.webkitTextFillColor===expected,'rank ink including text fill '+row.id);
   const art=getComputedStyle(alarm,'::before');
   check(major?art.display==='block'&&art.backgroundImage.includes('blob:'):art.display==='none','major-only portrait '+row.id);
   check(getComputedStyle(host,'::before').display===(major?'none':'block'),'only portrait row removes circuit decoration '+row.id);
   const a=alarm.getBoundingClientRect(),r=host.getBoundingClientRect(),text=host.querySelector('.following-main').getBoundingClientRect();
   if(major){const left=a.left+parseFloat(getComputedStyle(alarm).borderLeftWidth)+parseFloat(art.left),right=left+parseFloat(art.width);
    const matrix=new DOMMatrix(art.transform);check(art.backgroundSize==='contain'&&matrix.a===1&&matrix.d===1&&matrix.b===0&&matrix.c===0,'whole portrait uses original orientation '+row.id);
    check(left>=text.right-1&&right<=a.left&&left>=r.left,'portrait reserves space left of alarm '+row.id);
    check(Math.abs(parseFloat(art.width)-38.4)<.1&&Math.abs(parseFloat(art.height)-33.6)<.1&&a.left-right>=9.9,'portrait is 1.2x and stays ten pixels clear of gold alarm edge '+row.id);
    alarm.setAttribute('aria-pressed','true');check(getComputedStyle(alarm,'::before').backgroundImage===art.backgroundImage,'portrait remains on ON '+row.id);
   }
  }
  const majorHost=rows[2].host,hero=document.getElementById('next-name').closest('.hero');
  applyBackground(hero,rows[2].row);hero.querySelector('.hero-name').replaceChildren(...nameWithTime(rows[2].row,'').childNodes);
  check(getComputedStyle(hero.querySelector('.boss-name')).color==='rgb(255, 113, 91)','next boss uses major ink');
  hero.classList.add('spawn-blink');
  const frameLayer=hero.querySelector('.ironman-cinematic-stage'),glow=hero.getAnimations({subtree:true}).find(a=>a.animationName==='ironman-boss-frame');
  check(!!glow&&getComputedStyle(hero).outlineWidth==='0px'&&getComputedStyle(hero).animationName==='none','spawn frame has no separate outer outline or shadow animation');
  glow.pause();glow.currentTime=0;const dim=getComputedStyle(frameLayer,'::after').opacity;
  glow.currentTime=Number(glow.effect.getTiming().duration)/2;const bright=getComputedStyle(frameLayer,'::after'),frameBox=frameLayer.getBoundingClientRect(),heroBox=hero.getBoundingClientRect();
  check(Number(dim)<.01&&Number(bright.opacity)>.9&&bright.boxShadow.includes('inset')&&bright.clipPath!=='none'&&frameBox.left>=heroBox.left&&frameBox.right<=heroBox.right,'glow stays inside the angled frame and fades using opacity');
  hero.dataset.cinematicVisible='false';check(getComputedStyle(frameLayer,'::after').animationPlayState==='paused','offscreen frame glow pauses');delete hero.dataset.cinematicVisible;
  hero.classList.remove('spawn-blink');check(getComputedStyle(frameLayer,'::after').animationName==='none','frame glow stops after spawn blink');
  hero.dataset.cinematic='playing';check(getComputedStyle(hero.querySelector('.alarm'),'::before').visibility==='hidden','cinematic hides portrait with alarm');delete hero.dataset.cinematic;
  applyBackground(majorHost,rows[0].row);check(getComputedStyle(majorHost.querySelector('.alarm'),'::before').display==='none','reused row clears major portrait');
  const ironmanWidth=innerWidth,ironmanScroll=document.documentElement.scrollWidth;
  check(ironmanScroll<=window.rankExpectedWidth,'Iron Man fits physical viewport '+JSON.stringify({ironmanWidth,ironmanScroll,expected:window.rankExpectedWidth}));
  document.getElementById('theme-default').click();
  check(rows.every(({host})=>!getComputedStyle(host.querySelector('.alarm'),'::before').backgroundImage.includes('blob:')),'dark theme has no Iron Man portrait');
  check(getComputedStyle(rows[1].host.querySelector('.boss-name')).color==='rgb(255, 255, 255)','dark theme retains original configured name ink');
  check(document.documentElement.scrollWidth<=innerWidth,'no horizontal overflow');
  check(!window.previewErrors.length,'no runtime errors');
  return {checks:checks.length,width:ironmanWidth,darkWidth:innerWidth};
 }finally{section.remove();}
})()

/* One limited-edition butterfly. Document coordinates never reset on scroll. */
window.WebSakuraButterfly=(()=>{
  let enabled=false,layer=null,el=null,insect=null,bubble=null,mode='home',flight=null,trip=null;
  let pose={x:0,y:0,angle:0},moment=null,head=null,home=null,hero=null,homePoint=null,bossPoint=null,awayUntil=0;
  const visited=new Set();let wingURL='',bubbleURL='',frame=0;
  let lastDraw=0;
  // Original durations are the single reference; rates do not compound on later edits.
  const BASE={transfer:650,approach:3800,return:2400,startle:550,depart:1300};
  const RATE={flight:.3,homeStartle:.06,bossStartle:.1,bossDepart:.5};
  const VISIT={lead:15000,depart:1000};
  let large=false;
  function size(value) {
    large=value;el.dataset.role=value?'boss':'home';
    insect.style.setProperty('--sakura-insect-scale',value?'1':'.5');
  }
  const blob=svg=>URL.createObjectURL(new Blob([svg],{type:'image/svg+xml'}));
  const token=row=>row?String(row.at):'';
  const point=(node,big=false)=>{
    if(!node?.isConnected)return null;
    const r=node.getBoundingClientRect();
    return r.width && r.height?{x:r.left+(window.scrollX||0)+r.width*.72-(big?24:15),y:r.top+(window.scrollY||0)-(big?38:22)}:null;
  };
  function artwork() {
    if(!wingURL)wingURL=blob('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 42 62"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#f9d67a"/><stop offset=".4" stop-color="#f36ba8"/><stop offset="1" stop-color="#7551b5"/></linearGradient></defs><path d="M39 32C17-15-8 0 5 26L21 39C-5 26 1 65 19 57Q34 53 39 32Z" fill="url(#g)" stroke="#5d315e" stroke-width="1.2"/><g fill="none" stroke="#fff1cf" stroke-width=".8"><path d="M38 32 10 10M37 34 12 47M35 35 7 24M35 35 23 54"/></g><g fill="#ffe9f5" stroke="#b14f86" stroke-width=".5"><ellipse cx="16" cy="15" rx="4" ry="8"/><ellipse cx="16" cy="15" rx="8" ry="4"/><ellipse cx="16" cy="15" rx="4" ry="8" transform="rotate(45 16 15)"/><ellipse cx="16" cy="15" rx="8" ry="4" transform="rotate(45 16 15)"/><circle cx="16" cy="15" r="2.5" fill="#e4a736"/><circle cx="17" cy="44" r="6" fill="#b99ae6"/><circle cx="17" cy="44" r="2" fill="#fff1b7"/></g></svg>');
    if(!bubbleURL)bubbleURL=blob('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 56 48"><path d="M9 2h38q7 0 7 8v20q0 7-8 7H29L15 46l3-9H9q-7 0-7-8V10q0-8 7-8Z" fill="#fff5cf" stroke="#a8466a" stroke-width="2.5"/><path d="m19 10 1 15h4l1-15Zm13 0 1 15h4l1-15Z" fill="#b72a68"/><circle cx="22" cy="30" r="2.5" fill="#b72a68"/><circle cx="35" cy="30" r="2.5" fill="#b72a68"/></svg>');
  }
  function create() {
    if(el)return;artwork();
    el=document.createElement('button');el.type='button';el.className='sakura-butterfly';
    el.setAttribute('aria-label','나비 살짝 놀라게 하기');el.dataset.state='resting';el.hidden=true;
    insect=document.createElement('span');insect.className='sakura-insect';insect.setAttribute('aria-hidden','true');
    for(const name of ['left','right','body']) {
      const part=document.createElement('span');part.className='sakura-wing-'+name;
      if(name!=='body')part.style.setProperty('--sakura-wing','url("'+wingURL+'")');insect.append(part);
    }
    bubble=document.createElement('img');bubble.className='sakura-startle';bubble.src=bubbleURL;bubble.alt='!!';bubble.hidden=true;
    layer=document.createElement('div');layer.className='sakura-butterfly-layer';
    el.append(insect);el.append(bubble);layer.append(el);document.body.append(layer);
    el.addEventListener('pointerdown',event=>{event.preventDefault();event.stopPropagation();startle();});
    el.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();startle();}});
  }
  function clock() {
    if(!moment)return performance.now();
    const elapsed=performance.now()-moment.stamp,wall=Date.now()-moment.wall;
    return moment.now+(wall>=0 && Math.abs(wall-elapsed)>2000?wall:elapsed);
  }
  function schedule() {
    if(!frame && el && flight && !document.hidden && (enabled || mode==='escape'))frame=requestAnimationFrame(()=>{
      frame=0;
      const stamp=performance.now();
      if(!lastDraw || stamp-lastDraw>=1000/30-1){lastDraw=stamp;draw();}
      schedule();
    });
  }
  function dispose() {
    cancelAnimationFrame(frame);frame=0;layer?.remove();layer=null;el=null;mode='home';flight=null;trip=null;
  }
  function onScreen(p) {
    return p && p.x-(window.scrollX||0)>-60 && p.x-(window.scrollX||0)<window.innerWidth+60
      && p.y-(window.scrollY||0)>-30 && p.y-(window.scrollY||0)<window.innerHeight+30;
  }
  function outside(left=false) {
    const top=window.scrollY||0,y=Math.max(20,Math.min(window.innerHeight-50,pose.y-top-25-Math.random()*55));
    return {x:(window.scrollX||0)+(left?-60:window.innerWidth+70),y:top+y,angle:0};
  }
  function fly(kind,to,duration,now) {
    const dx=to.x-pose.x,dy=to.y-pose.y,distance=Math.hypot(dx,dy);
    const steps=Math.max(3,Math.min(14,Math.ceil(distance/75))),points=[{...pose}];
    // Small irregular rises and settles, chosen once per trip. No large orbits
    // and no random-per-frame jitter; the spline joins each beat smoothly.
    for(let i=1;i<steps;i++) {
      const t=i/steps,edge=Math.sin(Math.PI*t),lift=(Math.random()-.58)*Math.min(38,distance*.22);
      points.push({x:pose.x+dx*t+(Math.random()-.5)*Math.min(10,Math.abs(dx)/steps*.25)*edge,
        y:pose.y+dy*t+lift*edge});
    }
    points.push({...to});
    flight={from:{...pose},to:{...to},start:now,duration,
      ux:dx/(distance||1),uy:dy/(distance||1),points,
      flutter:Math.min(3+Math.random()*3,distance*.04),phase:Math.random()*Math.PI*2,
      beats:2.5+Math.random()*2};mode=kind;
    el.hidden=false;el.dataset.state='flying';
  }
  function depart(now,touched=false,themeExit=false) {
    bubble.hidden=!touched;
    awayUntil=now+(touched?10000:Math.max(1800,(trip?.at||now)-now+3000));
    const duration=touched || themeExit?BASE.startle/(large?RATE.bossStartle:RATE.homeStartle):BASE.depart/RATE.bossDepart;
    fly('escape',outside(),duration,now);schedule();
  }
  function startle() {
    if(!enabled || !el || mode==='away' || mode==='escape')return;
    const now=clock();if(head && head.at-now<=VISIT.lead && head.at-now>-VISIT.depart)visited.add(token(head));
    depart(now,true);
  }
  function approach(now) {
    if(!bossPoint || !trip || now>=trip.at+VISIT.depart){depart(now);return;}
    // Preserve the approach pace; the visit lasts through one second after spawn.
    fly('approach',bossPoint,Math.min(BASE.approach/RATE.flight,Math.max(250,trip.at-now-5500)),now);
  }
  function land(place) {
    if(place==='home')size(false);
    mode=place;flight=null;pose={...((place==='home'?homePoint:bossPoint)||pose),angle:0};
    el.dataset.state='resting';bubble.hidden=true;
  }
  function update(now,row) {
    moment={now,stamp:performance.now(),wall:Date.now()};head=row;if(!enabled)return;
    home=document.querySelector('.theme-dedication');hero=document.querySelector('.hero');
    // A folded menu is not a destination. Skip its layout read and any return flight.
    homePoint=home?.closest?.('[hidden],[data-folded="true"]')?null:point(home);bossPoint=point(hero,true);
    if(mode==='return' && !homePoint){flight=null;mode='home';el.hidden=true;cancelAnimationFrame(frame);frame=0;}
    if(mode==='home' && homePoint)pose={...homePoint,angle:0};
    if(mode==='perched' && bossPoint)pose={...bossPoint,angle:0};
    draw();schedule();
  }
  function draw() {
    if(!el || (!enabled && mode!=='escape'))return;
    const now=clock(),key=token(head),left=head?head.at-now:Infinity;
    if(enabled && trip && ['transfer','approach','perched'].includes(mode) && (key!==trip.key || !bossPoint))depart(now);
    if(enabled && ['transfer','approach','perched'].includes(mode) && trip && now>=trip.at+VISIT.depart)depart(now);
    if(enabled && mode!=='escape' && left<=VISIT.lead && left>-VISIT.depart && onScreen(bossPoint) && !visited.has(key)) {
      visited.add(key);if(visited.size>64)visited.delete(visited.values().next().value);
      trip={key,at:head.at};bubble.hidden=true;size(true);
      if((mode==='home' && onScreen(homePoint)) || (flight && !el.hidden && onScreen(pose)))fly('transfer',outside(true),Math.min(BASE.transfer/RATE.flight,Math.max(250,left-6500)),now);
      else {pose={x:(window.scrollX||0)-60,y:bossPoint.y-90,angle:0};approach(now);}
    }
    if(flight) {
      const u=Math.min(1,Math.max(0,(now-flight.start)/flight.duration)),q=u*u*(3-2*u);
      const sample=t=>{
        const nodes=flight.points,n=nodes.length-1,at=Math.min(n-1,Math.floor(t*n)),v=t*n-at;
        const a=nodes[Math.max(0,at-1)],b=nodes[at],c=nodes[at+1],d=nodes[Math.min(n,at+2)];
        const curve=k=>.5*((2*b[k])+(-a[k]+c[k])*v+(2*a[k]-5*b[k]+4*c[k]-d[k])*v*v
          +(-a[k]+3*b[k]-3*c[k]+d[k])*v*v*v);
        const flutter=Math.sin(Math.PI*t)**2*flight.flutter*
          (Math.sin(t*Math.PI*2*flight.beats+flight.phase)+.3*Math.sin(t*23+flight.phase));
        return {x:curve('x'),y:curve('y')+flutter};
      };
      const here=sample(q),ahead=sample(Math.min(1,q+.002)),behind=sample(Math.max(0,q-.002));
      const side=(ahead.y-behind.y)*flight.ux-(ahead.x-behind.x)*flight.uy;
      const bank=Math.atan2(side,Math.hypot(ahead.x-behind.x,ahead.y-behind.y)+.001)*180/Math.PI;
      pose={...here,angle:flight.from.angle*(1-q)+(flight.to.angle||0)*q
        +Math.sin(Math.PI*u)*Math.max(-22,Math.min(22,bank*.4))};
      if(u>=1) {
        flight=null;
        if(mode==='transfer')approach(now);
        else if(mode==='approach')land('perched');
        else if(mode==='return')land('home');
        else {mode='away';el.hidden=true;bubble.hidden=true;if(!enabled){dispose();return;}}
      }
    } else if(enabled && mode==='away' && now>=awayUntil && homePoint) {
      trip=null;size(false);pose={x:(window.scrollX||0)+window.innerWidth+60,y:homePoint.y-80,angle:0};
      fly('return',homePoint,BASE.return/RATE.flight,now);
    }
    if(mode==='home')el.hidden=!onScreen(homePoint);
    if(mode==='perched')el.hidden=!onScreen(bossPoint);
    if(!el.hidden) {
      const transform='translate3d('+(pose.x-(window.scrollX||0))+'px,'+(pose.y-(window.scrollY||0))+'px,0) rotate('+pose.angle+'deg)';
      if(el.style.transform!==transform)el.style.transform=transform;
    }
  }
  function sync(value) {
    if(enabled===Boolean(value))return;enabled=Boolean(value);
    if(enabled){create();if(mode==='home' && homePoint)land('home');schedule();}
    else if(el && !el.hidden){if(mode!=='escape')depart(clock(),false,true);schedule();}
    else dispose();
  }
  document.addEventListener('visibilitychange',()=>{
    if(layer)layer.hidden=document.hidden;
    if(document.hidden){cancelAnimationFrame(frame);frame=0;lastDraw=0;}else {draw();schedule();}
  });
  window.addEventListener('pagehide',()=>{cancelAnimationFrame(frame);frame=0;if(layer)layer.hidden=true;});
  window.addEventListener('pageshow',()=>{if(layer)layer.hidden=false;draw();schedule();});
  document.addEventListener('scroll',()=>{if(enabled && !flight){draw();schedule();}},{passive:true,capture:true});
  return {sync,update,draw};
})();

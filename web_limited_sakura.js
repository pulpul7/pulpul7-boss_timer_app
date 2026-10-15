/* Limited spring artwork. One lazy local atlas; no schedule/network polling. */
const WebLimitedSakura=(()=>{
  const url='/themes/limited-sakura-atlas-v1.png';
  // Pixel rectangles retain the naturally unequal silhouettes in the photo atlas.
  const crops=[[16,80,426,450],[449,105,355,390],[827,120,363,384],[1240,118,285,370],
    [55,600,310,350],[453,644,342,283],[867,593,153,349],[1146,578,370,380]];
  let active=false,loaded=false,requested=false,canvas=null,ctx=null,frame=0,last=0,spawnAt=0;
  let geometryDirty=true,scanned=0,targets=[],byNode=new Map(),petals=[],width=0,height=0;
  let petalSequence=0,flowerCache=[],scrollUntil=0;
  const image=new Image(),rand=(a,b)=>a+Math.random()*(b-a);
  const compact=()=>window.innerWidth<700;
  function seed(text) {let n=0;for(const c of text)n=(n*31+c.charCodeAt(0))>>>0;return n;}
  function wind(time,key=0) {
    const noise=x=>{const i=Math.floor(x),u=x-i,s=u*u*(3-2*u);
      const hash=n=>{const v=Math.sin(n*127.1)*43758.5453;return (v-Math.floor(v))*2-1;};
      return hash(i)*(1-s)+hash(i+1)*s;};
    return .58*noise(time*.21)+.42*noise(time*.47+key);
  }
  function sprite(index,x,y,size,angle=0,alpha=1,squash=1,breeze=null) {
    const [sx,sy,sw,sh]=crops[index];
    ctx.save();ctx.globalAlpha=alpha;ctx.translate(x,y);ctx.rotate(angle);ctx.scale(squash,1);
    if(index<4 && flowerCache[index] && breeze!==null) {
      const frames=flowerCache[index],side=size*Math.max(1,sh/sw);
      ctx.drawImage(frames[Math.max(0,Math.min(16,Math.round((breeze+1)*8)))],-side/2,-side/2,side,side);
    } else ctx.drawImage(image,sx,sy,sw,sh,-size/2,-size*sh/sw/2,size,size*sh/sw);
    ctx.restore();
  }
  function texture(surface,name) {
    return new Promise(resolve=>{
      let done=false;
      const finish=ok=>{if(!done){done=true;clearTimeout(timer);resolve(ok===true);}};
      const timer=setTimeout(()=>finish(false),1500);
      try {surface.toBlob(blob=>{
        if(blob)document.documentElement.style.setProperty(name,`url("${URL.createObjectURL(blob)}")`);
        finish(Boolean(blob));
      },'image/png');} catch(_) {finish(false);}
    });
  }
  async function warmFlowers() {
    // Warp only pale petal pixels. The dark twig, golden stamens and centers stay fixed.
    const centers=[[[.62,.37,.39,.34],[.27,.65,.27,.28],[.75,.69,.3,.3]],
      [[.50,.54,.46,.46]],[[.39,.41,.37,.39],[.79,.68,.29,.32]],[[.47,.53,.45,.46]]];
    const names=['twig','flower','pair','tilted'],S=72,N=7,textures=[];
    function triangle(c,base,a,b,d) {
      const det=a.x*(b.y-d.y)+b.x*(d.y-a.y)+d.x*(a.y-b.y);
      const v=k=>[(a[k]*(b.y-d.y)+b[k]*(d.y-a.y)+d[k]*(a.y-b.y))/det,
        (a[k]*(d.x-b.x)+b[k]*(a.x-d.x)+d[k]*(b.x-a.x))/det,
        (a[k]*(b.x*d.y-d.x*b.y)+b[k]*(d.x*a.y-a.x*d.y)+d[k]*(a.x*b.y-b.x*a.y))/det];
      const X=v('dx'),Y=v('dy'),mx=(a.dx+b.dx+d.dx)/3,my=(a.dy+b.dy+d.dy)/3;
      c.save();c.beginPath();
      for(const [i,p] of [a,b,d].entries())c[i?'lineTo':'moveTo'](mx+(p.dx-mx)*1.08,my+(p.dy-my)*1.08);
      c.closePath();c.clip();c.transform(X[0],Y[0],X[1],Y[1],X[2],Y[2]);c.drawImage(base,0,0);c.restore();
    }
    for(let index=0;index<4;index++) {
      const [sx,sy,sw,sh]=crops[index],base=document.createElement('canvas');base.width=base.height=S;
      const c=base.getContext('2d'),scale=S/Math.max(sw,sh),ox=(S-sw*scale)/2,oy=(S-sh*scale)/2;
      c.drawImage(image,sx,sy,sw,sh,ox,oy,sw*scale,sh*scale);
      const pixels=c.getImageData(0,0,S,S).data,points=[];
      for(let y=0;y<=N;y++)for(let x=0;x<=N;x++) {
        const px=x*S/N,py=y*S/N,k=(Math.min(S-1,Math.round(py))*S+Math.min(S-1,Math.round(px)))*4;
        let weight=0;
        if(pixels[k+3]>40 && pixels[k]>135 && pixels[k+2]>pixels[k+1]*.85) {
          for(const [cx,cy,rx,ry] of centers[index]) {
            const r=Math.hypot(((px-ox)/(sw*scale)-cx)/rx,((py-oy)/(sh*scale)-cy)/ry);
            if(r>.3 && r<1.15)weight=Math.max(weight,Math.min(1,(r-.3)/.45)*Math.min(1,(1.15-r)/.2));
          }
        }
        points.push({x:px,y:py,weight});
      }
      const frames=[];
      for(let f=0;f<17;f++) {
        const surface=document.createElement('canvas');surface.width=surface.height=S;const dest=surface.getContext('2d'),gust=(f-8)/8;
        const moved=points.map(p=>({...p,dx:p.x+p.weight*gust*2.5,dy:p.y+p.weight*gust*.8*Math.sin(p.x*.08+index)}));
        for(let y=0;y<N;y++)for(let x=0;x<N;x++) {
          const a=y*(N+1)+x,b=a+1,d=a+N+1,e=d+1;
          triangle(dest,base,moved[a],moved[b],moved[d]);triangle(dest,base,moved[b],moved[e],moved[d]);
        }
        frames.push(surface);if(f%4===3)await new Promise(resolve=>setTimeout(resolve,0));
      }
      flowerCache[index]=frames;
      // CSS icons use the same petal-only deformation, never a whole-flower rotation.
      const strip=document.createElement('canvas'),count=36;strip.width=S*count;strip.height=S;
      const sc=strip.getContext('2d');
      for(let f=0;f<count;f++) {
        const t=f/(count-1)*Math.PI*2,gust=.6*Math.sin(t)+.25*Math.sin(t*3+index)+.1*Math.sin(t*5+.6);
        sc.drawImage(frames[Math.round((gust+1)*8)],f*S,0);
      }
      textures.push(texture(strip,'--sakura-'+names[index]+'-wind'));
    }
    if((await Promise.all(textures)).every(Boolean))document.documentElement.setAttribute('data-sakura-wind-ready','true');
  }
  function makeTextures() {
    const names=['--sakura-twig','--sakura-flower','--sakura-pair','--sakura-tilted'];
    return Promise.all(names.map((name,index)=>{
      const [sx,sy,sw,sh]=crops[index],surface=document.createElement('canvas');
      // Small in-memory cutouts share the atlas download, never fetch separate files.
      surface.width=128;surface.height=Math.round(128*sh/sw);
      const context=surface.getContext('2d');
      if(!context)return Promise.resolve(false);
      context.drawImage(image,sx,sy,sw,sh,0,0,surface.width,surface.height);
      return texture(surface,name);
    }));
  }
  function resize() {
    width=window.innerWidth;height=window.innerHeight;
    const ratio=Math.min(window.devicePixelRatio || 1,compact()?1.5:2);
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
    canvas.style.width=width+'px';canvas.style.height=height+'px';ctx.setTransform(ratio,0,0,ratio,0,0);
    geometryDirty=true;
  }
  function scan(now) {
    targets=[];byNode=new Map();
    const candidates=document.querySelectorAll('.schedule-frame,.hero,.following,.event-row,.hidden-setting-card');
    for(const node of candidates) {
      if(node.classList.contains('type-break')) continue;
      const r=node.getBoundingClientRect();
      if(r.width<50 || r.height<15 || r.bottom<0 || r.top>height || r.right<0 || r.left>width) continue;
      const entry={node,r,seed:seed(node.id || node.dataset.id || node.className+Math.round(r.top+window.scrollY))};
      targets.push(entry);byNode.set(node,entry);
      if(targets.length>=(compact()?18:30)) break;
    }
    geometryDirty=false;scanned=now;
    for(const button of document.querySelectorAll('.schedule-tabs button,.alarm,.all-alarm,#push-toggle,#rest-filter,#fixed-filter')) {
      if(button.style.getPropertyValue('--sakura-wind-delay'))continue;
      const n=seed(button.id || button.parentElement?.textContent || 'flower');
      button.style.setProperty('--sakura-wind-delay',-(n%71)/10+'s');
      button.style.setProperty('--sakura-wind-duration',4.8+(n%31)/10+'s');
    }
  }
  function border(target,edge,u) {
    const r=target.r;
    if(edge==='left')return {x:r.left+1,y:r.top+r.height*u};
    if(edge==='right')return {x:r.right-1,y:r.top+r.height*u};
    return {x:r.left+r.width*u,y:edge==='bottom'?r.bottom-1:r.top+1};
  }
  function release(p,pose) {p.phase='fall';p.age=0;p.start={...pose};p.wind=rand(-7,7);}
  function createPetal() {
    const available=targets.filter(t=>t.r.top>35 && t.r.top<height-25);
    const target=available[Math.floor(rand(0,available.length))];
    if(!target) return;
    const edge=['top','left','right','bottom'][Math.floor(rand(0,4))],u=rand(.12,.88);
    const at=border(target,edge,u),direction=Math.random()<.5?-1:1;
    petals.push({node:target.node,edge,u,perch:petalSequence++%8===0,phase:'arrive',age:0,total:0,frame:Math.floor(rand(4,7)),
      size:rand(compact()?10:12,compact()?18:23),duration:rand(2.8,4.8),hold:rand(3.5,8),direction,
      start:{x:at.x+direction*rand(150,260),y:at.y-rand(130,260)},phaseSeed:rand(0,6),pose:{...at,angle:0}});
  }
  function advance(p,dt) {
    p.age+=dt;p.total+=dt;
    const target=byNode.get(p.node);
    if(p.phase!=='fall' && !target) release(p,p.pose);
    if(p.phase==='arrive') {
      const t=Math.min(1,p.age/p.duration),q=1-(1-t)**2,at=border(target,p.edge,p.u);
      p.pose={x:p.start.x+(at.x-p.start.x)*q+Math.sin(Math.PI*t)*p.direction*35,
        y:p.start.y+(at.y-p.start.y)*q,
        angle:p.direction*(1-t)*1.15+Math.sin(t*7+p.phaseSeed)*.27*(1-t)};
      if(t>=1){if(p.perch){p.phase='perch';p.age=0;}else release(p,p.pose);}
    } else if(p.phase==='perch') {
      const at=border(target,p.edge,p.u),ease=Math.min(1,p.age);
      p.pose={x:at.x,y:at.y,angle:wind(p.total,p.phaseSeed)*.18*ease};
      if(p.age>=p.hold)release(p,p.pose);
    } else {
      const t=p.age,wave=1-Math.exp(-t);
      p.pose={x:p.start.x+p.wind*t+wind(p.total,p.phaseSeed)*11*wave,
        y:p.start.y+t*25+t*t*2.7,
        angle:p.start.angle*Math.cos(t)+wind(p.total+.7,p.phaseSeed)*.24*wave};
    }
    return p.total<40 && (p.phase==='arrive' ||
      p.pose.y<height+60 && p.pose.x>-120 && p.pose.x<width+120);
  }
  function decorations(now=0) {
    const limit=compact()?11:19;
    let inside=0;
    for(const t of targets.slice(0,limit)) {
      // Stable, asymmetric positions sit on side and lower borders as well as tops.
      const r=t.r,n=t.seed,side=n%2?'left':'right';
      const a=border(t,side,.15+(n%60)/100),b=border(t,n%3?'top':'bottom',.12+(n%73)/100);
      const breeze=wind(now/1000,n*.001);
      sprite(n%3,a.x,a.y,compact()?23:29,side==='left'?-.35:.4,.92,1,breeze);
      const outer=t.node.classList.contains('schedule-frame') || t.node.classList.contains('hidden-setting-card');
      if(r.width>170 && (outer || inside++%4===0 && now>=scrollUntil))sprite(3,b.x,b.y,compact()?17:22,(n%7-3)*.12,.9,1,breeze);
    }
  }
  function schedule() {if(!frame && active && loaded && !document.hidden)frame=requestAnimationFrame(draw);}
  function draw(now) {
    frame=0;if(!active || !loaded || document.hidden)return;
    if(last && now-last<32){schedule();return;}
    const dt=last?Math.min(.25,(now-last)/1000):0;last=now;
    if(geometryDirty && now-scanned>90 || now-scanned>1600)scan(now);
    ctx.clearRect(0,0,width,height);decorations(now);
    if(now>=spawnAt && petals.length<(compact()?10:17)) {createPetal();spawnAt=now+rand(950,1600);}
    petals=petals.filter(p=>advance(p,dt));
    for(const p of petals) {
      const alpha=p.phase==='arrive'?Math.min(1,p.age/.4):Math.min(1,(height+45-p.pose.y)/50);
      sprite(p.frame,p.pose.x,p.pose.y,p.size,p.pose.angle,Math.max(0,alpha),.68+.32*Math.abs(Math.cos(p.total*1.7+p.phaseSeed)));
    }
    schedule();
  }
  function pause() {cancelAnimationFrame(frame);frame=0;last=0;}
  function refresh() {if(!active)return;geometryDirty=true;schedule();}
  function sync(value) {
    active=Boolean(value);
    if(!active){pause();petals=[];targets=[];scrollUntil=0;if(canvas)canvas.hidden=true;return;}
    if(canvas)canvas.hidden=false;
    if(!requested) {
      requested=true;
      image.onload=async()=>{
        try {
          canvas=document.createElement('canvas');canvas.className='limited-sakura-canvas';canvas.setAttribute('aria-hidden','true');
          canvas.hidden=!active;ctx=canvas.getContext('2d');if(!ctx)return;
          document.body.append(canvas);resize();loaded=true;schedule();
          // Petals use the atlas directly; slow/blocked toBlob must not stop them.
          await makeTextures();document.documentElement.setAttribute('data-sakura-ready','true');
          warmFlowers().catch(()=>{});
        } catch(_) { /* Decoration failure never affects the schedule. */ }
      };
      image.onerror=()=>{ /* One failed atlas request, no retry loop. */ };
      image.src=url;
    }
    refresh();
  }
  window.addEventListener('resize',()=>{if(active && loaded){resize();refresh();}},{passive:true});
  document.addEventListener('scroll',()=>{if(active)scrollUntil=performance.now()+180;refresh();},{passive:true,capture:true});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();else refresh();});
  window.addEventListener('pagehide',pause);
  window.addEventListener('pageshow',refresh);
  return {sync,refresh};
})();

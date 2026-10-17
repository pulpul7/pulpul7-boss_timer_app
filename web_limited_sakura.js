/* Limited spring artwork. One lazy local atlas; no schedule/network polling. */
const WebLimitedSakura=(()=>{
  const url='/themes/limited-sakura-atlas-v1.png';
  // Pixel rectangles retain the naturally unequal silhouettes in the photo atlas.
  const crops=[[16,80,426,450],[449,105,355,390],[827,120,363,384],[1240,118,285,370],
    [55,600,310,350],[453,644,342,283],[867,593,153,349],[1146,578,370,380]];
  let active=false,loaded=false,requested=false,canvas=null,ctx=null,frame=0,frameTimer=0,last=0,spawnAt=0;
  let geometryDirty=true,scanned=0,targets=[],byNode=new Map(),petals=[],width=0,height=0;
  let petalSequence=0,scanDue=0;
  let layoutKey=null,warming=false,warmed=false;
  let attachments=new Map(),nodeSequence=0;
  const nodeIds=new WeakMap();
  const image=new Image(),rand=(a,b)=>a+Math.random()*(b-a);
  const compact=()=>window.innerWidth<700;
  function seed(text) {let n=0;for(const c of text)n=(n*31+c.charCodeAt(0))>>>0;return n;}
  function wind(time,key=0) {
    const noise=x=>{const i=Math.floor(x),u=x-i,s=u*u*(3-2*u);
      const hash=n=>{const v=Math.sin(n*127.1)*43758.5453;return (v-Math.floor(v))*2-1;};
      return hash(i)*(1-s)+hash(i+1)*s;};
    return .58*noise(time*.21)+.42*noise(time*.47+key);
  }
  function sprite(index,x,y,size,angle=0,alpha=1,squash=1) {
    const [sx,sy,sw,sh]=crops[index];
    ctx.save();ctx.globalAlpha=alpha;ctx.translate(x,y);ctx.rotate(angle);ctx.scale(squash,1);
    ctx.drawImage(image,sx,sy,sw,sh,-size/2,-size*sh/sw/2,size,size*sh/sw);
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
    if(warming || warmed || !active || document.hidden)return;
    warming=true;
    try {
    // Warp only pale petal pixels. The dark twig, golden stamens and centers stay fixed.
    const centers=[[[.62,.37,.39,.34],[.27,.65,.27,.28],[.75,.69,.3,.3]],
      [[.50,.54,.46,.46]],[[.39,.41,.37,.39],[.79,.68,.29,.32]],[[.47,.53,.45,.46]]];
    const names=['twig','flower','pair','tilted'],S=compact()?48:72,N=compact()?4:8,F=compact()?17:33,count=compact()?42:84,textures=[];
    document.documentElement.style.setProperty('--sakura-strip-width',count*100+'%');
    document.documentElement.style.setProperty('--sakura-strip-steps',String(count-1));
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
      for(let f=0;f<F;f++) {
        if(!active || document.hidden)return;
        const surface=document.createElement('canvas');surface.width=surface.height=S;const dest=surface.getContext('2d'),gust=(f-(F-1)/2)/((F-1)/2);
        const moved=points.map(p=>({...p,dx:p.x+p.weight*gust*6.5*Math.cos(p.y*.035+index),dy:p.y+p.weight*gust*3.1*Math.sin(p.x*.08+index)}));
        for(let y=0;y<N;y++)for(let x=0;x<N;x++) {
          const a=y*(N+1)+x,b=a+1,d=a+N+1,e=d+1;
          triangle(dest,base,moved[a],moved[b],moved[d]);triangle(dest,base,moved[b],moved[e],moved[d]);
        }
        frames.push(surface);if(f%2===1)await new Promise(resolve=>setTimeout(resolve,0));
      }
      // CSS icons use the same petal-only deformation, never a whole-flower rotation.
      const strip=document.createElement('canvas'),cell=48;strip.width=cell*count;strip.height=cell;
      const sc=strip.getContext('2d');
      for(let f=0;f<count;f++) {
        const t=f/(count-1)*Math.PI*2,gust=.62*Math.sin(t)+.25*Math.sin(t*3+index)+.12*Math.sin(t*5+.6);
        sc.drawImage(frames[Math.round((gust+1)*(F-1)/2)],f*cell,0,cell,cell);
      }
      textures.push(texture(strip,'--sakura-'+names[index]+'-wind'));
    }
    const ready=(await Promise.all(textures)).every(Boolean);warmed=true;
    if(ready)document.documentElement.setAttribute('data-sakura-wind-ready','true');
    } catch(_) {warmed=true;} finally {warming=false;}
  }
  function makeTextures() {
    const names=['--sakura-twig','--sakura-flower','--sakura-pair','--sakura-tilted','--sakura-petal0','--sakura-petal1','--sakura-petal2'];
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
    const ratio=Math.min(window.devicePixelRatio || 1,compact()?1:2);
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
    canvas.style.width=width+'px';canvas.style.height=height+'px';ctx.setTransform(ratio,0,0,ratio,0,0);
    geometryDirty=true;scanDue=0;
  }
  function view(t) {
    const x=t.x-(window.scrollX || 0),y=t.y-(window.scrollY || 0);
    return {left:x,top:y,right:x+t.w,bottom:y+t.h,width:t.w,height:t.h};
  }
  function art(node,name) {
    const el=document.createElement('span');el.className=name;el.setAttribute('aria-hidden','true');
    node.classList.add('sakura-anchor');node.append(el);return el;
  }
  function fixedFlower(t,right=false) {
    const el=art(t.node,'sakura-fixed'),n=t.seed;
    const hero=t.node.classList.contains('hero'),index=right?0:hero?1+n%2:n%3,side=right?'right':hero || n%2?'left':'right';
    el.style.setProperty('--sakura-static','var(--sakura-'+['twig','flower','pair','tilted'][index]+')');
    el.style.setProperty('--sakura-moving','var(--sakura-'+['twig','flower','pair','tilted'][index]+'-wind)');
    el.style.setProperty('--sakura-wind-duration',4.2+(n%39)/10+'s');
    el.style.setProperty('--sakura-wind-delay',-(n%111)/10+'s');
    el.style.setProperty('--sakura-angle',(side==='left'?-20:23)+'deg');
    el.style.width=el.style.height=((compact()?46:58)*(hero?1:.7))+'px';
    el.style.left=side==='left'?'1px':'calc(100% - 1px)';
    el.style.top=hero?(right?'70%':'64%'):(15+n%60)+'%';
    return el;
  }
  function scan(now) {
    targets=[];byNode=new Map();const cards=[];
    const candidates=document.querySelectorAll('.schedule-app .schedule-frame,.schedule-app .hero,.schedule-app .following,.schedule-app .event-row,.schedule-app .hidden-setting-card');
    for(const node of candidates) {
      const r=node.getBoundingClientRect();
      if(r.width<50 || r.height<15)continue;
      if(!nodeIds.has(node))nodeIds.set(node,++nodeSequence);
      const t={node,x:r.left+(window.scrollX || 0),y:r.top+(window.scrollY || 0),w:r.width,h:r.height,
        seed:seed(node.id || node.dataset.id || String(nodeIds.get(node)))};
      const rest=node.classList.contains('type-break');
      if(!rest)cards.push(t);
      if(r.bottom<-300 || r.top>height+300 || r.right<0 || r.left>width)continue;
      if(rest)continue;
      if(targets.length>=(compact()?40:70))continue;
      targets.push(t);byNode.set(node,t);
    }
    // Every rendered card owns its flowers, including offscreen cards. Scrolling
    // never changes this membership or waits for the collision-geometry refresh.
    const wanted=new Set();
    for(const t of cards) {
      wanted.add(t.node);
      const hero=t.node.classList.contains('hero');
      const keep=hero || t.seed%2===1 && Math.floor(t.seed/2)%10<4;
      let list=attachments.get(t.node);
      if(!list?.every(el=>el.parentElement===t.node) || list.length!==(hero?2:Number(keep))) {
        list?.forEach(el=>el.remove());list=hero?[fixedFlower(t),fixedFlower(t,true)]:keep?[fixedFlower(t)]:[];attachments.set(t.node,list);
      }
      const r=view(t),state=r.bottom<-64 || r.top>height+64?'paused':'running';
      for(const el of list) {
        const size=(compact()?46:58)*(hero?1:.7)+'px';
        if(el.style.width!==size)el.style.width=el.style.height=size;
        if(el.style.animationPlayState!==state)el.style.animationPlayState=state;
      }
    }
    for(const [node,list] of attachments)if(!wanted.has(node)) {
      list.forEach(el=>el.remove());attachments.delete(node);
    }
    geometryDirty=false;scanned=now;
    for(const button of document.querySelectorAll('.schedule-tabs button,.alarm,.all-alarm,#push-toggle,#rest-filter,#fixed-filter')) {
      const r=button.getBoundingClientRect(),play=r.width && r.bottom>=-40 && r.top<=height+40?'running':'paused';
      if(button.style.getPropertyValue('--sakura-icon-play')!==play)button.style.setProperty('--sakura-icon-play',play);
      if(button.style.getPropertyValue('--sakura-wind-delay'))continue;
      const n=seed(button.id || button.parentElement?.textContent || 'flower');
      button.style.setProperty('--sakura-wind-delay',-(n%111)/10+'s');
      button.style.setProperty('--sakura-wind-duration',4.2+(n%39)/10+'s');
    }
  }
  function border(t,edge,u) {
    const r=view(t);
    if(edge==='left')return {x:r.left+1,y:r.top+r.height*u};
    if(edge==='right')return {x:r.right-1,y:r.top+r.height*u};
    return {x:r.left+r.width*u,y:edge==='bottom'?r.bottom-1:r.top+1};
  }
  function clearPerch(p) {if(p.el){p.el.remove();p.el=null;}}
  function paintPerch(p) {
    p.el.style.transform='translate(-50%,-50%) rotate('+p.pose.angle+'rad) scaleX('+(.12+.88*Math.abs(Math.cos(p.roll)))+')';
  }
  function release(p) {
    clearPerch(p);p.phase='fall';p.age=0;p.perchable=false;
    p.vx=wind(p.total,p.key)*22;p.vy=8;
  }
  function createPetal() {
    const petal={phase:'fall',age:0,total:0,frame:Math.floor(rand(4,7)),size:rand(compact()?10:12,compact()?18:23),
      perchable:petalSequence++%8===0,hold:rand(2.8,6.5),key:rand(0,40),roll:rand(0,6.28),spin:rand(-1,1),
      vx:rand(-15,15),vy:rand(15,27),pose:{x:rand(12,width-12),y:-28,angle:rand(-3,3)}};
    petals.push(petal);return petal;
  }
  function contact(a,b) {
    let best=null;
    for(const t of targets) {
      const r=view(t);if(r.bottom<0 || r.top>height)continue;
      for(const edge of ['top','bottom','left','right']) {
        const horizontal=edge==='top' || edge==='bottom',delta=horizontal?b.y-a.y:b.x-a.x;
        if(Math.abs(delta)<.001)continue;
        const at=horizontal?(edge==='top'?r.top+1:r.bottom-1):(edge==='left'?r.left+1:r.right-1);
        const q=(at-(horizontal?a.y:a.x))/delta;
        if(q<=0 || q>1 || best && q>=best.q)continue;
        const along=horizontal?a.x+(b.x-a.x)*q:a.y+(b.y-a.y)*q;
        const u=(along-(horizontal?r.left:r.top))/(horizontal?r.width:r.height);
        if(u<.03 || u>.97)continue;
        best={target:t,edge,u,q};
      }
    }
    return best;
  }
  function perch(p,hit) {
    p.node=hit.target.node;p.edge=hit.edge;p.u=hit.u;p.phase='perch';p.age=0;
    p.pose={...border(hit.target,p.edge,p.u),angle:p.pose.angle};p.landedAngle=p.pose.angle;
    p.el=art(p.node,'sakura-perched');p.el.style.backgroundImage='var(--sakura-petal'+(p.frame-4)+')';
    p.el.style.width=p.size+'px';p.el.style.height=p.size*crops[p.frame][3]/crops[p.frame][2]+'px';
    p.el.style.left=p.edge==='left'?'1px':p.edge==='right'?'calc(100% - 1px)':p.u*100+'%';
    p.el.style.top=p.edge==='top'?'1px':p.edge==='bottom'?'calc(100% - 1px)':p.u*100+'%';
    paintPerch(p);
  }
  function advance(p,dt) {
    p.age+=dt;p.total+=dt;
    if(p.phase==='perch') {
      const target=byNode.get(p.node);
      if(!target || !p.node.isConnected || !p.el.isConnected){clearPerch(p);return false;}
      p.pose={...border(target,p.edge,p.u),angle:p.landedAngle+wind(p.total,p.key)*.16*Math.min(1,p.age)};
      paintPerch(p);
      if(p.age>=p.hold)release(p);
    } else {
      const before={...p.pose},gust=wind(p.total,p.key),flutter=Math.sin(p.total*2.4+p.key);
      p.roll+=dt*(1.8+gust*.8);p.spin+=(gust*.85+flutter*.65-p.spin*.35)*dt;
      p.vx+=(gust*54+flutter*16-p.vx)*dt*1.1;
      p.vy+=((23+Math.abs(Math.sin(p.roll))*17)-p.vy)*dt*1.3;
      p.pose={x:before.x+p.vx*dt,y:before.y+p.vy*dt,angle:before.angle+p.spin*dt};
      if(p.perchable){const hit=contact(before,p.pose);if(hit)perch(p,hit);}
    }
    const alive=p.total<55 && p.pose.y<height+70 && p.pose.x>-150 && p.pose.x<width+150;
    if(!alive)clearPerch(p);return alive;
  }
  let butterflyRequested=false,headMoment=null;
  function update(now,row) {
    headMoment={now,row,stamp:performance.now()};
  }
  function butterflySync(value) {
    const api=window.WebSakuraButterfly;
    if(api){api.sync(value);if(headMoment)api.update(headMoment.now+performance.now()-headMoment.stamp,headMoment.row);return;}
    if(!value || butterflyRequested)return;
    butterflyRequested=true;
    const script=document.createElement('script');script.src='/themes/limited-sakura-butterfly-v1.js';
    script.onload=()=>butterflySync(active);document.head.append(script);
  }
  function schedule() {
    if(frame || frameTimer || !active || !loaded || document.hidden)return;
    const wait=last?1000/(compact()?24:30)-(performance.now()-last):0;
    if(wait>2) {frameTimer=setTimeout(()=>{frameTimer=0;schedule();},wait);return;}
    frame=requestAnimationFrame(draw);
  }
  function draw(now) {
    frame=0;if(!active || !loaded || document.hidden)return;
    if(last && now-last<1000/(compact()?24:30)-1){schedule();return;}
    const dt=last?Math.min(.05,(now-last)/1000):0;last=now;
    if(geometryDirty && now>=scanDue || now-scanned>10000)scan(now);
    ctx.clearRect(0,0,width,height);
    if(now>=spawnAt && petals.length<(compact()?4:8)){createPetal();spawnAt=now+rand(1900,3200);}
    petals=petals.filter(p=>advance(p,dt));
    for(const p of petals)if(p.phase==='fall') {
      const alpha=Math.min(1,p.total/.4,(height+45-p.pose.y)/50),squash=.12+.88*Math.abs(Math.cos(p.roll));
      sprite(p.frame,p.pose.x,p.pose.y,p.size,p.pose.angle,Math.max(0,alpha),squash);
    }
    schedule();
  }
  function pause() {cancelAnimationFrame(frame);clearTimeout(frameTimer);frameTimer=0;frame=0;last=0;document.documentElement.setAttribute('data-sakura-paused','true');}
  function refresh(key) {if(!active)return;
    if(key===undefined || key!==layoutKey){geometryDirty=true;scanDue=0;layoutKey=key;}
    if(!document.hidden)document.documentElement.removeAttribute('data-sakura-paused');
    if(loaded)warmFlowers().catch(()=>{});
    if(headMoment)window.WebSakuraButterfly?.update(headMoment.now+performance.now()-headMoment.stamp,headMoment.row);schedule();}
  function clean() {
    petals.forEach(clearPerch);petals=[];
    for(const list of attachments.values())list.forEach(el=>el.remove());
    for(const node of document.querySelectorAll('.sakura-anchor'))node.classList.remove('sakura-anchor');
    attachments.clear();
    targets=[];byNode.clear();
  }
  function sync(value) {
    active=Boolean(value);butterflySync(active);
    if(!active){pause();clean();if(canvas)canvas.hidden=true;return;}
    if(canvas)canvas.hidden=false;
    if(!requested) {
      requested=true;
      image.onload=async()=>{
        try {
          canvas=document.createElement('canvas');canvas.className='limited-sakura-canvas';canvas.setAttribute('aria-hidden','true');
          canvas.hidden=!active;ctx=canvas.getContext('2d');if(!ctx)return;
          document.body.append(canvas);resize();loaded=true;schedule();
          await makeTextures();document.documentElement.setAttribute('data-sakura-ready','true');
          warmFlowers().catch(()=>{});
        } catch(_) { /* Decoration failure never affects the schedule. */ }
      };
      image.onerror=()=>{};image.src=url;
    }
    refresh();
  }
  window.addEventListener('resize',()=>{if(active && loaded){resize();refresh();}},{passive:true});
  document.addEventListener('scroll',event=>{
    if(!active)return;
    // Layout reads run once in rAF, never inside the scroll event. Window scroll
    // needs only occasional new-card discovery; cached document coordinates suffice.
    if(!geometryDirty){geometryDirty=true;scanDue=performance.now()+(event.target===document?220:0);}
    else if(event.target!==document)scanDue=0;
    schedule();
  },{passive:true,capture:true});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();else refresh();});
  window.addEventListener('pagehide',pause);window.addEventListener('pageshow',refresh);
  return {sync,refresh,update};
})();

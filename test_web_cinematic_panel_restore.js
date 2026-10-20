/* Production two-phase final-frame timer, including cancelled/late callbacks. */
const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
function fixture(){
 let stamp=0,id=0;const timers=new Map();
 const api=new Function('performance','setTimeout','clearTimeout',`
  let panelTimer=0,compactDeadline=0,restoreDeadline=0,running=false,preview=null;
  const hero={dataset:{}},still={hidden:true,width:0,height:0};let holdGroup=null,holdOwner='';
  ${source.slice(source.indexOf('function clearCompaction('),source.indexOf('function setTransportOpen('))}
  ${source.slice(source.indexOf('function clearStill('),source.indexOf('function failPlayback('))}
  return {hero,still,settle:settlePanel,clear:clearStill,
   end(){running=false;preview=null;still.hidden=false;still.width=960;still.height=226;hero.dataset.cinematic='still';hero.dataset.cinematicSize='expanded';compactAfterEnd();},
   play(){clearCompaction();running=true;hero.dataset.cinematic='playing';hero.dataset.cinematicSize='expanded';},
   deadlines:()=>[compactDeadline,restoreDeadline]};
 `)({now:()=>stamp},(fn,ms)=>{timers.set(++id,{fn,at:stamp+ms});return id;},key=>timers.delete(key));
 return {api,timers,time:value=>stamp=value,advance(value){
  while(true){const next=[...timers].filter(([,t])=>t.at<=value).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;
   stamp=next[1].at;timers.delete(next[0]);next[1].fn();}stamp=value;
 }};
}
const f=fixture();f.api.end();
check(f.timers.size===1,'final frame schedules only one panel timer');
f.advance(4999);check(!f.api.still.hidden&&f.api.hero.dataset.cinematicSize==='expanded','final frame stays expanded for first five seconds');
f.advance(5000);check(!f.api.still.hidden&&f.api.hero.dataset.cinematicSize==='compact','five seconds compacts while retaining final frame');
check(f.timers.size===1&&f.api.deadlines().join()==='0,10000','compaction replaces its timer with one restore deadline');
f.advance(9999);check(!f.api.still.hidden,'compact final frame stays for another five seconds');
f.advance(10000);check(f.api.still.hidden&&f.api.still.width===0&&f.api.still.height===0&&!f.api.hero.dataset.cinematic,'ten seconds restores initial artwork and frees bitmap');
check(f.api.hero.dataset.cinematicSize==='compact'&&f.timers.size===0,'restored panel stays compact without timer');
const early=fixture();early.api.end();early.advance(2000);early.api.clear();
check(early.timers.size===0&&early.api.hero.dataset.cinematicSize==='compact','boss transition cancels pending deadlines and restores compact panel');
const replaced=fixture();replaced.api.end();replaced.advance(5000);const obsolete=[...replaced.timers.values()][0].fn;
replaced.advance(6000);replaced.api.play();replaced.time(11000);obsolete();
check(replaced.api.hero.dataset.cinematic==='playing'&&replaced.api.hero.dataset.cinematicSize==='expanded','late previous callback cannot clear or shrink new playback');
const next=fixture();next.api.end();next.advance(5000);const previous=[...next.timers.values()][0].fn;
next.advance(7000);next.api.end();next.time(10000);previous();
check(!next.api.still.hidden&&next.api.hero.dataset.cinematicSize==='expanded','old callback cannot expire a newer final frame before its deadline');
next.advance(17000);check(next.api.still.hidden,'new final frame has its own complete two-phase lifetime');
const background=fixture();background.api.end();background.time(20000);background.api.settle();
check(background.api.still.hidden&&background.timers.size===0,'late background resume consumes both elapsed deadlines immediately');
return {checks:checks.length};

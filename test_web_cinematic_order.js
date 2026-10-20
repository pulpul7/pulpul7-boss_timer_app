/* Run with `source` bound to the production cinematic.js text. */
const orderChecks=[];
function assertOrder(ok,label){if(!ok)throw Error(label);orderChecks.push(label);}
const orderSource=source.slice(source.indexOf('function choose('),source.indexOf('function groups('));
const makeOrder=new Function('random',`
 let library=['a','b','c','d'].map(url=>({url})),automaticCursor=-1;
 let currentIndex=-1,manualCursor=-1,preview=null,running=false;
 const automaticMovies=new Map(),Math=Object.create(globalThis.Math);Math.random=random;
 const automaticPlan=()=>({movie:choose('upcoming')});
 ${orderSource}
 return {choose,advance:advanceAutomatic,size:()=>automaticMovies.size,panel:panelIndex,
 manual:index=>{manualCursor=currentIndex=index;preview={};},
 automatic:index=>{manualCursor=-1;currentIndex=index;preview=null;running=true;},
 end:()=>{preview=null;running=false;}};
`);
for(const [seed,start] of [[0,'a'],[.5,'c'],[.999999,'d']]){
 let calls=0;const order=makeOrder(()=>{++calls;return seed;});
 const firstPosition=['a','b','c','d'].indexOf(start);
 assertOrder(order.panel(0)===firstPosition&&order.panel(1)===firstPosition,'panel first play matches assigned automatic movie '+seed);
 assertOrder(order.panel(-1)===(firstPosition+3)%4,'panel previous follows same rotated list '+seed);
 order.manual(firstPosition);
 assertOrder(order.panel(1)===(firstPosition+1)%4&&order.panel(-1)===(firstPosition+3)%4,'manual panel moves in automatic list order '+seed);
 order.end();
 assertOrder(order.choose('boss-1').url===start,'random first position '+seed);
 for(let n=0;n<20;n++)order.choose('future-'+n);
 assertOrder(calls===1&&order.choose('boss-1').url===start,'planning and skipped future rows do not consume or rerandomize');
 const first=order.choose('boss-1');order.advance('boss-1',first);order.advance('boss-1',first);
 order.automatic(firstPosition);order.end();
 assertOrder(order.panel(0)===(firstPosition+1)%4,'panel realigns to next automatic slot after actual movie '+seed);
 assertOrder(order.choose('boss-1')===first,'active movie remains stable across pause/resume and repeated events');
 const expected=['a','b','c','d'],offset=expected.indexOf(start);
 for(let n=1;n<=8;n++){
  const key='boss-'+(n+1),movie=order.choose(key);
  assertOrder(movie.url===expected[(offset+n)%4],'sequential automatic playback and wraparound '+seed+'/'+n);
  order.advance(key,movie);
 }
 assertOrder(calls===1,'full cycles never rerandomize');
 for(let n=0;n<100;n++){const key='extra-'+n;order.advance(key,order.choose(key));}
 assertOrder(order.size()===64,'automatic assignment memory remains bounded');
}
JSON.stringify(orderChecks);

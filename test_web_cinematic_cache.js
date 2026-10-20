/* Production localMedia cache migration, fresh download and persistent reuse. */
const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
const code=source.slice(source.indexOf('const localMedia='),source.indexOf('const cancel='));
function fixture(blocked=false){
 const stores=new Map(),deleted=[],requests=[];
 let fail=false,bodyReads=0;
 const names=['BossTimer.cinematic.v1','BossTimer.cinematic.v2','BossTimer.settings.v1','BossTimer.cinematic.v2-other'];
 const entry=()=>({status:200,headers:{},async blob(){bodyReads++;return {size:10};}});
 for(const name of names)stores.set(name,new Map());
 const a='/cinematic/'+'a'.repeat(64)+'.mp4',b='/cinematic/'+'b'.repeat(64)+'.mp4';
 const old=stores.get(names[0]);old.set(a,entry());
 const current=stores.get(names[1]);current.set('/cinematic/removed.mp4',entry());
 const caches={async keys(){if(blocked)throw Error('storage denied');return [...stores.keys()];},
  async delete(name){deleted.push(name);return stores.delete(name);},
  async open(name){if(!stores.has(name))stores.set(name,new Map());const rows=stores.get(name);return {
   match:async url=>rows.get(url),put:async(url,response)=>rows.set(url,response),
   keys:async()=>[...rows.keys()].map(url=>({url:new URL(url,'https://example.test').href})),
   delete:async request=>rows.delete(new URL(request.url).pathname)};}};
 const api=new Function('cacheStorage','fetch','Response','AbortController','URL',`
  const globalThis={caches:cacheStorage},document={hidden:false},location={href:'https://example.test/'};
  let library=[{url:${JSON.stringify(a)}},{url:${JSON.stringify(b)}}];const paintTransport=()=>{};
  const setTimeout=fn=>{fn();return 1;};
  ${code}
  return {...localMedia,setLibrary:rows=>library=rows};
 `)(caches,async(url,options)=>{requests.push({url,cache:options.cache});if(fail)throw Error('offline');return entry();},
  class {constructor(blob,options){this.status=200;this.headers=options.headers;this.blob=async()=>{bodyReads++;return blob;};}},AbortController,URL);
 return {api,stores,current,old,deleted,requests,a,b,fail:value=>fail=value,reads:()=>bodyReads};
}
const f=fixture();
await Promise.all([f.api.blob(f.a),f.api.blob(f.a)]);
check(f.deleted.join()==='BossTimer.cinematic.v1'&&!f.stores.has('BossTimer.cinematic.v1'),'old cinematic cache is deleted on migration');
check(f.stores.has('BossTimer.settings.v1')&&f.stores.has('BossTimer.cinematic.v2-other'),'settings and unrelated caches are preserved');
check(f.requests.length===1&&f.requests[0].cache==='reload','uncached movie is downloaded once with old HTTP cache bypassed');
const before=f.requests.length;await f.api.blob(f.a);
check(f.requests.length===before,'replaying a cached movie makes no download request');
f.api.start();
for(let n=0;n<100&&f.api.ready()!==2;n++)await Promise.resolve();
check(f.api.ready()===2&&f.requests.length===2,'theme warm downloads only remaining movie');
check(!f.current.has('/cinematic/removed.mp4'),'removed movies are pruned from the current cache');
const reads=f.reads();f.api.stop();f.api.start();
for(let n=0;n<100;n++)await Promise.resolve();
check(f.requests.length===2&&f.reads()===reads,'returning to theme neither redownloads nor rereads cached bodies');
f.api.setLibrary([{url:f.a}]);f.api.stop();f.api.start();
for(let n=0;n<100;n++)await Promise.resolve();
check(!f.current.has(f.b)&&f.current.has(f.a),'changed catalogue prunes deleted files without discarding retained movies');
const fallback=fixture(true);await fallback.api.blob(fallback.a);
check(fallback.requests[0].cache==='force-cache','restricted CacheStorage retains existing browser HTTP-cache fallback');
const retry=fixture();retry.fail(true);let rejected=false;
try{await retry.api.blob(retry.a);}catch(_){rejected=true;}
retry.fail(false);await retry.api.blob(retry.a);
check(rejected&&retry.requests.length===2&&retry.current.has(retry.a),'failed download is not recorded as cached and can retry');
return {checks:checks.length};

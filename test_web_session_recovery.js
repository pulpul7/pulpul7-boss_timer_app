/* Offline production request/session checks. No browser or network. */
const checks=[];
function check(value,message) {if(!value) throw Error(message);checks.push(message);}
const section=source.slice(source.indexOf('async function loginExpired('),source.indexOf('async function poll()'));
function response(status,body={}) {
  return {status,ok:status>=200&&status<300,headers:{get:()=>null},
    json:async()=>{if(body instanceof Error) throw body;return body;}};
}
function fixture(replies) {
  const calls=[],redirects=[];
  const fetch=async path=>{calls.push(path);const next=replies.shift();if(next instanceof Error) throw next;return next;};
  const api=new Function('fetch','AbortController','setTimeout','clearTimeout','performance','location',`
    let leaving=false,pollTimer=null,retryWait=0,serverTime=0,received=0,receivedWall=0;
    const instant=Date.parse;
    ${section}
    return {request,leaving:()=>leaving};
  `)(fetch,AbortController,setTimeout,clearTimeout,performance,{replace:path=>redirects.push(path)});
  return {...api,calls,redirects};
}
async function failure(f) {try {await f.request('/api/status');} catch(_) {return;} throw Error('expected failed request');}
for(const status of [429,503]) {
  const f=fixture([response(status,{message:'temporarily unavailable'})]);await failure(f);
  check(!f.leaving()&&f.redirects.length===0&&f.calls.length===1,`${status} keeps the current page and login`);
}
for(const verification of [response(503,{}),response(200,{}),response(200,{available:false,configured:true,authenticated:false}),
    response(403,new Error('router HTML')),new Error('connection lost')]) {
  const f=fixture([response(401,new Error('router HTML')),verification]);await failure(f);
  check(!f.leaving()&&f.redirects.length===0,'unconfirmed rejection cannot redirect to login');
}
const alive=fixture([response(401,{}),response(200,{available:true,configured:true,authenticated:true}),
  response(200,{server_time:'2026-10-16T12:00:00+09:00'})]);
await failure(alive);await alive.request('/api/status');
check(!alive.leaving()&&alive.redirects.length===0,'valid session recovers from an isolated rejected response');
const expired=fixture([response(401,{}),response(200,{available:true,configured:true,authenticated:false})]);
await failure(expired);
check(expired.leaving()&&expired.redirects[0]==='/?login=1','confirmed expired session requests the password');
const blocked=fixture([response(403,{}),response(403,{access_blocked:true})]);await failure(blocked);
check(blocked.leaving()&&blocked.redirects.length===1,'explicit administrator block remains effective');
const offline=fixture([new Error('offline')]);await failure(offline);
check(!offline.leaving()&&offline.redirects.length===0,'network disconnection does not log out');
return checks;

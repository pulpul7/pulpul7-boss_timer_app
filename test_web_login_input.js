/* Offline login input checks; source is the production login.js, no network/storage writes. */
const checks=[];
function check(value,message){if(!value)throw Error(message);checks.push(message);}
for(const [typed,accepted] of [['0123',true],['5678',true],['9999',false]]){
 const handlers={},sent=[],reads=[],writes=[];
 const elements=Object.fromEntries(['pin','enter','notice','nickname','login'].map(id=>[id,{
  value:'',textContent:'',className:'',focus(){},addEventListener(type,callback){handlers[id+':'+type]=callback;}
 }]));
 const document={hidden:false,getElementById:id=>elements[id],addEventListener(){}};
 Object.defineProperty(document,'cookie',{get(){throw Error('login script must not read cookies');}});
 const stale=new Map([['bt_nickname','tester'],['bt_pin','1111'],['bt_password','2222']]);
 const storage={getItem(key){reads.push(key);return stale.get(key)??null;},
  setItem(key,value){writes.push([key,value]);},clear(){throw Error('must not clear storage');}};
 const fetch=async(path,options={})=>{
  if(path==='/api/session')return {ok:true,headers:{get:()=>null},json:async()=>({available:true,configured:true,authenticated:false})};
  check(path==='/api/login','form submits only to the login endpoint');
  sent.push(JSON.parse(options.body));
  return {ok:accepted,status:accepted?200:401,headers:{get:()=>null},
   json:async()=>({message:accepted?'accepted':'password mismatch'})};
 };
 const create=new Function('document','window','fetch','localStorage','location','performance',
  'AbortController','setTimeout','clearTimeout','URLSearchParams',source);
 create(document,{addEventListener(){}},fetch,storage,{search:'',replace(){}},{now:()=>100},
  AbortController,()=>1,()=>{},URLSearchParams);
 for(let n=0;n<8;n++)await Promise.resolve();
 check(!elements.enter.disabled,'session readiness enables the form');
 elements.pin.value=typed;
 elements.nickname.value='  current user  ';
 await handlers['login:submit']({preventDefault(){}});
 check(sent.length===1&&sent[0].pin===typed&&typeof sent[0].pin==='string','current input is sent exactly, retaining leading zero');
 check(sent[0].nickname==='current user','current nickname is independent of stored nickname');
 check(!reads.includes('bt_pin')&&!reads.includes('bt_password'),'stale stored passwords are never read');
 check(!writes.some(([key,value])=>key.includes('password')||key==='bt_pin'||value===typed),'typed password is not persisted');
 check(elements.pin.value==='','input is cleared only after the submitted request completes');
}
return checks;

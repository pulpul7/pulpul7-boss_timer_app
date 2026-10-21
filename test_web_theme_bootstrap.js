/* Execute the actual CSP-approved first-paint script. */
const checks=[];function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
function fixture(theme,marked=false,blocked=false){
 const attrs=new Map([['data-theme','gray'],['data-theme-pink','true'],['data-theme-solid','true']]),meta=new Map();
 const values=new Map([['bt_nickname','user']]);if(theme!==null)values.set('BossTimer.webTheme.v1:user',theme);if(marked)values.set('BossTimer.webTheme.gray.v1:user','1');
 const root={setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k)};
 new Function('localStorage','document',bootstrap)({getItem(k){if(blocked)throw Error('blocked');return values.get(k)??null;},setItem(k,v){values.set(k,v);}},{documentElement:root,querySelector:()=>({setAttribute:(k,v)=>meta.set(k,v)})});
 return{attrs,meta,values};
}
for(const value of [null,'default']){const f=fixture(value);check(f.attrs.get('data-theme')==='gray'&&f.attrs.get('data-theme-solid')==='true','new or previous dark first paint is gray');check(f.meta.get('content')==='#f5f6f7','gray first paint sets browser chrome color');}
const dark=fixture('default',true);check(!dark.attrs.has('data-theme')&&!dark.attrs.has('data-theme-pink')&&!dark.attrs.has('data-theme-solid'),'explicitly selected dark removes static gray attributes');check(dark.meta.get('content')==='#151b24','explicit dark retains dark chrome');
for(const theme of ['sakura','gray','spring','winter','rain','avengers']){const f=fixture(theme);check(f.attrs.get('data-theme')===theme,theme+' first paint preserves existing preference');}
return checks;

/* Offline navigation lifecycle checks. Parameters: source and currentTabSource. */
const checks=[];
function check(value,message){if(!value)throw Error(message);checks.push(message);}
const begin='// BEGIN WEB CURRENT TAB',end='// END WEB CURRENT TAB';
const embedded=source.slice(source.indexOf(begin)+begin.length,source.indexOf(end));
for(const [label,code] of [['readable',currentTabSource],['embedded',embedded]]){
 function fixture(raw,blocked=false){
  let storage=raw,listener;
  const api=new Function('sessionStorage','window',`
   let selectedFilter='all',hiddenMenuCollapsed=true,calls=0;
   function selectFilter(value){if(value==='today')value='custom';if(['all','custom','txt'].includes(value)){selectedFilter=value;hiddenMenuCollapsed=true;calls++;}}
   function toggleHiddenMenuFold(){hiddenMenuCollapsed=false;calls++;}
   ${code}
   return{restore:WebCurrentTab.restore,state:()=>({filter:selectedFilter,theme:!hiddenMenuCollapsed,calls}),select:selectFilter,theme:toggleHiddenMenuFold};
  `)({getItem(){if(blocked)throw Error('blocked');return storage??null;},setItem(k,v){if(blocked)throw Error('blocked');storage=v;}},
  {addEventListener(type,fn){if(type==='pagehide')listener=fn;}});
  return{api,hide:()=>listener(),stored:()=>storage};
 }
 for(const filter of ['all','custom','txt','today']){
  const f=fixture(JSON.stringify({filter,theme:false}));f.api.restore();
  check(f.api.state().filter===(filter==='today'?'custom':filter),label+' restores '+filter);
  f.api.select('all');const calls=f.api.state().calls;f.api.restore();
  check(f.api.state().filter==='all'&&f.api.state().calls===calls,label+' does not restore old state on later payloads '+filter);
 }
 const theme=fixture(JSON.stringify({filter:'txt',theme:true}));theme.api.restore();
 check(theme.api.state().filter==='txt'&&theme.api.state().theme,label+' restores theme with underlying TXT tab');
 theme.api.select('custom');theme.hide();
 check(JSON.stringify(JSON.parse(theme.stored()))===JSON.stringify({filter:'custom',theme:false}),label+' pagehide saves actual tab');
 theme.api.theme();theme.hide();
 check(JSON.parse(theme.stored()).theme===true,label+' pagehide saves open theme');
 for(const raw of [null,'{bad','null','42','"txt"','{"filter":"unknown"}']){
  const bad=fixture(raw);bad.api.restore();
  check(bad.api.state().filter==='all'&&!bad.api.state().theme,label+' invalid/missing state preserves default '+raw);
 }
 const blocked=fixture(null,true);blocked.api.restore();blocked.api.select('txt');blocked.hide();
 check(blocked.api.state().filter==='txt',label+' blocked storage does not break navigation');
}
return checks;

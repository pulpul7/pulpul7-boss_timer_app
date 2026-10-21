/* Gray defaults, one-time dark migration and user-selected dark restoration. */
const checks=[];
const check=(ok,message)=>{if(!ok)throw Error(message);checks.push(message);};
const helper=source.split('// BEGIN WEB THEME PREFERENCE')[1].split('// END WEB THEME PREFERENCE')[0];
function fixture(saved){
 const values=new Map(Object.entries(saved||{}));let writes=0;
 const storage={getItem:k=>values.get(k)??null,setItem(k,v){values.set(k,v);writes++;}};
 const read=new Function('localStorage',helper+';return readThemePreference;')(storage);
 return{values,read,writes:()=>writes};
}
const key=n=>'BossTimer.webTheme.v1:'+n,marker=n=>'BossTimer.webTheme.gray.v1:'+n;
const fresh=fixture();check(fresh.read('guest')==='gray','new visitors use gray');
check(fresh.values.get(key('guest'))==='gray'&&fresh.values.get(marker('guest'))==='1','new default is recorded with a one-time marker');
const dark=fixture({[key('old')]:'default','unrelated':'keep'});check(dark.read('old')==='gray','existing dark migrates to gray');
const count=dark.writes();check(dark.read('old')==='gray'&&dark.writes()===count,'repeat reads do not write preferences');
dark.values.set(key('old'),'default');check(dark.read('old')==='default','explicitly choosing dark after migration remains dark');
check(dark.values.get('unrelated')==='keep','migration preserves unrelated storage');
for(const theme of ['sakura','spring','winter','rain','avengers','sky','wood','green','blue','magenta','ivory','canary','forsythia','purple','gray']){
 const f=fixture({[key('user')]:theme});check(f.read('user')===theme&&f.values.get(key('user'))===theme,theme+' existing selection is preserved');
}
const separate=fixture({[key('one')]:'default',[key('two')]:'spring'});check(separate.read('one')==='gray'&&separate.read('two')==='spring','migration is isolated by nickname');
const blocked=new Function('localStorage',helper+';return readThemePreference;')({getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}});check(blocked('guest')==='gray','blocked storage still defaults to gray');
const order=[...html.matchAll(/id="theme-(default|sakura|gray|sky|wood|green|blue|magenta|ivory|canary|forsythia|purple)"/g)].map(m=>m[1]);
check(order[0]==='gray'&&order[1]==='default'&&order.at(-1)==='sakura','theme controls rotate gray, dark and pink without renaming their identities');
check(html.includes('data-theme="gray" data-theme-pink="true" data-theme-solid="true"'),'static HTML also starts in gray');
const select=html.match(/<select id="menu-mouse-delay">([\s\S]*?)<\/select>/)[1];
check([...select.matchAll(/value="(\d+)"/g)].map(m=>m[1]).join() === '1,3,5,10,15,30,60','all requested hide-delay choices are present');
check(select.includes('value="10" selected'),'distributed dropdown defaults to ten seconds');
return checks;

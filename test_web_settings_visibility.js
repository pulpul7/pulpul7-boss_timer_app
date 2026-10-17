/* Public settings access; nickname artwork and limited theme stay separate. */
const checks=[];
function check(ok,text){if(!ok)throw Error(text);checks.push(text);}
function fixture(name){
  const values=new Map([['bt_nickname',name]]),nodes=new Map();
  const element=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,content:{textContent:'AQ=='},
    setAttribute(k,v){this[k]=v;},getAttribute(k){return this[k];},removeAttribute(k){delete this[k];}});return nodes.get(id);};
  const code=source.slice(source.indexOf('const THEME_EGG_NAMES='),source.indexOf('const SPRING_BACKGROUND_BASE64='))+
    source.slice(source.indexOf('function themeEligible()'),source.indexOf('function themeStorageKey('));
  const api=new Function('element','localStorage',`
    const DEFENDER_EGG_KEY='BossTimer.webEgg.defender.v1';
    const WebBossSpectrum={palettes:{sky:[],spring:[]}},setText=(n,t)=>n.textContent=t;
    const refreshTheme=()=>{},fitHeader=()=>{},toast=()=>{},atob=()=>String.fromCharCode(1);
    const URL={createObjectURL:()=> 'blob:badge'},Blob=class {};
    ${code}
    refreshNickname();return {refresh:refreshNickname,tap:touchNickname,eligible:themeEligible,theme:themeAvailable};`)(
    element,{getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)});
  return {api,values,element};
}
const ordinary=fixture('일반사용자');
check(ordinary.api.eligible(),'every user can open settings without nickname taps');
check(ordinary.api.theme('sky')&&!ordinary.api.theme('spring'),'ordinary themes are public but limited blossom stays restricted');
for(let i=0;i<16;i++)ordinary.api.tap();
check(ordinary.values.size===1&&!ordinary.element('viewer-nickname').hidden,'ordinary nickname taps neither unlock nor hide menus or names');
for(const name of ['수비수','설기','백설기']){
  const user=fixture(name);
  check(!user.element('viewer-nickname').hidden,'artwork still requires its own Easter egg for '+name);
  for(let i=0;i<7;i++)user.api.tap();
  check(!user.element('viewer-nickname').hidden,'seven taps do not reveal artwork for '+name);
  user.api.tap();
  check(user.element('viewer-nickname').hidden&&!user.element('viewer-nickname-badge').hidden,'eight taps reveal the nickname artwork for '+name);
  user.api.refresh();for(let i=0;i<8;i++)user.api.tap();
  check(user.element('viewer-nickname').hidden,'saved artwork stays visible after more taps for '+name);
  check(user.api.theme('spring'),'limited blossom stays available for '+name);
}
check(fixture('나츠').api.theme('spring'),'developer retains limited blossom access');
check(source.includes("element('tab-theme').addEventListener('click',toggleHiddenMenuFold)"),'settings are opened from the public theme tab');
check(!source.includes('hiddenMenuUnlockKey')&&!source.includes('hiddenMenuUnlocked'),'menu access has no remaining unlock state');
return checks;

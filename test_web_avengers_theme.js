/* Exercise theme transitions and image reuse against the production functions. */
const checks=[];
function check(ok,text){if(!ok)throw Error(text);checks.push(text);}
const nodes=new Map(),calls=[];
function element(id){if(!nodes.has(id))nodes.set(id,{hidden:false,attrs:{},setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k]||null;},removeAttribute(k){delete this.attrs[k];}});return nodes.get(id);}
const root=element('root'),meta=element('meta');
const energy=[element('header-energy'),element('hero-energy')];
const doc={documentElement:root,hidden:false,querySelector:()=>meta,querySelectorAll:()=>energy};
const props=new Map(),blobs=[];
root.style={getPropertyValue:k=>props.get(k)||'',setProperty:(k,v)=>props.set(k,v)};
let imageReads=0;
Object.defineProperty(element('avengers-scene'),'src',{set(v){this.attrs.src=v;imageReads++;}});
const start=source.indexOf('function applyTheme()'),end=source.indexOf('function prepareSpringScene()');
const api=new Function('element','document','getComputedStyle','calls','URL','Blob','atob',`
 let selectedTheme='default',nicknameValue='guest',summaryKey='',renderedKey='',data=null;
 const WebHiddenHelp={close(){}},WebNotices={refresh(){}},WebFonts={refreshScope:()=>Promise.resolve()},
  WebBossSpectrum={refresh(){}},WebLimitedSakura={sync:v=>calls.push(['sakura',v])};
 const themeAvailable=v=>['default','avengers','spring','sakura','winter','rain'].includes(v),themeEligible=()=>true,
  refreshHiddenMenuFold=()=>{},prepareSpringScene=()=>{},prepareWinterScene=()=>{},prepareRainScene=()=>{},
  stopRainAudio=()=>{},rainAudio={foreground:true},syncWinterSnow=v=>calls.push(['snow',v]),
  syncRainGlass=v=>calls.push(['glass',v]),syncRainForeground=v=>calls.push(['drops',v]);
 ${source.slice(start,end)}
 return {apply(value){selectedTheme=value;applyTheme();},visibility:syncAvengersMotion};`)(element,
 doc,()=>({getPropertyValue:k=>
  k.endsWith('-image')?'"data:image/jpeg;base64,AQ=="':'"data:image/svg+xml,%3Csvg%2F%3E"'}),calls,
 {createObjectURL:blob=>{blobs.push(blob);return 'blob:https://example.test/'+blobs.length;}},
 class {constructor(parts,options){this.parts=parts;this.type=options.type;}},()=>String.fromCharCode(1));
api.apply('spring');api.apply('avengers');
check(root.attrs['data-theme']==='avengers'&&!root.attrs['data-theme-pink']&&!root.attrs['data-theme-photo'],'Avengers clears pink and photographic blossom flags');
check(root.attrs['data-theme-image']==='true'&&!element('avengers-art').hidden&&element('spring-art').hidden,'only Avengers background is visible');
check(calls.slice(-4).every(([,enabled])=>enabled===false),'switching to Avengers stops all existing ambient animation modules');
check(element('theme-avengers').attrs['aria-pressed']==='true'&&element('theme-default').attrs['aria-pressed']==='false','selected theme state agrees with the UI');
check(imageReads===1&&meta.attrs.content==='#110b09','local scene is prepared once with dark browser chrome');
check(blobs.length===10&&[...props.values()].every(v=>v.startsWith('url("blob:')),'every CSS decoration uses an allowed blob URL instead of a blocked data URL');
check(blobs[0].parts[0][0]===1&&blobs[2].parts[0]==='<svg/>'&&blobs[2].type==='image/svg+xml','raster base64 and SVG percent encoding both decode correctly');
api.apply('default');
check(!root.attrs['data-theme']&&!root.attrs['data-theme-image']&&element('avengers-art').hidden,'dark mode removes the Avengers scene and flags');
check(root.attrs['data-avengers-awake']==='false','leaving the theme pauses reactor energy');
api.apply('winter');
check(calls.slice(-4).find(([type])=>type==='snow')[1]===true,'winter resumes its own animation after Avengers');
api.apply('avengers');api.apply('avengers');
check(imageReads===1&&blobs.length===10,'returning to Avengers reuses all URLs without image or object URL leaks');
check(root.attrs['data-avengers-awake']==='true'&&energy.every(n=>n.attrs['data-in-view']==='true'),'visible foreground reactors can animate without an observer implementation');
doc.hidden=true;api.visibility();
check(root.attrs['data-avengers-awake']==='false','backgrounding the document pauses both reactors');
doc.hidden=false;api.visibility();
check(root.attrs['data-avengers-awake']==='true','returning to the page restores foreground reactor motion');
return checks;

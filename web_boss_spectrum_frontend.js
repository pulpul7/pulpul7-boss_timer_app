/* Theme-specific, developer-owned boss colors. Only one initial read; explicit saves. */
const WebBossSpectrum=(()=>{
const el=id=>document.getElementById(id);
const specs={sakura:{saturation:58,hues:[355,330,283],names:['로즈','분홍','보라']},
spring:{saturation:74,hues:[355,330,308],names:['벚꽃','딸기','자주']},
winter:{saturation:62,hues:[22,36,48],names:['코코아','앰버','골드']},
rain:{saturation:46,hues:[77,132,175],names:['올리브','비취','청록']},
sky:{saturation:60,hues:[190,205,220],names:['아쿠아','하늘','스카이']},
wood:{saturation:48,hues:[20,35,50],names:['월넛','나무','오크']},
green:{saturation:54,hues:[95,125,150],names:['새싹','그린','에메랄드']},
blue:{saturation:68,hues:[205,225,245],names:['오션','블루','인디고']},
magenta:{saturation:72,hues:[292,312,332],names:['퍼플','마젠타','베리']},
ivory:{saturation:30,hues:[30,43,56],names:['크림','아이보리','샌드']},
canary:{saturation:70,hues:[36,46,55],names:['허니','카나리아','크림골드']},
forsythia:{saturation:86,hues:[52,56,60],light:[.98,.97,.955,.94,.925,.91,.89,.87,.85,.83],names:['크림','개나리','레몬']},
purple:{saturation:56,hues:[258,276,292],names:['라벤더','퍼플','플럼']},
gray:{saturation:10,hues:[30,190,225],names:['웜그레이','스톤','쿨그레이']}};
const levels=[{name:'우윳빛',light:.926},{name:'은은한',light:.75},{name:'연한',light:.61},
{name:'부드러운',light:.485},{name:'파스텔',light:.38},{name:'밝은',light:.29},
{name:'선명한',light:.215},{name:'깊은',light:.153},{name:'진한',light:.105},{name:'짙은',light:.069}];
const COLOR_COUNT=30;
function hex(h,s,l) {
s/=100;l/=100;
const a=s*Math.min(l,1-l),f=n=>{
const k=(n+h/30)%12;
return Math.round(255*(l-a*Math.max(-1,Math.min(k-3,9-k,1)))).toString(16).padStart(2,'0');
};
return '#'+f(0)+f(8)+f(4);
}
function luminance(color) {
const channels=[1,3,5].map(at=>parseInt(color.slice(at,at+2),16)/255)
.map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4);
return .2126*channels[0]+.7152*channels[1]+.0722*channels[2];
}
function tone(hue,saturation,light) {
// Equal perceived brightness.
let low=0,high=100;
for(let i=0;i<14;i++) {
const middle=(low+high)/2;
if(luminance(hex(hue,saturation,middle))<light) low=middle;else high=middle;
}
return hex(hue,saturation,(low+high)/2);
}
const palettes=Object.fromEntries(Object.entries(specs).map(([theme,spec])=>[theme,
levels.flatMap((level,i)=>spec.hues.map(hue=>tone(hue,spec.saturation,spec.light?.[i]??level.light)))]));
function colorName(t,index) {return `${t==='forsythia'?'연한':levels[Math.floor(index/3)].name} ${specs[t].names[index%3]}`;}
function ink(color) {
const light=luminance(color);
return (light+.05)/.05>=1.05/(light+.05)?'#000000':'#ffffff';
}
function gradient(color) {
if(!hooks) return '';
if(hooks.theme()==='forsythia') return `linear-gradient(90deg,${color}b3,${color}b3)`;
if(hooks.theme()==='spring') return `linear-gradient(100deg,${color}fc 0%,${color}e6 60%,${color}a8 100%)`;
if(!['winter','rain'].includes(hooks.theme()) || ink(color)!=='#ffffff') return '';
return `linear-gradient(100deg,${color}fc 0%,${color}f7 65%,${color}e6 100%)`;
}
let numbers={},allowed=false,ready=false,busy=false,hooks=null,currentTheme='',number=null,preview=false,paletteOpen=false;
const ids=()=>[...hooks.selected()].filter(id=>hooks.known(id));
const theme=()=>Object.hasOwn(palettes,hooks.theme())?hooks.theme():null;
function assigned(id) {
const t=theme();
if(!t) return 0;
if(allowed && preview && hooks.selected().has(id)) return number || 0;
return numbers[id] || 0;
}
function color(row) {
const index=assigned(hooks.groupId?.(row)) || assigned(row.catalog_id || row.id);
return index?palettes[theme()][index-1]:'';
}
function dirty() {
return Boolean(theme() && preview && number!==null && ids().some(id=>(numbers[id] || 0)!==number));
}
function paintChoices() {
const t=theme();
for(const button of document.querySelectorAll('[data-choice]')) {
const index=assigned(button.dataset.choice),background=index?palettes[t][index-1]:'';
button.classList.toggle('spectrum-choice',Boolean(background));
if(background) {
button.style.setProperty('--boss-spectrum-bg',background);
button.style.setProperty('--boss-spectrum-fill',gradient(background) || background);
button.style.setProperty('--boss-spectrum-ink',ink(background));
} else {
button.style.removeProperty('--boss-spectrum-bg');button.style.removeProperty('--boss-spectrum-ink');
button.style.removeProperty('--boss-spectrum-fill');
}
const badge=button.querySelector('.custom-spectrum-badge');
if(!index || !allowed) {badge?.remove();continue;}
const target=badge || document.createElement('span');
target.className='custom-spectrum-badge';target.textContent=String(index);
target.style.backgroundColor=background;target.title=`${index}번 · ${colorName(t,index-1)}`;
target.style.color=ink(background);
if(!badge) button.append(target);
}
}
function refresh() {
if(!hooks) return;
if(hooks.editing?.()===false) paletteOpen=false;
el('boss-spectrum-tools').hidden=!allowed;
el('boss-spectrum-controls').hidden=!allowed;
if(hooks.editing?.()!==false)paintChoices();
if(!allowed) {el('boss-spectrum-palette').hidden=true;return;}
const t=theme();
if(currentTheme!==t) {
currentTheme=t;
const choices=(palettes[t] || []).map((color,i)=>{
const button=document.createElement('button');button.type='button';
button.dataset.spectrumNumber=String(i+1);
const order=document.createElement('span'),stroke=document.createElement('span'),label=document.createElement('span');
order.className='spectrum-color-number';order.textContent=String(i+1).padStart(2,'0');
stroke.className='spectrum-brush';label.className='spectrum-color-name';label.textContent=colorName(t,i);
stroke.append(label);button.append(order,stroke);
button.style.setProperty('--spectrum-swatch',color);
button.style.setProperty('--spectrum-ink',ink(color));
button.setAttribute('aria-label',`${i+1}번 · ${colorName(t,i)}`);
button.title=`${i+1}번 · ${colorName(t,i)} · ${color}`;return button;
});
el('boss-spectrum-bar').replaceChildren(...choices);
}
el('boss-spectrum-select').disabled=!ready || busy || !t;
el('boss-spectrum-select').title=t?'박스 배경색 번호는 모든 밝은 테마에 공통 적용됩니다.':'다크 테마는 제외됩니다. 밝은 테마를 먼저 선택해주세요.';
el('boss-spectrum-select').setAttribute('aria-expanded',String(Boolean(t && paletteOpen)));
el('boss-spectrum-label').textContent=!t?'다크 제외':number===null?'스펙트럼 선택':number===0?'기존 보스색':`${number} · ${colorName(t,number-1)}`;
el('boss-spectrum-palette').hidden=!(t && paletteOpen);
el('boss-spectrum-reset').disabled=busy;
for(const button of el('boss-spectrum-bar').children) {
const index=Number(button.dataset.spectrumNumber);
button.setAttribute('aria-pressed',String(number===index));button.disabled=busy;
}
el('boss-spectrum-save').disabled=!ready || busy || !dirty();
el('boss-spectrum-save').textContent=busy?'저장 중':'배경 저장';
const swatch=el('boss-spectrum-swatch');
swatch.style.backgroundColor=t && number?palettes[t][number-1]:'var(--muted)';
swatch.title=t && number?`${number}번 · ${palettes[t][number-1]}`:'스펙트럼을 선택하세요.';
}
async function load() {
const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
try {
const response=await fetch('/api/boss-spectrum',{cache:'no-store',signal:controller.signal});
if(!response.ok) return;
const data=await response.json();
numbers=read(data);allowed=data.can_save===true;ready=true;
refresh();hooks.repaint();
} catch(_) { /* A color module failure never blocks schedules. */ }
finally {clearTimeout(timer);}
}
function clean(value) {
const result={};
if(!value || typeof value!=='object' || Array.isArray(value)) return result;
return Object.fromEntries(Object.entries(value).slice(0,4000).filter(([id,index])=>
/^[0-9a-f]{24}$/.test(id) && Number.isInteger(index) && index>=1 && index<=COLOR_COUNT));
}
function read(data) {
if(data.schema===2) return clean(data.numbers);
const result={};
for(const [t,entries] of Object.entries(data.themes || {})) if(Object.hasOwn(palettes,t)) Object.assign(result,clean(entries));
return result;
}
async function save() {
if(!allowed || !ready || busy || !dirty()) return;
const job={theme:theme(),bosses:ids(),number},controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
busy=true;refresh();
try {
const response=await fetch('/api/boss-spectrum',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify(job),signal:controller.signal});
const result=await response.json();
if(!response.ok) {
if(response.status===403) allowed=false;
throw new Error(result.message || '색상을 저장하지 못했습니다.');
}
numbers=read(result);
if(number===job.number && ids().sort().join(',')===job.bosses.sort().join(',')) preview=false;
hooks.note(result.message);hooks.repaint();
} catch(error) {hooks.note(error.name==='AbortError'?'저장 응답이 늦습니다. 새로고침으로 저장 여부를 확인해주세요.':error.message || '색상을 저장하지 못했습니다.');}
finally {clearTimeout(timer);busy=false;refresh();}
}
function init(options) {
hooks=options;
if(!el('boss-spectrum-controls')) return;
el('boss-spectrum-select').addEventListener('click',()=>{
if(!allowed || busy || !theme()) return;
paletteOpen=!paletteOpen;refresh();
});
el('boss-spectrum-select').addEventListener('keydown',event=>{
if(event.key!=='ArrowDown' || !allowed || busy || !theme()) return;
event.preventDefault();paletteOpen=true;refresh();
const button=el('boss-spectrum-bar').children[Math.max(0,(number || 1)-1)];
button?.focus({preventScroll:true});button?.scrollIntoView({block:'nearest'});
});
function choose(value) {
if(!allowed || busy) return;
if(!theme() || !Number.isInteger(value) || value<0 || value>COLOR_COUNT) return;
number=value;preview=true;paletteOpen=false;
refresh();hooks.repaint();
el('boss-spectrum-select').focus({preventScroll:true});
}
el('boss-spectrum-bar').addEventListener('click',event=>{
const button=event.target.closest('[data-spectrum-number]');
if(button) choose(Number(button.dataset.spectrumNumber));
});
el('boss-spectrum-reset').addEventListener('click',()=>choose(0));
el('boss-spectrum-bar').addEventListener('keydown',event=>{
if(event.key==='Escape') {paletteOpen=false;refresh();el('boss-spectrum-select').focus({preventScroll:true});return;}
if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
const buttons=[...el('boss-spectrum-bar').children],index=buttons.indexOf(document.activeElement);
if(index<0) return;
event.preventDefault();
const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:
(index+(['ArrowRight','ArrowDown'].includes(event.key)?1:-1)+buttons.length)%buttons.length;
buttons[next].focus({preventScroll:true});buttons[next].scrollIntoView({block:'nearest',inline:'nearest'});
});
el('boss-spectrum-save').addEventListener('click',save);
document.addEventListener('click',event=>{
if(paletteOpen && !el('boss-spectrum-palette').contains(event.target) && !el('boss-spectrum-select').contains(event.target)) {
paletteOpen=false;refresh();
}
});
document.addEventListener('keydown',event=>{
if(event.key==='Escape' && paletteOpen) {paletteOpen=false;refresh();el('boss-spectrum-select').focus({preventScroll:true});}
});
load();
}
return {init,refresh,color,palettes,ink,gradient};
})();

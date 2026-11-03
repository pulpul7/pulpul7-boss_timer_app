/* Shared picker DOM, independent personal and color selections. */
globalThis.WebCustomEditor=(()=>{
let hooks,allowed=false,coloring=false,picked=new Set();
const el=id=>document.getElementById(id);
const active=()=>allowed&&coloring;
function refresh(canSave,editing){
allowed=canSave;
if(!allowed||!editing)coloring=false;
el('custom-color-tab').hidden=!allowed;
for(const mode of ['selection','color']){
const selected=(mode==='color')===active(),button=el(`custom-${mode}-tab`);
button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;
el(`custom-${mode}-panel`).hidden=!selected;
}
const groups=el('custom-groups'),host=el(active()?'custom-color-host':'custom-selection-host');
if(groups.parentElement!==host)host.append(groups);
el('custom-color-count').textContent=`색지정 ${[...picked].filter(id=>hooks.known(id)).length}개`;
}
function select(mode){
if(hooks.busy())return;
coloring=mode==='color'&&allowed;
hooks.switched();
}
function change(update){
if(!active()||hooks.busy())return;
const next=new Set(picked);update(next);picked=next;
hooks.repaint();
}
function init(options){
hooks=options;
const mount=el('spectrum-mount');
if(mount)mount.innerHTML=`<div id="boss-spectrum-tools" class="custom-spectrum-tools" hidden><div id="boss-spectrum-controls" class="boss-spectrum-controls" hidden><button id="boss-spectrum-select" aria-controls="boss-spectrum-palette" aria-expanded="false"><span id="boss-spectrum-swatch" class="boss-spectrum-swatch" aria-hidden="true"></span><span id="boss-spectrum-label">스펙트럼 선택</span></button><button id="boss-spectrum-save" disabled>배경 저장</button></div><div id="boss-spectrum-palette" class="boss-spectrum-palette" hidden><div id="boss-spectrum-bar" class="boss-spectrum-bar" role="group" aria-label="밝은 테마 공통 박스 배경색 1~30번"></div><button id="boss-spectrum-reset">기존 보스색</button></div></div>`;

for(const mode of ['selection','color']){
el(`custom-${mode}-tab`).addEventListener('click',()=>select(mode));
el(`custom-${mode}-tab`).addEventListener('keydown',event=>{
if(!allowed||hooks.busy()||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
event.preventDefault();const target=event.key==='Home'?'selection':event.key==='End'?'color':mode==='color'?'selection':'color';
select(target);el(`custom-${target}-tab`).focus({preventScroll:true});
});
}
el('custom-color-all').addEventListener('click',()=>change(set=>hooks.catalog().forEach(row=>set.add(row.id))));
el('custom-color-clear').addEventListener('click',()=>change(set=>set.clear()));
}
return{init,refresh,active,change,selection:fallback=>active()?picked:fallback};
})();

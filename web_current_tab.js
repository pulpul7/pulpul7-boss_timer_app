// Per-window navigation state; schedule data and personal settings stay separate.
const WebCurrentTab=(()=>{
  const key='BossTimer.webCurrentTab.v1';
  let saved,restored=false;
  try { saved=JSON.parse(sessionStorage.getItem(key)); } catch(_) {}
  window.addEventListener('pagehide',()=>{
    try {sessionStorage.setItem(key,JSON.stringify({filter:selectedFilter,theme:!hiddenMenuCollapsed}));} catch(_) {}
  });
  return {restore(){
    if(restored)return;
    restored=true;
    const value=saved;saved=null;
    const valid=value && ['all','custom','txt','today'].includes(value.filter);
    const first=scheduleTabs().find(button=>['all','custom','txt'].includes(button.dataset.filter));
    selectFilter(valid?value.filter:first?.dataset.filter || 'all');
    if(valid && value.theme===true)toggleHiddenMenuFold();
  }};
})();

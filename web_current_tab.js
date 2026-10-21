// Per-window navigation state; schedule data and personal settings stay separate.
const WebCurrentTab=(()=>{
  const key='BossTimer.webCurrentTab.v1';
  let saved;
  try { saved=JSON.parse(sessionStorage.getItem(key)); } catch(_) {}
  window.addEventListener('pagehide',()=>{
    try {sessionStorage.setItem(key,JSON.stringify({filter:selectedFilter,theme:!hiddenMenuCollapsed}));} catch(_) {}
  });
  return {restore(){
    const value=saved;saved=null;
    if(!value || typeof value!=='object')return;
    selectFilter(value.filter);
    if(value.theme===true)toggleHiddenMenuFold();
  }};
})();

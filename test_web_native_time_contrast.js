/* Exercise production native-card time contrast with the real luminance helper. */
const passed=[];
function check(value,message){if(!value)throw Error(message);passed.push(message);}
const moduleCode=source.slice(source.indexOf('// BEGIN WEB BOSS SPECTRUM MODULE'),source.indexOf('// END WEB BOSS SPECTRUM MODULE'));
const spectrum=new Function(moduleCode+';return WebBossSpectrum;')();
const code=source.slice(source.indexOf('function backgroundOf('),source.indexOf('function bossKey('));
const apply=new Function('WebBossSpectrum','isBoss',code+';return applyBackground;')(
  {color:()=>'',ink:spectrum.ink,gradient:()=>''},row=>row.kind==='boss');
function card(background) {
  const values=new Map(),target={classList:{toggle(){}},style:{
    setProperty:(key,value)=>values.set(key,value),removeProperty:key=>values.delete(key)}};
  apply(target,{kind:'boss',bg_color:background});return {target,values};
}
for(const background of ['#adff2f','#b7e67f','#ffffff'])
  check(card(background).values.get('--boss-time-ink')==='#000000','dark theme uses black time ink on '+background);
for(const background of ['#151b24','#004b22','#000000'])
  check(card(background).values.get('--boss-time-ink')==='#ffffff','dark cards retain white time ink on '+background);
check(card('').values.get('--boss-time-ink')==='#ffffff','missing boss background uses the existing white fallback');
const reused=card('#adff2f');apply(reused.target,null);
check(reused.values.get('--boss-time-ink')==='#ffffff','a reused empty card does not retain black time ink');
check(css.includes('html:not([data-theme]) .schedule-app :is(.hero,.following,.event-row:not(.elapsed)) :is(time,.hero-countdown,.following-state,.event-state)'),
  'time contrast is scoped to dark-theme future schedule cards');
return passed;

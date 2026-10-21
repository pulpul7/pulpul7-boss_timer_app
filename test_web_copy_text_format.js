/* Actual shared formatter: notice heading, stars without dividers, later dates. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
const section=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b));
const option={checked:false};
const format=new Function('element',`
const visibleRows=()=>[],serverNow=()=>0,localDate=t=>new Date(t),
 dayKey=t=>new Date(t).toISOString().slice(0,10),clock=t=>new Date(t).toISOString().slice(11,19);
${section('function bossViewLabel(', 'function bossName(')}
${section('function shareText(', 'function filename(')}
return shareText;
`)(()=>option);
const row=(name,time,extra={})=>({name,at:Date.parse('2026-10-21T'+time+'Z'),kind:'boss',...extra});
const normal=[row('탕그리스니르','04:22:00',{last_hit:true}),row('샤무크','09:55:00',{last_hit:true})];
check(format(normal)==='▣ 보스 타임테이블 공지(수요일)\n\n04:22 탕그리스니르(막)\n09:55 샤무크(막)','normal text matches the requested heading, blank line and compact last-hit markers');
const stars=[row('최하층 강글','07:55:00',{important:true}),row('히로킨','08:22:00',{important:true})];
check(format(stars)==='▣ 보스 타임테이블 공지(수요일)\n\n07:55 ☆최하층 강글\n08:22 ☆히로킨','starred bosses retain hollow stars without divider lines');
const mixed=format([normal[0],...stars,normal[1]]);
check(mixed.includes('탕그리스니르(막)\n07:55 ☆')&&mixed.includes('☆히로킨\n09:55 샤무크(막)'),'mixed bosses have no extra lines around starred bosses');
const separate=format([stars[0],normal[1],stars[1]]).split('\n');
check(separate.every(line=>!/^\-+$/.test(line)),'separated starred bosses do not add dividers');
const tomorrow={...stars[1],at:Date.parse('2026-10-22T08:22:00Z')};
check(format([stars[0],tomorrow]).includes('07:55 ☆최하층 강글\n\n10월 22일 목요일 ======\n08:22 ☆히로킨'),'starred bosses retain six equals after the following-day heading');
check(format([normal[0],{...tomorrow,important:false}]).includes('\n\n10월 22일 목요일 ======\n08:22 히로킨'),'following-day date has one space and six equals');
check(format([normal[0],{...tomorrow,at:Date.parse('2026-10-23T08:22:00Z')}]).includes('10월 23일 금요일 ======\n'),'later dates match the requested Friday example');
option.checked=true;
check(format(stars).includes('07:55:00 ☆최하층 강글'),'seconds option remains effective');
check(format([row('막 보스 (막)','10:00:00',{important:true,last_hit:true})]).includes('☆막 보스(막)'),'existing last-hit suffix is not duplicated');
check(format([])===''&&format([{kind:'break',name:'rest',at:0}])==='','empty or break-only selections stay empty');
check(format([row('fixed major','11:00:00',{kind:'fixed',major:true,important:false})]).includes('11:00:00 fixed major')&&!format([row('fixed major','11:00:00',{kind:'fixed',major:true})]).includes('----------'),'major classification alone does not create a starred run');
check(!format(stars).includes('----------'),'no ten-hyphen separators remain');
return checks;

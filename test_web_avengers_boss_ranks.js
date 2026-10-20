/* Use the production major-tab classification for labels and row artwork. */
const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
const logic=source.slice(source.indexOf('function majorBoss('),source.indexOf('function matchesFilter('));
const rank=source.slice(source.indexOf('function bossRank('),source.indexOf('function colorOf('));
const classify=new Function(`const isBoss=row=>['boss','fixed','invasion'].includes(row.kind);${logic}${rank};return bossRank;`)();
for(const [row,expected,label] of [
 [{name:'Regular',kind:'boss',important:false},'','regular boss retains pale-yellow name'],
 [{name:'Star',kind:'boss',important:true},'major','star boss gets major classification'],
 [{name:'Fixed',kind:'fixed',major:false},'fixed','ordinary fixed boss gets arc classification'],
 [{name:'Fixed major',kind:'fixed',major:true},'major','major fixed boss takes red precedence'],
 [{name:'Guild',kind:'fixed',important:true},'major','starred fixed boss is major'],
 [{name:'길드던전',kind:'fixed'},'major','canonical guild event uses existing major fallback'],
 [{name:'지옥성채정예',kind:'fixed',major:true},'fixed','existing excluded fixed event keeps canonical behavior'],
 [{name:'휴식',kind:'break',major:true},'','rest never gets boss artwork'],
 [{name:'Maintenance',kind:'maintenance'},'','maintenance retains its own style']
])check(classify(row)===expected,label);
return {checks:checks.length};

/* Copy range snapshots and per-button one-second cooldown, without a browser. */
const checks=[],check=(ok,message)=>{if(!ok)throw Error(message);checks.push(message);};
const embedded=source.split('// BEGIN WEB COPY TEXT')[1].split('// END WEB COPY TEXT')[0];
for(const [label,code] of [['readable',copySource],['embedded',embedded]]){
 let now=Date.parse('2026-10-21T10:00:00+09:00'),pending=null,denied=false,writes=[],notices=[],capture;
 const timers=[],nodes=new Map(),data={window_start:'2026-10-20T00:00:00+09:00',window_end:'2026-11-10T23:59:59+09:00'};
 const element=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'tomorrow-8',open:false,disabled:false,before(n){this.previous=n;},closest(selector){return selector.split(',').includes('#'+this.id)?this:null;}});return nodes.get(id);};
 const context=new Function('hooks','data','element','document','navigator','setTimeout',`
 const serverNow=()=>hooks.now(),instant=Date.parse,pad=n=>String(n).padStart(2,'0'),offsetOf=()=>540;
 ${source.slice(source.indexOf('function localDate('),source.indexOf('function contextTitle('))}
 ${source.slice(source.indexOf('function periodEnd('),source.indexOf('function included('))}
 const shareText=rows=>'rows:'+rows,visibleRows=(now,sharing,period)=>period||element('period').value;
 const node=(tag,text)=>({tag,text}),toast=(...parts)=>hooks.toast(parts),openCopyDialog=()=>element('copy-dialog').open=true;
 ${code}
 return{copy:copyText,until:copyUntil};
 `)({now:()=>now,toast:parts=>notices.push(parts)},data,element,
 {addEventListener(type,fn,phase){check(type==='click'&&phase===true,label+' uses capture to block an externally reenabled button');capture=fn;},createElement:()=>({setAttribute(){},focus(){},select(){},addEventListener(){}})},
 {clipboard:{writeText:async value=>{writes.push(value);if(denied)throw Error('denied');if(pending)await pending;}}},(fn,delay)=>timers.push({fn,delay}));
 const expect=(ok,message)=>check(ok,label+': '+message),success=()=>notices.at(-1);
 await context.copy();expect(success()[0].tag==='strong'&&success()[0].text==='내일 8시'&&success()[1]==='까지 복사됐습니다.','default copy reports next-day eight in a strong node');
 now=Date.parse('2026-10-21T18:00:00+09:00');await context.copy();expect(success()[0].text==='내일 8시','new default remains tomorrow08:00 even in the evening');
 element('period').value='default';await context.copy();expect(success()[0].text==='모레 8시','renamed old default reports day-after-tomorrow08:00');element('period').value='tomorrow-8';
 await context.copy('tomorrow-end');expect(success()[0].text==='내일 23시 59분 59초','right copy reports the full next-day end');
 element('period').value='24';now=Date.parse('2026-10-21T12:34:56+09:00');await context.copy();expect(success()[0].text==='내일 12시 34분 56초','rolling24-hour period keeps its actual minutes and seconds');
 element('period').value='48';await context.copy();expect(success()[0].text==='모레 12시 34분 56초','rolling48-hour period is reported independently');
 data.window_end='2026-10-21T15:00:00+09:00';await context.copy('tomorrow-end');expect(success()[0].text==='오늘 15시','available schedule end clamps the notice');
 data.window_end='2026-11-10T23:59:59+09:00';element('period').value='all';await context.copy();expect(success()[0].text.startsWith('11월 10일'),'distant all-period cutoff includes an absolute date');
 const copiedEnd=Date.parse('2026-10-22T23:59:59.999+09:00');now=Date.parse('2026-10-22T01:00:00+09:00');await context.copy('tomorrow-end','visible TXT snapshot',copiedEnd);
 expect(writes.at(-1)==='visible TXT snapshot'&&success()[0].text==='오늘 23시 59분 59초','TXT preserves its displayed string and captured cutoff even after midnight');
 now=Date.parse('2026-10-21T16:59:59+09:00');element('period').value='tomorrow-8';let resolve;pending=new Promise(r=>resolve=r);const waiting=context.copy();now+=2000;resolve();await waiting;pending=null;
 expect(success()[0].text==='내일 8시','permission delay across17:00 never changes the copied range');
 denied=true;await context.copy('tomorrow-end','manual snapshot');expect(element('copy-events').previous.value==='manual snapshot'&&typeof success()[0]==='string','denied clipboard uses the same text and never emits a success notice');denied=false;
 const first=element('copy-left'),other=element('copy-right');let stopped=false;
 capture({target:first,stopImmediatePropagation(){stopped=true;}});expect(first.disabled&&!stopped&&timers.at(-1).delay===1000,'first click disables only its button for1000ms without stopping that click');
 first.disabled=false;capture({target:first,stopImmediatePropagation(){stopped=true;}});expect(stopped&&first.disabled&&timers.length===1,'rapid duplicate is blocked even if another render reenables the button');
 stopped=false;capture({target:other,stopImmediatePropagation(){stopped=true;}});expect(other.disabled&&!stopped&&timers.length===2,'another copy button keeps its independent cooldown');
 timers[0].fn();expect(!first.disabled&&other.disabled,'after one second only the first button is reenabled');
 for(const id of ['txt-copy','text-copy']){const button=element(id);capture({target:button,stopImmediatePropagation(){}});expect(button.disabled&&timers.at(-1).delay===1000,id+' also uses the same one-second cooldown');timers.at(-1).fn();}
}
return checks;

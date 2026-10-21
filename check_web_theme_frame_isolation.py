"""Headless local fixture: retained cinematic UI and frame bounds across themes.

No live service, user profile, movie downloads, install or deployment.
--restricted-runner affects only this disposable Chrome visiting loopback.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

from check_web_avengers_browser import create_fixture_server
from check_web_pwa_browser import Cdp
from web_cinematic import cinematic_response


def check(restricted=False,phone_menu=False,mouse_menu=False,frame_height=False,chat_preview=''):
    def no_movies(path, *args):
        if path=='/cinematic/catalog.json':return 200,b'{"clips":[]}', 'application/json', ()
        return cinematic_response(path,*args)
    with patch('web_cinematic.cinematic_response', side_effect=no_movies):
        server=create_fixture_server()
        try:
            with tempfile.TemporaryDirectory(prefix='boss-theme-frame-unit-') as temporary:
                folder=Path(temporary)
                chrome=Path(os.environ.get('PROGRAMFILES',r'C:\Program Files'))/'Google/Chrome/Application/chrome.exe'
                args=[str(chrome),'--headless=new','--remote-debugging-port=0',f'--user-data-dir={folder}',
                      '--no-first-run','--no-default-browser-check','--disable-default-apps','--disable-extensions',
                      '--disable-background-networking','--disable-component-update','--disable-gpu','about:blank']
                if restricted:args[1:1]=['--in-process-gpu','--no-sandbox']
                process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                cdp=None
                try:
                    deadline=time.monotonic()+15
                    while True:
                        try:
                            port,endpoint=(folder/'DevToolsActivePort').read_text().splitlines()[:2]
                            break
                        except (OSError,ValueError):
                            if time.monotonic()>deadline:raise RuntimeError('Chrome startup timeout')
                            time.sleep(.05)
                    cdp=Cdp(f'ws://127.0.0.1:{port}{endpoint}')
                    target=next(v for v in cdp.call('Target.getTargets')['targetInfos'] if v['type']=='page')
                    session=cdp.call('Target.attachToTarget',dict(targetId=target['targetId'],flatten=True))['sessionId']
                    def evaluate(source):
                        reply=cdp.call('Runtime.evaluate',dict(expression=source,awaitPromise=True,returnByValue=True),session)
                        if reply.get('exceptionDetails'):raise RuntimeError(str(reply['exceptionDetails']))
                        return reply['result'].get('value')
                    cdp.call('Emulation.setDeviceMetricsOverride',dict(width=390,height=844,deviceScaleFactor=1,mobile=True),session)
                    if phone_menu:
                        cdp.call('Emulation.setUserAgentOverride',dict(userAgent='Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Mobile Safari/537.36'),session)
                    cdp.call('Page.addScriptToEvaluateOnNewDocument',dict(source="localStorage.setItem('BossTimer.webFixed.tabs.v1',JSON.stringify({all:true,custom:false}));"),session)
                    cdp.call('Page.navigate',dict(url=f'http://127.0.0.1:{server.server_port}/preview'),session)
                    while not evaluate("!!document.querySelector('#events .following')&&!!document.querySelector('.ironman-cinematic-stage')"):
                        if time.monotonic()>deadline:raise RuntimeError('Fixture did not render: '+str(evaluate("({errors:window.previewErrors,events:document.getElementById('events')?.innerHTML.slice(0,150),clock:document.getElementById('clock')?.textContent,theme:document.documentElement.dataset.theme,cinematic:!!window.WebIronmanCinematic})")))
                        time.sleep(.1)
                    defaults=evaluate("""(()=>{
 const period=document.getElementById('period');return{legacy:document.getElementById('copy-legacy-txt').checked,period:period.value,first:period.options[0].value,label:period.options[0].textContent,oldLabel:period.querySelector('[value="default"]').textContent};
})()""")
                    assert defaults==dict(legacy=True,period='tomorrow-8',first='tomorrow-8',label='내일 아침 8시까지',oldLabel='모레 아침 8시까지'),defaults
                    print(json.dumps(dict(copy_release_defaults=defaults)))
                    copy_layout=[]
                    for width in [320,390,1280]:
                        cdp.call('Emulation.setDeviceMetricsOverride',dict(width=width,height=900,deviceScaleFactor=1,mobile=width<600),session)
                        layout=evaluate("""(()=>{
 document.getElementById('copy-settings').click();
 const period=document.getElementById('period').getBoundingClientRect(),actions=[...document.querySelectorAll('.copy-actions button')].map(b=>b.getBoundingClientRect());
 const label=document.getElementById('copy-legacy-txt').parentElement.textContent.trim();
 document.getElementById('copy-close').click();
 return{label,below:actions.length===3&&actions.every(r=>r.top>=period.bottom),overflow:document.documentElement.scrollWidth>innerWidth};
})()""")
                        assert layout==dict(label='공지용 스케쥴 TXT',below=True,overflow=False),(width,layout)
                        copy_layout.append(dict(width=width,**layout))
                    print(json.dumps(dict(copy_dialog_layout=copy_layout)))
                    reloads=[]
                    for tab in ['custom','txt','theme','all']:
                        evaluate("document.getElementById('tab-'+"+json.dumps(tab)+").click()")
                        cdp.call('Page.reload',{},session)
                        ready=time.monotonic()+12
                        while not evaluate("typeof data!=='undefined'&&!!data&&!!document.querySelector('.ironman-cinematic-stage')"):
                            if time.monotonic()>ready:raise RuntimeError('Reload fixture timeout')
                            time.sleep(.05)
                        state=evaluate("({filter:selectedFilter,theme:!document.getElementById('theme-panel').hidden,txt:!document.getElementById('txt-panel').hidden,selected:document.querySelector('.schedule-tabs [aria-selected=\"true\"]')?.id,errors:window.previewErrors||[]})")
                        assert state['selected']=='tab-'+tab and not state['errors'],(tab,state)
                        assert state['theme']==(tab=='theme'),(tab,state)
                        reloads.append(dict(tab=tab,**state))
                    print(json.dumps(dict(current_tab_reload=reloads)))
                    # Later assertions inject short-lived schedules. Keep the fixture's
                    # normal 15s status poll from replacing those schedules mid-check.
                    evaluate("""(async()=>{
 while(polling)await new Promise(r=>setTimeout(r,20));
 clearTimeout(pollTimer);pollTimer=null;nextPollAt=performance.now()+600000;
})()""")
                    header_sizes=[]
                    for width,height in [(320,760),(390,844),(500,760),(1280,900)]:
                        cdp.call('Emulation.setDeviceMetricsOverride',dict(width=width,height=height,deviceScaleFactor=1,mobile=width<600),session)
                        header=evaluate("""(async()=>{
 const out=[];
 for(const theme of ['gray','sakura','avengers']){
 document.getElementById('theme-'+theme).click();
 for(const tab of ['all','custom']){
 selectFilter(tab);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 const box=id=>{const r=document.getElementById(id).getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};};
 const button=document.getElementById('custom-open'),r=button.getBoundingClientRect(),a=document.getElementById('all-alarm').getBoundingClientRect(),clock=document.querySelector('.clock-controls').getBoundingClientRect();
 out.push({theme,tab,heading:box('frame-heading'),date:document.querySelector('.date-clock').getBoundingClientRect().toJSON(),clock:box('clock'),button:box('custom-open'),alarm:box('all-alarm'),contained:tab!=='custom'||r.left>=clock.left&&r.right<=clock.right&&r.top>=clock.top&&r.bottom<=clock.bottom,noOverlap:tab!=='custom'||r.right<=a.left||r.left>=a.right||r.bottom<=a.top||r.top>=a.bottom,noOverflow:document.documentElement.scrollWidth<=innerWidth,font:getComputedStyle(button).fontSize});
 }}return out;
})()""")
                        header_sizes.append(dict(width=width,rows=header))
                        assert all(r['contained'] and r['noOverlap'] and r['noOverflow'] for r in header),(width,header)
                        assert all(r['button']['height']<=24.1 and r['button']['width']<=59 for r in header if r['tab']=='custom'),(width,header)
                        if width>=500:
                            for theme in ['gray','sakura','avengers']:
                                if width<900 and theme=='avengers':continue
                                pair=[r['heading']['height'] for r in header if r['theme']==theme]
                                assert abs(pair[0]-pair[1])<=1,(width,theme,pair)
                    print(json.dumps(dict(major_header_sizes=header_sizes)))
                    cdp.call('Emulation.setDeviceMetricsOverride',dict(width=390,height=844,deviceScaleFactor=1,mobile=True),session)
                    bounds=evaluate("""(()=>{
 const out=[],before=data;
 for(const theme of ['gray','sakura','default','avengers'])for(const tab of ['custom','txt']){
 document.getElementById('theme-'+theme).click();selectFilter(tab);
 accept({...before,events:[],active:[],schedule_version:'empty-'+theme+tab});
 for(const idle of [false,true]){
 document.documentElement.classList.toggle('menus-idle',idle);
 const app=document.querySelector('.schedule-app'),style=getComputedStyle(app);
 const sample={theme,tab,idle,height:innerHeight,scroll:document.documentElement.scrollHeight,body:document.body.offsetHeight,minHeight:getComputedStyle(document.body).minHeight,padding:parseFloat(style.paddingBottom)};
 const extra=document.createElement('div');extra.style.height='1200px';document.getElementById(tab==='txt'?'txt-panel':'schedule-panel').append(extra);
 sample.longScroll=document.documentElement.scrollHeight;sample.longBottom=app.getBoundingClientRect().bottom+scrollY;extra.remove();out.push(sample);
 }document.documentElement.classList.remove('menus-idle');
 }
 accept(before);selectFilter('all');return out;
})()""")
                    print(json.dumps(dict(short_view_bounds=bounds)))
                    assert all(v['scroll']<=v['height']+4 for v in bounds),bounds
                    assert all(v['minHeight']=='0px' and v['body']<v['height'] for v in bounds),bounds
                    assert all(v['longScroll']>v['height'] and abs(v['longBottom']-v['longScroll'])<=2 for v in bounds),bounds
                    assert all(v['padding']==8 for v in bounds if v['tab']=='txt'),bounds
                    tabs=evaluate("""(()=>{
 const ids=[...document.querySelectorAll('[data-filter]')].map(n=>n.id);
 const custom=document.getElementById('tab-custom');custom.click();
 const personal=custom.textContent==='주요보스'&&custom.getAttribute('aria-selected')==='true'
   &&document.getElementById('schedule-panel').getAttribute('aria-labelledby')==='tab-custom';
 const migrated=JSON.parse(localStorage.getItem('BossTimer.webCustom.v1:오9:딸기'))?.schema===2;
 const fixed=document.getElementById('fixed-filter').textContent==='고정 ON'
   &&JSON.parse(localStorage.getItem('BossTimer.webFixed.tabs.v1'))?.custom===true;
 document.getElementById('custom-open').click();
 const editor=!document.getElementById('custom-editor').hidden;
 document.getElementById('custom-close').click();document.getElementById('tab-all').click();
 return {ids,personal,editor,migrated,fixed,errors:window.previewErrors||[]};
})()""")
                    assert tabs['ids']==['tab-all','tab-custom','tab-txt'] and all(tabs[k] for k in ['personal','editor','migrated','fixed']) and not tabs['errors'],tabs
                    print(json.dumps(dict(schedule_tabs=tabs)))
                    cdp.call('Emulation.setDeviceMetricsOverride',dict(width=320,height=480,deviceScaleFactor=1,mobile=True),session)
                    picker=evaluate("""(async()=>{
 const before=data,ids=['a','b','c','d'].map(c=>c.repeat(24));
 const names=['길던','사용자이벤트','체크된침공','침공'];
 const special=names.map((name,i)=>({id:ids[i],name,label:i===3?'전체침공':name,kind:'boss',group:'특수 이벤트'}));
 accept({...before,catalog_version:'picker-special',boss_catalog:[...before.boss_catalog,...special]});
 const wait=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 const samples=[];
 for(const theme of ['avengers','default','gray','sakura']){
 document.getElementById('tab-theme').click();document.getElementById('theme-'+theme).click();
 document.getElementById('tab-custom').click();document.getElementById('custom-open').click();
 document.documentElement.style.setProperty('--web-font-size-offset','6px');
 const button=document.querySelector('[data-choice="'+ids[2]+'"]');
 if(button.getAttribute('aria-pressed')!=='true')button.click();
 await wait();window.scrollTo(0,document.documentElement.scrollHeight);await wait();button.scrollIntoView({block:'nearest'});await wait();
 const rect=button.getBoundingClientRect(),bar=document.querySelector('.custom-savebar').getBoundingClientRect();
 const style=getComputedStyle(button),compact=theme==='avengers'?style.minHeight==='48px'&&style.paddingTop==='9px':style.minHeight==='40px'&&style.paddingTop==='6px';
 samples.push({theme,width:innerWidth,compact,checked:button.getAttribute('aria-pressed')==='true',userOff:document.querySelector('[data-choice="'+ids[1]+'"]').getAttribute('aria-pressed')==='false',mark:getComputedStyle(button,'::before').content.includes('✓'),uncovered:rect.top>=0&&rect.bottom<bar.top,hit:document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2)?.closest('[data-choice]')===button,rect:rect.toJSON(),bar:bar.toJSON()});
 document.getElementById('custom-close').click();
 }
 document.documentElement.style.removeProperty('--web-font-size-offset');
 document.getElementById('custom-undo').click();document.getElementById('tab-all').click();accept(before);document.getElementById('tab-theme').click();document.getElementById('theme-avengers').click();document.getElementById('tab-all').click();window.scrollTo(0,0);
 return samples;
})()""")
                    assert all(all(s[k] for k in ['compact','checked','userOff','mark','uncovered','hit']) for s in picker),picker
                    print(json.dumps(dict(custom_picker=picker)))
                    cdp.call('Emulation.setDeviceMetricsOverride',dict(width=390,height=844,deviceScaleFactor=1,mobile=True),session)
                    evaluate("""window.previewCopy=async target=>{
 const button=typeof target==='string'?document.getElementById(target):target,until=performance.now()+3000;
 while(button.disabled){if(performance.now()>until)throw Error('copy cooldown timeout');await new Promise(r=>setTimeout(r,20));}
 button.click();await Promise.resolve();await Promise.resolve();
};""")
                    feedback=evaluate("""(async()=>{
 const original=Object.getOwnPropertyDescriptor(navigator,'clipboard'),period=document.getElementById('period'),oldPeriod=period.value;
 let writes=0;Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{writes++;}}});period.value='tomorrow-8';
 const result=[];let independent=false;
 for(const id of ['copy-left','copy-right','txt-copy','text-copy']){
 document.getElementById(id==='txt-copy'?'tab-txt':'tab-all').click();if(id==='text-copy')openCopyDialog();
 const button=document.getElementById(id),before=writes;button.click();await Promise.resolve();await Promise.resolve();
 const first=writes===before+1&&button.disabled,range=document.querySelector('#toast strong'),label=range?.textContent||'';
 const strong=!!range&&Number(getComputedStyle(range).fontWeight)>=700&&document.getElementById('toast').textContent===label+'까지 복사됐습니다.';
 const correct=label.includes(id==='copy-right'||id==='txt-copy'?'23시 59분 59초':'8시');
 button.click();await Promise.resolve();const blocked=writes===before+1;
 button.disabled=false;button.click();await Promise.resolve();const protectedAgain=writes===before+1&&button.disabled;
 if(id==='copy-left'){document.getElementById('copy-right').click();await Promise.resolve();independent=writes===before+2&&document.getElementById('copy-right').disabled;}
 await new Promise(r=>setTimeout(r,1100));result.push({id,first,blocked,protectedAgain,strong,correct,label,reenabled:!button.disabled});
 if(document.getElementById('copy-dialog').open)document.getElementById('copy-dialog').close();
 }
 const oldAlarms=alarmPrefs;document.getElementById('tab-custom').click();alarmPrefs={defaultOn:false,overrides:{}};refreshAlarms();const off=document.getElementById('all-alarm-label').textContent==='주요 알림 OFF';
 alarmPrefs={defaultOn:true,overrides:{}};refreshAlarms();const on=document.getElementById('all-alarm-label').textContent==='주요 알림 ON';alarmPrefs=oldAlarms;document.getElementById('tab-all').click();refreshAlarms();
 period.value=oldPeriod;if(original)Object.defineProperty(navigator,'clipboard',original);else delete navigator.clipboard;
 return{result,independent,majorLabel:on&&off};
})()""")
                    assert all(all(r[k] for k in ['first','blocked','protectedAgain','strong','correct','reenabled']) for r in feedback['result']) and feedback['independent'] and feedback['majorLabel'],feedback
                    print(json.dumps(dict(copy_feedback=feedback)))
                    copy=evaluate("""(async()=>{
 const gear=document.getElementById('copy-settings'),button=document.getElementById('copy-left'),right=document.getElementById('copy-right'),dialog=document.getElementById('copy-dialog'),legacy=document.getElementById('copy-legacy-txt'),period=document.getElementById('period');
 const original=Object.getOwnPropertyDescriptor(navigator,'clipboard');let text='';
 Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{text=value;}}});
 gear.click();const opens=dialog.open&&legacy.checked;period.value='24';period.dispatchEvent(new Event('change'));dialog.close();await previewCopy(button);
 const quick=text,direct=!dialog.open&&quick.length>0&&!quick.includes('황금고블린');
 document.getElementById('tab-custom').click();document.getElementById('custom-open').click();
 document.getElementById('custom-clear').click();await previewCopy(button);const independent=text===quick;
 document.getElementById('custom-undo').click();document.getElementById('custom-close').click();document.getElementById('tab-all').click();
 gear.click();legacy.checked=false;legacy.dispatchEvent(new Event('change'));
 await previewCopy('text-copy');const normal=text,originalCopy=text.includes('황금고블린');dialog.close();
 await previewCopy(button);const leftSettings=text===normal;
 const stored=localStorage.getItem('BossTimer.webCopy.options');await previewCopy(right);
 const rightSettings=text.length>normal.length&&period.value==='24'&&localStorage.getItem('BossTimer.webCopy.options')===stored;
 await previewCopy(button);const restores=text===normal;
 legacy.checked=true;legacy.dispatchEvent(new Event('change'));
 navigator.clipboard.writeText=async()=>{throw Error('denied');};await previewCopy(button);
 const fallback=dialog.open&&dialog.querySelector('.copy-fallback')?.value===quick;
 dialog.querySelector('.copy-fallback')?.remove();dialog.close();
 if(original)Object.defineProperty(navigator,'clipboard',original);else delete navigator.clipboard;
 return {opens,direct,independent,originalCopy,leftSettings,rightSettings,restores,fallback};
})()""")
                    assert all(copy.values()),copy
                    print(json.dumps(dict(quick_copy=copy)))
                    backgrounds=evaluate("""(async()=>{
 const result=[],frame=document.querySelector('.schedule-frame');
 const wait=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 const skin=()=>{const s=getComputedStyle(frame);return [s.backgroundColor,s.backgroundImage,s.borderTopWidth,s.borderTopStyle,s.boxShadow,s.backdropFilter];};
 for(const theme of ['default','sakura','spring','winter','rain','avengers']){
 document.getElementById('tab-all').click();document.getElementById('tab-theme').click();document.getElementById('theme-'+theme).click();await wait();
 const before=skin();document.getElementById('tab-txt').click();await wait();const themed=skin()[0]===getComputedStyle(document.querySelector('.txt-chat')).backgroundColor;
 document.getElementById('tab-theme').click();await wait();const after=skin();
 result.push({theme,themed,restored:JSON.stringify(before)===JSON.stringify(after),themeVisible:!document.getElementById('theme-panel').hidden,scheduleHidden:document.getElementById('schedule-view').hidden,before,after});
 }document.getElementById('tab-all').click();return result;
})()""")
                    assert all(all(v[k] for k in ['themed','restored','themeVisible','scheduleHidden']) for v in backgrounds),backgrounds
                    print(json.dumps(dict(txt_theme_backgrounds=backgrounds)))
                    plain=evaluate("""(async()=>{
 const original=Object.getOwnPropertyDescriptor(navigator,'clipboard');let clipboard='';
 Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{clipboard=text;}}});
 const root=document.documentElement,font=root.style.getPropertyValue('--web-font-family'),size=root.style.getPropertyValue('--web-font-size-offset');
 const samples=[];
 for(const theme of ['default','spring','avengers']){
 document.getElementById('tab-theme').click();document.getElementById('theme-'+theme).click();document.getElementById('tab-txt').click();
 root.style.setProperty('--web-font-family','serif');root.style.setProperty('--web-font-size-offset','9px');
 await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 const box=document.getElementById('schedule-txt'),style=getComputedStyle(box),panel=document.getElementById('schedule-panel');
 await previewCopy('copy-left');
 const visible=node=>node.getClientRects().length>0&&getComputedStyle(node).display!=='none';
 const copy=document.getElementById('txt-copy'),copyStyle=getComputedStyle(copy);
 await previewCopy(copy);
 samples.push({theme,equal:box.value===shareText(sharingEvents(serverNow(),'tomorrow-end').filter(r=>r.at>serverNow()))&&box.value.startsWith('▣ 보스 타임테이블 공지('),
 copied:clipboard===box.value,copyVisible:visible(copy)&&copy.textContent==='전체 복사'&&copyStyle.backgroundColor===getComputedStyle(document.querySelector('.txt-chat')).backgroundColor,
 hidden:panel.hidden&&[...document.querySelectorAll('#schedule-view button')].filter(n=>n.id!=='txt-copy').every(n=>!visible(n)),
 clock:visible(document.getElementById('clock'))&&visible(document.getElementById('schedule-title')),
 plain:style.fontFamily.includes('system-ui')&&parseFloat(style.fontSize)===(root.classList.contains('is-phone')?18:14)&&Math.abs(parseFloat(style.lineHeight)-parseFloat(style.fontSize)*1.2)<.01&&style.fontWeight==='700'&&style.color==='rgb(17, 17, 17)'&&style.backgroundColor==='rgb(255, 255, 255)'&&style.textShadow==='none'&&style.boxShadow==='none'&&style.filter==='none'&&style.animationName==='none'&&style.backgroundImage==='none',
 readonly:box.readOnly,linked:document.getElementById('txt-panel').getAttribute('aria-labelledby')==='tab-txt'});
 }
 const box=document.getElementById('schedule-txt');box.focus();box.setSelectionRange(5,15);
 const originalText=shareText;let formats=0;shareText=(...args)=>{formats++;return originalText(...args);};
 await new Promise(r=>setTimeout(r,1200));shareText=originalText;
 const selection=box.selectionStart===5&&box.selectionEnd===15,noPolling=formats===0;box.blur();
 box.focus();box.setSelectionRange(5,15);const focused=getComputedStyle(box),focusLine=focused.outlineStyle==='none'&&focused.borderTopWidth==='0px'&&focused.boxShadow==='none'&&box.selectionStart===5&&box.selectionEnd===15;box.blur();
 const write=navigator.clipboard.writeText,expectedCopy=box.value;
 navigator.clipboard.writeText=async()=>{throw Error('denied');};await previewCopy('txt-copy');
 const fallback=document.getElementById('copy-events').previousElementSibling,txtFallback=document.getElementById('copy-dialog').open&&fallback?.classList.contains('copy-fallback')&&fallback.value===expectedCopy;
 fallback?.remove();document.getElementById('copy-dialog').close();navigator.clipboard.writeText=write;
 const option=document.getElementById('text-seconds');option.checked=true;option.dispatchEvent(new Event('change'));
 await previewCopy('copy-left');const changed=box.value===shareText(sharingEvents(serverNow(),'tomorrow-end').filter(r=>r.at>serverNow()));
 option.checked=false;option.dispatchEvent(new Event('change'));
 const period=document.getElementById('period'),oldPeriod=period.value,fixedText=box.value;
 period.value='all';period.dispatchEvent(new Event('change'));await previewCopy('copy-left');
 await previewCopy('copy-right');const independentPeriod=box.value===fixedText;
 period.value=oldPeriod;period.dispatchEvent(new Event('change'));
 const before=data,target=data.events.find(r=>r.kind==='boss'&&!r.important&&r.at>serverNow());
 accept({...before,schedule_version:'txt-refresh',events:before.events.map(r=>r===target?{...r,name:r.name+' TXT_REFRESH'}:r)});
 const published=box.value.includes('TXT_REFRESH');accept(before);
 const at=serverNow(),sample=before.events.find(r=>r.kind==='boss'&&!r.important&&r.at>at);
 const short=(name,delay,id)=>({...sample,id:id.repeat(24),name,scheduled_at:new Date(at+delay).toISOString(),end_at:new Date(at+delay+1000).toISOString()});
 accept({...before,schedule_version:'txt-deadline',events:[short('TXT_CHAIN_A',-500,'a'),short('TXT_CHAIN_B',500,'b'),short('TXT_LATER',300000,'c')]});
 const retained=box.value.includes('TXT_CHAIN_A')&&box.value.includes('TXT_CHAIN_B');
 await new Promise(r=>setTimeout(r,800));
 const expired=retained&&!box.value.includes('TXT_CHAIN_')&&box.value.includes('TXT_LATER');accept(before);
 root.style.setProperty('--web-font-family',font);root.style.setProperty('--web-font-size-offset',size);
 document.getElementById('tab-all').click();const restored=!document.getElementById('schedule-panel').hidden&&document.getElementById('txt-panel').hidden;
 if(original)Object.defineProperty(navigator,'clipboard',original);else delete navigator.clipboard;
 return {samples,selection,noPolling,focusLine,txtFallback,changed,independentPeriod,published,expired,restored,errors:window.previewErrors};
})()""")
                    assert all(all(sample[k] for k in ['equal','copied','copyVisible','hidden','clock','plain','readonly','linked']) for sample in plain['samples']) and all(plain[k] for k in ['selection','noPolling','focusLine','txtFallback','changed','independentPeriod','published','expired','restored']) and not plain['errors'],plain
                    print(json.dumps(dict(schedule_txt=plain)))
                    chat=evaluate("""(async()=>{
 const box=document.getElementById('schedule-txt'),before=data;
 document.getElementById('tab-txt').click();
 const image=document.querySelector('.txt-avatar');await image.decode();
 const canvas=document.createElement('canvas');canvas.width=canvas.height=1;canvas.getContext('2d').drawImage(image,0,0);
 const avatar=image.naturalWidth>0&&canvas.getContext('2d').getImageData(0,0,1,1).data[3]===0&&document.querySelector('.txt-speaker').textContent==='슬픈영혼';
 const sample=before.events.find(r=>r.kind==='boss'&&!r.important&&r.at>serverNow());
 accept({...before,schedule_version:'chat-short',events:[{...sample,name:'짧은보스'}]});
 const short=box.offsetHeight,bubble=document.querySelector('.txt-bubble'),thread=document.querySelector('.txt-thread');
 const compact=bubble.offsetWidth<=thread.clientWidth&&bubble.offsetWidth<=300;
 accept({...before,schedule_version:'chat-long',events:[{...sample,name:'긴보스이름'.repeat(16)}]});
 const wraps=box.offsetHeight>short&&box.scrollHeight<=box.clientHeight+1&&box.scrollWidth<=box.clientWidth+1;
 const oldTheme=selectedTheme;
 document.getElementById('tab-theme').click();document.getElementById('theme-gray').click();document.getElementById('tab-txt').click();
 const gray=getComputedStyle(document.querySelector('.txt-chat')).backgroundColor;
 document.getElementById('tab-theme').click();document.getElementById('theme-sakura').click();document.getElementById('tab-txt').click();
 const themed=getComputedStyle(document.querySelector('.txt-chat')).backgroundColor!==gray;
 document.getElementById('tab-theme').click();document.getElementById('theme-'+oldTheme).click();document.getElementById('tab-txt').click();accept(before);window.scrollTo(0,0);
 return {avatar,compact,wraps,themed,width:bubble.offsetWidth,short};
})()""")
                    assert all(chat[k] for k in ['avatar','compact','wraps','themed']),chat
                    print(json.dumps(dict(txt_chat=chat)))
                    chat_sizes=[]
                    for width,height in [(320,760),(390,844),(1280,900)]:
                        cdp.call('Emulation.setDeviceMetricsOverride',dict(width=width,height=height,deviceScaleFactor=1,mobile=width<600),session)
                        size=evaluate("""(async()=>{
 document.getElementById('tab-txt').click();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));fitHeader();
 const box=document.getElementById('schedule-txt'),bubble=document.querySelector('.txt-bubble'),probe=document.querySelector('.txt-width');
 const thread=document.querySelector('.txt-thread').clientWidth;
 const expected=document.documentElement.classList.contains('is-phone')?18:14,font=parseFloat(getComputedStyle(box).fontSize),speaker=parseFloat(getComputedStyle(document.querySelector('.txt-speaker')).fontSize);
 return{font,speaker,fontCorrect:font===expected&&speaker===expected,bubble:bubble.offsetWidth,thread,headerFits:probe.scrollWidth<=box.clientWidth+1,fills:bubble.offsetWidth>=thread-1,noOverflow:box.scrollWidth<=box.clientWidth+1&&document.documentElement.scrollWidth<=innerWidth};
})()""")
                        assert size['fontCorrect'] and (size['headerFits'] or size['fills']) and size['noOverflow'] and size['bubble']<=size['thread'],(width,size)
                        chat_sizes.append(dict(width=width,**size))
                    print(json.dumps(dict(txt_chat_sizes=chat_sizes)))
                    growing=evaluate("""(()=>{
 const before=data,box=document.getElementById('schedule-txt'),bubble=document.querySelector('.txt-bubble'),chat=document.querySelector('.txt-chat'),speaker=document.querySelector('.txt-speaker');
 const row=before.events.find(r=>r.kind==='boss'&&!r.important&&r.at>serverNow());
 accept({...before,schedule_version:'grow-short',events:[{...row,name:'짧은보스'}]});
 const shortWidth=bubble.offsetWidth,shortHeight=box.offsetHeight;
 accept({...before,schedule_version:'grow-long',events:[{...row,name:'너비확인보스'.repeat(3)}]});
 const expands=bubble.offsetWidth>shortWidth&&box.offsetHeight===shortHeight;
 const body=getComputedStyle(box),name=getComputedStyle(speaker),expected=document.documentElement.classList.contains('is-phone')?18:14,fonts=parseFloat(body.fontSize)===expected&&parseFloat(name.fontSize)===expected&&Math.abs(parseFloat(body.lineHeight)-expected*1.2)<.01;
 chat.style.setProperty('--txt-font-size','20px');const follows=parseFloat(getComputedStyle(speaker).fontSize)===parseFloat(getComputedStyle(box).fontSize);
 chat.style.removeProperty('--txt-font-size');accept(before);fitHeader();return{expands,fonts,follows,shortWidth};
})()""")
                    assert all(growing[k] for k in ['expands','fonts','follows']),growing
                    print(json.dumps(dict(txt_font_and_expansion=growing)))
                    reuse=evaluate("""(()=>{
 const box=document.getElementById('schedule-txt'),originalText=shareText,originalRows=sharingEvents,oldTheme=selectedTheme;
 let formats=0,scans=0,hiddenHeight=true,immediate=true;
 shareText=(...args)=>{formats++;return originalText(...args);};sharingEvents=(...args)=>{scans++;return originalRows(...args);};
 box.setSelectionRange(3,12);
 for(const theme of ['gray','sakura','spring','default','avengers']){
 const height=box.style.height;
 document.getElementById('tab-theme').click();document.getElementById('theme-'+theme).click();fitHeader();hiddenHeight&&=box.style.height===height;
 document.getElementById('tab-txt').click();immediate&&=box===document.getElementById('schedule-txt')&&box.clientHeight>0;
 }
 shareText=originalText;sharingEvents=originalRows;
 const selection=box.selectionStart===3&&box.selectionEnd===12;
 document.getElementById('tab-theme').click();document.getElementById('theme-'+oldTheme).click();document.getElementById('tab-txt').click();
 return {formats,scans,hiddenHeight,immediate,selection};
})()""")
                    assert reuse['formats']==reuse['scans']==0 and all(reuse[k] for k in ['hiddenHeight','immediate','selection']),reuse
                    print(json.dumps(dict(txt_theme_cache=reuse)))
                    cdp.call('Emulation.setDeviceMetricsOverride',dict(width=390,height=844,deviceScaleFactor=1,mobile=True),session)
                    if chat_preview:
                        import base64
                        evaluate("document.getElementById('tab-theme').click();document.getElementById('theme-gray').click();document.getElementById('tab-txt').click();window.scrollTo(0,0)")
                        raw=cdp.call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False),session)
                        Path(chat_preview).write_bytes(base64.b64decode(raw['data']))
                    evaluate("document.getElementById('tab-all').click()")
                    gear_layout=[]
                    for width,height in [(320,760),(390,844),(1280,900)]:
                        cdp.call('Emulation.setDeviceMetricsOverride',dict(width=width,height=height,deviceScaleFactor=1,mobile=width<600),session)
                        sample=evaluate("""(async()=>{
 const result=[],gear=document.getElementById('copy-settings'),copy=document.getElementById('copy-open');
 const layout=()=>['copy-open','logout','context','nickname-area'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return [r.x,r.y,r.width,r.height];});
 for(const theme of ['default','sakura','avengers']){
 document.getElementById('theme-'+theme).click();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 const before=layout(),g=gear.getBoundingClientRect(),b=copy.getBoundingClientRect();
 const left=document.getElementById('copy-left').getBoundingClientRect(),right=document.getElementById('copy-right').getBoundingClientRect();
 const split=Math.abs(left.width-right.width)<=1&&Math.abs(left.right-right.left)<1&&left.height>10&&left.y===right.y;
 const hit=document.elementFromPoint(g.x+g.width/2,g.y+g.height/2)?.closest('#copy-settings')===gear;
 gear.style.display='none';const after=layout();gear.style.display='';
 result.push({theme,unchanged:JSON.stringify(before)===JSON.stringify(after),hit,split,
   aligned:Math.abs(g.x-(b.right-13))<1&&Math.abs(g.y-(b.bottom-13))<1,
   small:g.width===18&&g.height===18});
 }return result;
})()""")
                        assert all(all(row[k] for k in ['unchanged','hit','aligned','small','split']) for row in sample),(width,sample)
                        gear_layout.append(dict(width=width,checks=sample))
                    print(json.dumps(dict(copy_gear_layout=gear_layout)))
                    if not phone_menu:
                        cdp.call('Emulation.setDeviceMetricsOverride',dict(width=500,height=760,deviceScaleFactor=1,mobile=False),session)
                        anchored=evaluate("""(async()=>{
 const root=document.documentElement,gear=document.getElementById('copy-settings'),copy=document.getElementById('copy-open'),old=root.style.overflowY,out=[];
 const wait=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 for(const theme of ['gray','sakura','avengers']){
 document.getElementById('theme-'+theme).click();
 for(const tab of ['all','custom','txt','theme']){
 document.getElementById('tab-'+tab).click();window.scrollTo(0,0);
 for(const mode of ['hidden','scroll','hidden']){
 root.style.overflowY=mode;await wait();
 const b=copy.getBoundingClientRect(),g=gear.getBoundingClientRect();
 out.push({theme,tab,mode,viewport:root.clientWidth,aligned:Math.abs(g.x-b.right+13)<1&&Math.abs(g.y-b.bottom+13)<1,local:gear.parentElement===copy.parentElement&&gear.style.left===''&&gear.style.top===''});
 }}}
 root.style.overflowY=old;document.getElementById('tab-all').click();return out;
})()""")
                        assert all(r['aligned'] and r['local'] for r in anchored),anchored
                        assert any(r['viewport']<500 for r in anchored if r['mode']=='scroll'),anchored
                        print(json.dumps(dict(copy_gear_scrollbar=anchored)))
                    cdp.call('Emulation.setDeviceMetricsOverride',dict(width=390,height=844,deviceScaleFactor=1,mobile=True),session)
                    cinematic_ui=evaluate(Path('check_web_cinematic_layout.js').read_text(encoding='utf-8'))
                    print(json.dumps(dict(cinematic_ui=cinematic_ui)))
                    alarm_colors=evaluate("""(()=>{
 document.getElementById('theme-avengers').click();
 const button=document.getElementById('all-alarm'),label=document.getElementById('all-alarm-label');
 const original=[button.getAttribute('aria-pressed'),button.dataset.locked,button.disabled],rows=[];
 button.disabled=false;
 for(const locked of ['false','true'])for(const state of ['false','mixed','true']){
   button.dataset.locked=locked;button.setAttribute('aria-pressed',state);
   const text=getComputedStyle(label),panel=getComputedStyle(button);
   rows.push({locked,state,color:text.color,fill:text.webkitTextFillColor,labelBackground:text.backgroundImage,
     labelOpacity:text.opacity,buttonOpacity:panel.opacity,background:panel.backgroundImage});
 }
 button.setAttribute('aria-pressed',original[0]);button.dataset.locked=original[1];button.disabled=original[2];
 return rows;
})()""")
                    for sample in alarm_colors:
                        if sample['state']=='mixed':
                            assert sample['fill']==sample['color']=='rgb(255, 240, 207)',sample
                            assert sample['labelBackground']=='none' and sample['labelOpacity']=='1',sample
                            assert sample['buttonOpacity']=='1',sample
                            assert 'rgb(166, 36, 11)' in sample['background'] and 'rgb(20, 24, 24)' in sample['background'],sample
                    print(json.dumps(dict(ironman_alarm_colors=len(alarm_colors),passed=True)))
                    if mouse_menu:
                        mouse=evaluate("""(async()=>{
 const root=document.documentElement,input=document.getElementById('menu-mouse-hide'),delay=document.getElementById('menu-mouse-delay'),wait=ms=>new Promise(r=>setTimeout(r,ms));
 const visible=()=>!root.classList.contains('menus-idle')&&['frame-heading','recent'].every(id=>getComputedStyle(document.getElementById(id)).display!=='none');
 const boundary=type=>root.dispatchEvent(new PointerEvent(type,{pointerType:'mouse'}));
 const option=v=>{input.checked=v;input.dispatchEvent(new Event('change',{bubbles:true}));};
 const wheel=dy=>{const e=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:dy});document.dispatchEvent(e);return e;};
 const edge=()=>document.querySelector('.hero').getBoundingClientRect().top+scrollY;
 const extra=document.createElement('div');extra.id='mouse-scroll-fixture';extra.style.height='1500px';document.querySelector('.schedule-app').append(extra);
 const defaultOn=input.checked,delayDefault=delay.value==='10';delay.value='1';delay.dispatchEvent(new Event('change'));option(true);
 window.scrollTo(0,0);await wait(100);boundary('pointerleave');await wait(1100);
 const aligned=scrollY>1&&Math.abs(document.querySelector('.hero').getBoundingClientRect().top)<=1&&visible();
 boundary('pointerenter');const entered=scrollY;await wait(50);const noEntryJump=scrollY===entered;
 window.scrollTo(0,0);await wait(100);boundary('pointerleave');await wait(500);window.scrollTo(0,100);await wait(800);const userScrollKept=Math.abs(scrollY-100)<=1;
 boundary('pointerleave');await wait(1100);const belowTopKept=Math.abs(scrollY-100)<=1;
 window.scrollTo(0,0);await wait(100);boundary('pointerenter');boundary('pointerleave');await wait(500);boundary('pointerenter');await wait(650);const cancelled=scrollY===0;
 const down=wheel(500),downStop=down.defaultPrevented&&Math.abs(scrollY-edge())<=1&&visible();
 const up=wheel(-10);window.scrollTo(0,100);await wait(100);const ascentFree=!up.defaultPrevented&&Math.abs(scrollY-100)<=1&&visible();
 wheel(500);await wait(350);const nextDown=!wheel(100).defaultPrevented;
 option(false);window.scrollTo(0,0);await wait(100);boundary('pointerleave');await wait(1100);const disabled=scrollY===0;
 option(true);return {defaultOn,delayDefault,aligned,noEntryJump,userScrollKept,belowTopKept,cancelled,downStop,ascentFree,nextDown,disabled,menusVisible:visible()};
})()""")
                        assert all(mouse.values()),mouse
                        cdp.call('Input.dispatchMouseEvent',dict(type='mouseMoved',x=150,y=150),session)
                        cdp.call('Input.dispatchMouseEvent',dict(type='mouseMoved',x=-10,y=150),session)
                        mouse['nativeAutoAlign']=evaluate("new Promise(r=>setTimeout(()=>r(scrollY>1&&Math.abs(document.querySelector('.hero').getBoundingClientRect().top)<=1&&!document.documentElement.classList.contains('menus-idle')),1100))")
                        before=evaluate('scrollY')
                        cdp.call('Input.dispatchMouseEvent',dict(type='mouseMoved',x=150,y=150),session)
                        mouse['nativeEntryKept']=evaluate('Math.abs(scrollY-'+str(before)+')<=1')
                        cdp.call('Input.dispatchMouseEvent',dict(type='mouseWheel',x=150,y=150,deltaY=-30,deltaX=0),session)
                        mouse['nativeUpFree']=evaluate("new Promise(r=>setTimeout(()=>r(scrollY<"+str(before)+"&&!document.documentElement.classList.contains('menus-idle')),150))")
                        assert all(mouse.values()),mouse
                        evaluate("document.getElementById('mouse-scroll-fixture').remove();window.scrollTo(0,0)")
                        print(json.dumps(dict(mouse_menu=mouse)))
                    if phone_menu:
                        mobile=evaluate("""(async()=>{
 const root=document.documentElement,folded=()=>root.classList.contains('menus-idle'),wait=ms=>new Promise(r=>setTimeout(r,ms));
 const touch=(type,y)=>{const p=new Touch({identifier:1,target:document.body,clientX:120,clientY:y});const e=new TouchEvent(type,{bubbles:true,cancelable:true,touches:['touchend','touchcancel'].includes(type)?[]:[p],changedTouches:[p]});document.dispatchEvent(e);return e;};
 window.scrollTo(0,0);WebMenuIdle.activity();await wait(3200);
 const idleVisible=!folded(),phone=root.classList.contains('is-phone');
 await document.fonts.ready;
 const extra=document.createElement('div');extra.style.height='1800px';document.querySelector('.schedule-app').append(extra);
 const edge=Math.max(0,document.querySelector('.hero').getBoundingClientRect().top+scrollY),atEdge=()=>Math.abs(scrollY-edge)<=1;
 touch('touchstart',600);const down=touch('touchmove',200);window.scrollTo(0,edge+80);await wait(150);
 const dragVisible=!folded()&&!down.defaultPrevented&&scrollY>edge+10;
 touch('touchend',200);await wait(150);const releaseStopped=atEdge()&&!folded();
 window.scrollTo(0,edge+150);await wait(150);const inertiaStopped=atEdge();
 touch('touchstart',600);touch('touchmove',200);window.scrollTo(0,edge+220);await wait(150);touch('touchend',200);
 const listScrolled=scrollY>edge+100&&!folded();
 touch('touchstart',200);touch('touchmove',400);window.scrollTo(0,edge+50);await wait(150);touch('touchend',400);
 window.scrollTo(0,edge-50);await wait(150);const ascentFree=Math.abs(scrollY-(edge-50))<=1&&!folded();
 touch('touchstart',200);touch('touchmove',500);window.scrollTo(0,0);await wait(150);touch('touchend',500);
 const topReached=scrollY===0&&!folded();touch('touchcancel',500);extra.remove();await wait(3200);
 return {phone,idleVisible,dragVisible,releaseStopped,inertiaStopped,listScrolled,ascentFree,topReached,staysVisible:!folded()};
})()""")
                        assert all(mobile.values()),mobile
                        print(json.dumps(dict(phone_menu=mobile)))
                    if frame_height:
                        height_checks=[]
                        for width,height in ((390,844),(500,560),(1280,900)):
                            cdp.call('Emulation.setDeviceMetricsOverride',dict(width=width,height=height,deviceScaleFactor=1,mobile=width<600),session)
                            result=evaluate("""(async()=>{
 const root=document.documentElement,app=document.querySelector('.schedule-app');
 document.getElementById('theme-avengers').click();
 data={...data,events:[],active:[],schedule_version:'empty-frame-'+innerWidth};tick();
 const banner=document.createElement('aside');banner.style.height='48px';banner.style.flexShrink='0';document.body.prepend(banner);
 const rows=[];
 const inspect=()=>({viewport:innerHeight,app:app.getBoundingClientRect().height,appBottom:app.getBoundingClientRect().bottom+scrollY,
   body:document.body.getBoundingClientRect().height,banner:banner.getBoundingClientRect().height,scrollWidth:root.scrollWidth,width:root.clientWidth,
   border:getComputedStyle(app,'::before').borderImageSource});
 for(const folded of [false,true]){
   root.classList.toggle('menus-idle',folded);await new Promise(r=>requestAnimationFrame(r));
   const short=inspect(),extra=document.createElement('div');extra.style.height='1600px';app.append(extra);
   await new Promise(r=>requestAnimationFrame(r));const long=inspect();extra.remove();rows.push({folded,short,long});
 }
 root.classList.remove('menus-idle');banner.remove();return rows;
})()""")
                            for state in result:
                                for mode in ('short','long'):
                                    sample=state[mode]
                                    assert sample['app']+sample['banner']>=sample['viewport']-1,(width,state,mode)
                                    assert abs(sample['appBottom']-sample['body'])<=1,(width,state,mode)
                                    assert sample['scrollWidth']==sample['width'],(width,state,mode)
                                    assert sample['border']!='none',(width,state,mode)
                                assert state['long']['app']>state['short']['app']+800,(width,state)
                            height_checks.append(dict(width=width,height=height,states=result))
                        print(json.dumps(dict(frame_height=height_checks),ensure_ascii=True))
                        return height_checks
                    report=evaluate("""(async()=>{
 const result=[];
 for(const theme of ['avengers','sakura','default','spring','sky','wood','green','blue','magenta','ivory','canary','forsythia','purple','gray','winter','rain','avengers','sakura']){
   document.getElementById('theme-'+theme).click();
   await new Promise(r=>setTimeout(r,100));
   const root=document.documentElement,app=document.querySelector('.schedule-app'),push=document.getElementById('push-controls');
   const props=(node,pseudo)=>{const c=getComputedStyle(node,pseudo);return {display:c.display,content:c.content,border:c.borderImageSource,shadow:c.boxShadow,animation:c.animationName};};
   const stage=document.querySelector('.ironman-cinematic-stage'),dock=document.getElementById('ironman-media-dock');
   result.push({theme,actual:root.dataset.theme||'default',width:root.clientWidth,scrollWidth:root.scrollWidth,
     frame:props(app,'::before'),stage:props(stage),stageRect:stage.getBoundingClientRect().toJSON(),
     dock:props(dock),pushRect:push.getBoundingClientRect().toJSON(),
     below:[...document.querySelectorAll('body *')].filter(n=>{const r=n.getBoundingClientRect();return r.width>80&&r.height>0&&r.top>=push.getBoundingClientRect().bottom-2;}).slice(0,12).map(n=>({id:n.id,tag:n.tagName,class:n.className?.baseVal??n.className,rect:n.getBoundingClientRect().toJSON()}))});
 }
 return result;
})()""")
                    for entry in report:
                        assert entry['actual']==entry['theme'],entry
                        assert entry['scrollWidth']==entry['width'],entry
                        if entry['theme']=='avengers':
                            assert entry['stage']['display']=='block' and entry['stageRect']['height']>0,entry
                            assert entry['frame']['border']!='none',entry
                        else:
                            assert entry['stage']['display']=='none' and entry['stageRect']['height']==0,entry
                            assert entry['dock']['display']=='none' and entry['frame']['border']=='none',entry
                    print(json.dumps(dict(transitions=len(report),viewport=390,passed=True,
                        themes=[v['theme'] for v in report]),ensure_ascii=True))
                    if mouse_menu:
                        short=evaluate("""(async()=>{
 const before=data,base=before.events.find(r=>r.kind==='boss');
 const one={...base,id:'9'.repeat(24),scheduled_at:new Date(Date.now()+3600000).toISOString(),end_at:new Date(Date.now()+3660000).toISOString()};
 document.getElementById('theme-gray').click();selectFilter('all');document.getElementById('day-today').click();
 accept({...before,schedule_version:'short-scroll-range',events:[one],active:[]});window.scrollTo(0,0);await new Promise(r=>setTimeout(r,100));
 document.dispatchEvent(new WheelEvent('wheel',{deltaY:100,cancelable:true,bubbles:true}));await new Promise(r=>setTimeout(r,100));
 const retained=getComputedStyle(document.getElementById('frame-heading')).display!=='none';
 const noOverflow=document.documentElement.scrollHeight<=innerHeight+1;
 const unchanged=scrollY===0;
 const noExtra=!document.querySelector('#events>div[aria-hidden="true"]');
 accept(before);return {retained,noOverflow,unchanged,noExtra};
})()""")
                        assert all(short.values()),short
                        print(json.dumps(dict(short_next_scroll=short)))
                        motion=evaluate(Path('check_web_hidden_motion.js').read_text(encoding='utf-8'))
                        print(json.dumps(dict(offscreen_motion=motion)))
                    spectrum=evaluate("""(async()=>{
 const checked='a254f002f2e41a83e299f49c',whole='8342eabe36e4b872a1be5075',other='a'.repeat(24);
 const rows=[{id:checked,name:'체크된침공',label:'체크된침공'},{id:whole,name:'침공',label:'전체침공'},{id:other,name:'비교 보스',label:'비교 보스'}].map(r=>({...r,kind:'boss',group:'특수 이벤트'}));
 accept({...data,catalog_version:'checked-spectrum',boss_catalog:rows});
 document.getElementById('theme-gray').click();selectFilter('custom');customEditing=true;customDraft=new Set([checked,other]);customEditorKey='';refreshCustomUI();
 const original=window.fetch;window.fetch=(input,init)=>String(input)==='/api/boss-spectrum'?Promise.resolve(new Response(JSON.stringify({schema:2,numbers:{[checked]:28,[whole]:28,[other]:24},can_save:true}))):original(input,init);
 WebBossSpectrum.init({selected:()=>customDraft,known:id=>customById.has(id),editing:()=>customEditing,theme:()=>document.documentElement.dataset.theme,note:toast,repaint:()=>refreshCustomUI()});
 const find=id=>document.querySelector('[data-choice="'+id+'"]');
 for(let i=0;i<100&&!find(checked).querySelector('.custom-spectrum-badge');i++)await new Promise(r=>setTimeout(r,10));
 window.fetch=original;
 const button=find(checked),reference=find(other),selected=getComputedStyle(button).backgroundImage;
 const result={badge:button.querySelector('.custom-spectrum-badge')?.textContent,assigned:button.classList.contains('spectrum-choice'),selectedSame:selected===getComputedStyle(reference).backgroundImage};
 button.click();result.released=button.getAttribute('aria-pressed')==='false'&&getComputedStyle(button).backgroundImage!==selected&&button.querySelector('.custom-spectrum-badge')?.textContent==='28';
 return result;
})()""")
                    assert spectrum==dict(badge='28',assigned=True,selectedSame=True,released=True),spectrum
                    print(json.dumps(dict(checked_invasion_spectrum=spectrum)))
                    return report
                finally:
                    if cdp:
                        try:cdp.call('Browser.close')
                        except (OSError,EOFError):pass
                        cdp.socket.close()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
        finally:server.shutdown();server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restricted-runner',action='store_true')
    parser.add_argument('--phone-menu',action='store_true')
    parser.add_argument('--mouse-menu',action='store_true')
    parser.add_argument('--frame-height',action='store_true')
    parser.add_argument('--chat-preview',default='')
    options=parser.parse_args()
    check(options.restricted_runner,options.phone_menu,options.mouse_menu,options.frame_height,options.chat_preview)

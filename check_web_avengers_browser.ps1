param([Parameter(Mandatory)][string]$Url,[Parameter(Mandatory)][int]$BrowserPort,[switch]$Preview,[switch]$Font15,[switch]$CustomHeader,[switch]$Cinematic,[switch]$HelpChrome,[switch]$CinematicAudio,[switch]$MenuIdle,[switch]$MenuNative,[switch]$EmptyCustomRest,[switch]$HiddenMotion,[switch]$BossRanks,[switch]$IdleGap,[switch]$FontThemes,[switch]$CinematicPreferences)
$ErrorActionPreference='Stop'
$socket=[System.Net.WebSockets.ClientWebSocket]::new()
$script:serial=0
function Send-CDP($method,$parameters=@{}) {
    $script:serial++
    $id=$script:serial
    $bytes=[Text.Encoding]::UTF8.GetBytes((@{id=$id;method=$method;params=$parameters}|ConvertTo-Json -Depth 12 -Compress))
    $socket.SendAsync([ArraySegment[byte]]::new($bytes),[Net.WebSockets.WebSocketMessageType]::Text,$true,[Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null
    while($true){
        $stream=[IO.MemoryStream]::new();$buffer=New-Object byte[] 65536
        do {
            $part=$socket.ReceiveAsync([ArraySegment[byte]]::new($buffer),[Threading.CancellationToken]::None).GetAwaiter().GetResult()
            $stream.Write($buffer,0,$part.Count)
        } while(-not $part.EndOfMessage)
        $reply=[Text.Encoding]::UTF8.GetString($stream.ToArray())|ConvertFrom-Json
        $stream.Dispose()
        if($reply.id -eq $id){if($reply.error){throw ($reply.error|ConvertTo-Json -Compress)};return $reply.result}
    }
}
try {
    $deadline=(Get-Date).AddSeconds(12)
    $pages=$null
    while(-not $pages){
        if((Get-Date) -gt $deadline){throw 'Isolated Chrome did not become ready'}
        try {$pages=Invoke-RestMethod "http://127.0.0.1:$BrowserPort/json/list"} catch {}
        Start-Sleep -Milliseconds 100
    }
    $page=$pages|Where-Object {$_.type -eq 'page'}|Select-Object -First 1
    $socket.ConnectAsync([Uri]$page.webSocketDebuggerUrl,[Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null
    Send-CDP 'Page.enable' | Out-Null
    Send-CDP 'Page.navigate' @{url=$Url} | Out-Null
    $result=Send-CDP 'Runtime.evaluate' @{expression="new Promise((resolve,reject)=>{const until=Date.now()+10000;const poll=()=>{const v=document.getElementById('result')?.textContent;if(v&&v!=='pending')resolve(v);else if(Date.now()>until)reject(Error('fixture timeout'));else setTimeout(poll,50)};poll()})";awaitPromise=$true;returnByValue=$true}
    if($result.exceptionDetails){throw ($result.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
    $report=$result.result.value|ConvertFrom-Json
    if($report.loaded.Count -ne 10 -or ($report.loaded -contains $false) -or -not $report.same -or -not $report.interiorClear -or $report.scene -ne 1200 -or -not $report.frame -or -not $report.button -or $report.violations.Count -ne 0){throw $result.result.value}
    Write-Output $result.result.value
    Send-CDP 'DOM.enable' | Out-Null
    Send-CDP 'CSS.enable' | Out-Null
    $tree=Send-CDP 'DOM.getDocument'
    $copy=Send-CDP 'DOM.querySelector' @{nodeId=$tree.root.nodeId;selector='#copy-open'}
    Send-CDP 'CSS.forcePseudoState' @{nodeId=$copy.nodeId;forcedPseudoClasses=@('active')} | Out-Null
    $pressed=Send-CDP 'Runtime.evaluate' @{expression="getComputedStyle(document.getElementById('copy-open')).backgroundImage";returnByValue=$true}
    if(-not $pressed.result.value.Contains('rgb(255, 138, 68)')){throw 'Pressed button did not turn armor red'}
    Send-CDP 'CSS.forcePseudoState' @{nodeId=$copy.nodeId;forcedPseudoClasses=@()} | Out-Null
    if($MenuIdle -or $MenuNative){
        Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=844;deviceScaleFactor=1;mobile=$true} | Out-Null
        Send-CDP 'Page.navigate' @{url=($Url.TrimEnd('/')+'/preview')} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,1500))';awaitPromise=$true} | Out-Null
        if(-not $MenuNative){
            $idleCheck=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'check_web_menu_idle.js'))
            $idle=Send-CDP 'Runtime.evaluate' @{expression=$idleCheck;awaitPromise=$true;returnByValue=$true}
            if($idle.exceptionDetails){throw ($idle.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
            Write-Output ($idle.result.value|ConvertTo-Json -Depth 8 -Compress)
        } else {
            Send-CDP 'Runtime.evaluate' @{expression="document.getElementById('theme-default').click()"} | Out-Null
        }
        Send-CDP 'Runtime.evaluate' @{expression="window.nativeMenuSpacer=document.createElement('div');nativeMenuSpacer.style.height='900px';document.querySelector('main').append(nativeMenuSpacer);window.scrollTo(0,0);WebMenuIdle.activity()"} | Out-Null
        Send-CDP 'Input.dispatchMouseEvent' @{type='mouseWheel';x=100;y=400;deltaX=0;deltaY=600} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,150))';awaitPromise=$true} | Out-Null
        Send-CDP 'Input.dispatchMouseEvent' @{type='mouseWheel';x=100;y=400;deltaX=0;deltaY=100} | Out-Null
        $down=Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(()=>r(document.documentElement.classList.contains('menus-idle')&&scrollY===0),100))";awaitPromise=$true;returnByValue=$true}
        if(-not $down.result.value){throw 'Native first downward wheel did not hold at next boss'}
        Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,350))';awaitPromise=$true} | Out-Null
        Send-CDP 'Input.dispatchMouseEvent' @{type='mouseWheel';x=100;y=400;deltaX=0;deltaY=100} | Out-Null
        $downNext=Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(()=>r(document.documentElement.classList.contains('menus-idle')&&scrollY>0),150))";awaitPromise=$true;returnByValue=$true}
        if(-not $downNext.result.value){throw 'Native new downward wheel could not scroll list after stop'}
        Send-CDP 'Runtime.evaluate' @{expression='window.scrollTo(0,160)'} | Out-Null
        Send-CDP 'Input.dispatchMouseEvent' @{type='mouseWheel';x=100;y=400;deltaX=0;deltaY=-600} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,150))';awaitPromise=$true} | Out-Null
        Send-CDP 'Input.dispatchMouseEvent' @{type='mouseWheel';x=100;y=400;deltaX=0;deltaY=-100} | Out-Null
        $held=Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(()=>r({folded:document.documentElement.classList.contains('menus-idle'),top:scrollY,errors:window.previewErrors}),100))";awaitPromise=$true;returnByValue=$true}
        if(-not $held.result.value.folded -or $held.result.value.top -ne 0){throw ('Native continuous scrolling did not stop at the next boss: '+($held.result.value|ConvertTo-Json -Compress))}
        Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,350))';awaitPromise=$true} | Out-Null
        Send-CDP 'Input.dispatchMouseEvent' @{type='mouseWheel';x=100;y=400;deltaX=0;deltaY=-100} | Out-Null
        $wheel=Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(()=>r(!document.documentElement.classList.contains('menus-idle')&&scrollY===0),150))";awaitPromise=$true;returnByValue=$true}
        if(-not $wheel.result.value){throw 'Native separate upward wheel at top did not reveal menus'}
        Send-CDP 'Emulation.setTouchEmulationEnabled' @{enabled=$true} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchStart';touchPoints=@(@{x=350;y=500})} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchMove';touchPoints=@(@{x=350;y=450})} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,30))';awaitPromise=$true} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchMove';touchPoints=@(@{x=350;y=250})} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchEnd';touchPoints=@()} | Out-Null
        $swipeDown=Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(()=>r(document.documentElement.classList.contains('menus-idle')&&scrollY===0),150))";awaitPromise=$true;returnByValue=$true}
        if(-not $swipeDown.result.value){throw 'Native first downward swipe did not hold at next boss'}
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchStart';touchPoints=@(@{x=350;y=500})} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchMove';touchPoints=@(@{x=350;y=450})} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,30))';awaitPromise=$true} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchMove';touchPoints=@(@{x=350;y=250})} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchEnd';touchPoints=@()} | Out-Null
        $swipeNext=Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(()=>r(document.documentElement.classList.contains('menus-idle')&&scrollY>0),150))";awaitPromise=$true;returnByValue=$true}
        if(-not $swipeNext.result.value){throw 'Native new downward swipe could not scroll list after stop'}
        Send-CDP 'Runtime.evaluate' @{expression='window.scrollTo(0,160)'} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchStart';touchPoints=@(@{x=350;y=500})} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchMove';touchPoints=@(@{x=350;y=560})} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,30))';awaitPromise=$true} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchMove';touchPoints=@(@{x=350;y=750})} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchEnd';touchPoints=@()} | Out-Null
        $swipe=Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(()=>r(document.documentElement.classList.contains('menus-idle')&&scrollY===0),200))";awaitPromise=$true;returnByValue=$true}
        if(-not $swipe.result.value){throw 'Native mobile swipe did not stop at the next boss'}
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchStart';touchPoints=@(@{x=350;y=700})} | Out-Null
        Send-CDP 'Input.dispatchTouchEvent' @{type='touchEnd';touchPoints=@()} | Out-Null
        $touch=Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(()=>r(!document.documentElement.classList.contains('menus-idle')&&scrollY===0),150))";awaitPromise=$true;returnByValue=$true}
        if(-not $touch.result.value){throw 'Native direct touch did not reveal menus'}
        Send-CDP 'Runtime.evaluate' @{expression='nativeMenuSpacer.remove()'} | Out-Null
        Write-Output 'Native first descent/ascent stopped at next boss; new gestures scroll normally and light tap restores menus'
    }
    if($EmptyCustomRest){
        Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=844;deviceScaleFactor=1;mobile=$true} | Out-Null
        Send-CDP 'Page.navigate' @{url=($Url.TrimEnd('/')+'/preview')} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,1500))';awaitPromise=$true} | Out-Null
        $restCheck=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'check_web_empty_custom_rest.js'))
        $rest=Send-CDP 'Runtime.evaluate' @{expression=$restCheck;awaitPromise=$true;returnByValue=$true}
        if($rest.exceptionDetails){throw ($rest.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
        Write-Output ($rest.result.value|ConvertTo-Json -Depth 8 -Compress)
    }
    if($CinematicPreferences){
        Send-CDP 'Page.addScriptToEvaluateOnNewDocument' @{source='Math.random=()=>0;'} | Out-Null
        foreach($width in @(390,320,280)){
            Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=$width;height=1100;deviceScaleFactor=1;mobile=$true} | Out-Null
            Send-CDP 'Page.navigate' @{url=($Url.TrimEnd('/')+'/preview?font=15')} | Out-Null
            Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,1600))';awaitPromise=$true} | Out-Null
            $fontCheck=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'check_web_cinematic_preferences.js'))
            $result=Send-CDP 'Runtime.evaluate' @{expression=$fontCheck;awaitPromise=$true;returnByValue=$true;userGesture=$true}
            if($result.exceptionDetails){throw ($result.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
            Write-Output ($result.result.value|ConvertTo-Json -Depth 6 -Compress)
        }
    }
    if($FontThemes){
        foreach($width in @(390,320,280)){
            Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=$width;height=1100;deviceScaleFactor=1;mobile=$true} | Out-Null
            Send-CDP 'Page.navigate' @{url=($Url.TrimEnd('/')+'/preview?font=15')} | Out-Null
            Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,1200))';awaitPromise=$true} | Out-Null
            $fontCheck=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'check_web_font_themes.js'))
            $fontResult=Send-CDP 'Runtime.evaluate' @{expression=$fontCheck;awaitPromise=$true;returnByValue=$true}
            if($fontResult.exceptionDetails){throw ($fontResult.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
            Write-Output ($fontResult.result.value|ConvertTo-Json -Depth 8 -Compress)
        }
    }
    if($IdleGap){
        Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=844;deviceScaleFactor=1;mobile=$true} | Out-Null
        Send-CDP 'Page.navigate' @{url=($Url.TrimEnd('/')+'/preview')} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,1200))';awaitPromise=$true} | Out-Null
        $clockCheck=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'check_web_idle_gap.js'))
        $clockResult=Send-CDP 'Runtime.evaluate' @{expression=$clockCheck;awaitPromise=$true;returnByValue=$true}
        if($clockResult.exceptionDetails){throw ($clockResult.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
        Write-Output ($clockResult.result.value|ConvertTo-Json -Depth 8 -Compress)
    }
    if($BossRanks){
        foreach($width in @(390,320,280)){
            Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=$width;height=1000;deviceScaleFactor=1;mobile=$true} | Out-Null
            Send-CDP 'Page.navigate' @{url=($Url.TrimEnd('/')+'/preview')} | Out-Null
            Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,1200))';awaitPromise=$true} | Out-Null
            $rankCheck=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'check_web_avengers_boss_ranks.js'))
            Send-CDP 'Runtime.evaluate' @{expression="window.rankExpectedWidth=$width"} | Out-Null
            $ranks=Send-CDP 'Runtime.evaluate' @{expression=$rankCheck;awaitPromise=$true;returnByValue=$true}
            if($ranks.exceptionDetails){throw ($ranks.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
            Write-Output ($ranks.result.value|ConvertTo-Json -Depth 8 -Compress)
        }
    }
    if($HiddenMotion){
        foreach($preference in @('no-preference','reduce')){
            Send-CDP 'Emulation.setEmulatedMedia' @{features=@(@{name='prefers-reduced-motion';value=$preference})} | Out-Null
            Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=844;deviceScaleFactor=1;mobile=$true} | Out-Null
            Send-CDP 'Page.navigate' @{url=($Url.TrimEnd('/')+'/preview')} | Out-Null
            Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,1200))';awaitPromise=$true} | Out-Null
            $hiddenCheck=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'check_web_hidden_motion.js'))
            $motion=Send-CDP 'Runtime.evaluate' @{expression=$hiddenCheck;awaitPromise=$true;returnByValue=$true}
            if($motion.exceptionDetails){throw ($motion.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
            Write-Output ($preference+': '+($motion.result.value|ConvertTo-Json -Depth 8 -Compress))
        }
    }
    if($Preview){
        $motionPreference=Send-CDP 'Runtime.evaluate' @{expression="matchMedia('(prefers-reduced-motion: reduce)').matches";returnByValue=$true}
        Write-Output "Browser default reduced motion: $($motionPreference.result.value)"
        Send-CDP 'Emulation.setEmulatedMedia' @{features=@(@{name='prefers-reduced-motion';value='no-preference'})} | Out-Null
        Send-CDP 'Emulation.setTimezoneOverride' @{timezoneId='Asia/Seoul'} | Out-Null
        foreach($width in @(390,320,1440)){
            Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=$width;height=1100;deviceScaleFactor=1;mobile=($width -lt 760)} | Out-Null
            $previewUrl=$Url.TrimEnd('/')+'/preview'
            if($Font15){$previewUrl+='?font=15'}
            Send-CDP 'Page.navigate' @{url=$previewUrl} | Out-Null
            Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,2000))';awaitPromise=$true} | Out-Null
            $report=Send-CDP 'Runtime.evaluate' @{expression="JSON.stringify({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,errors:window.previewErrors,scene:document.getElementById('avengers-scene').naturalWidth,selected:getComputedStyle(document.getElementById('tab-all')).backgroundImage})";returnByValue=$true}
            Write-Output $report.result.value
            $value=$report.result.value|ConvertFrom-Json
            if($value.scrollWidth -gt $width -or $value.errors.Count){throw $report.result.value}
            $recent=Send-CDP 'Runtime.evaluate' @{expression="document.getElementById('recent').children.length";returnByValue=$true}
            if($recent.result.value -ne 1){throw 'Iron Man must show exactly one recent boss even with simultaneous spawns'}
            if($width -eq 390){
                $caps=Send-CDP 'Runtime.evaluate' @{expression="(()=>{const counts=['theme-default','theme-sakura','theme-winter'].map(id=>{document.getElementById(id).click();return document.getElementById('recent').children.length});document.getElementById('theme-avengers').click();return JSON.stringify({normal:counts,ironman:document.getElementById('recent').children.length})})()";returnByValue=$true}
                $capReport=$caps.result.value|ConvertFrom-Json
                if(($capReport.normal|Where-Object {$_ -ne 2}).Count -or $capReport.ironman -ne 1){throw $caps.result.value}
                Write-Output $caps.result.value
                $drag=Send-CDP 'Runtime.evaluate' @{expression="(()=>{window.getSelection().removeAllRanges();const r=document.querySelector('#next-name time').getBoundingClientRect(),h=document.querySelector('.hero').getBoundingClientRect();return JSON.stringify({x:r.left+3,y:r.top+r.height/2,end:h.right-18})})()";returnByValue=$true}
                $point=$drag.result.value|ConvertFrom-Json
                Send-CDP 'Input.dispatchMouseEvent' @{type='mousePressed';x=$point.x;y=$point.y;button='left';buttons=1;clickCount=1} | Out-Null
                Send-CDP 'Input.dispatchMouseEvent' @{type='mouseMoved';x=$point.end;y=$point.y;button='left';buttons=1} | Out-Null
                Send-CDP 'Input.dispatchMouseEvent' @{type='mouseReleased';x=$point.end;y=$point.y;button='left';buttons=0;clickCount=1} | Out-Null
                $selection=Send-CDP 'Runtime.evaluate' @{expression="window.getSelection().toString()";returnByValue=$true}
                if($selection.result.value){throw 'Dragging inside next-boss card selected text'}
                Write-Output 'Native mouse drag leaves next-boss text unselected: OK'
            }
            $fill=Send-CDP 'Runtime.evaluate' @{expression="[...document.querySelectorAll('.schedule-app button[aria-pressed=true],.schedule-app button[aria-selected=true]')].every(n=>{const s=getComputedStyle(n);return s.backgroundClip==='border-box'&&s.backgroundOrigin==='border-box'&&s.borderImageSource===document.documentElement.style.getPropertyValue('--avengers-selected-frame')})";returnByValue=$true}
            if(-not $fill.result.value){throw 'Selected button background does not continue through its frame interior'}
            $glow=Send-CDP 'Runtime.evaluate' @{expression="JSON.stringify(['tab-all','rest-filter','fixed-filter'].map(id=>{const s=getComputedStyle(document.getElementById(id));return {id,shadow:s.boxShadow,animation:s.animationName,filter:s.filter,willChange:s.willChange}}))";returnByValue=$true}
            Write-Output $glow.result.value
            foreach($light in ($glow.result.value|ConvertFrom-Json)){
                if(-not $light.shadow.Contains('inset') -or $light.animation -ne 'none' -or $light.filter -ne 'none' -or $light.willChange -ne 'auto'){throw 'Primary button glow missing or continuously animated'}
            }
            $layout=Send-CDP 'Runtime.evaluate' @{expression="(()=>{const h=document.querySelector('.hero'),r=h.getBoundingClientRect(),before=getComputedStyle(h,'::before'),after=getComputedStyle(h,'::after'),s=getComputedStyle(h),time=document.querySelector('#next-name time').getBoundingClientRect(),other=document.querySelector('#events .event-row.type-boss time').getBoundingClientRect(),title=document.querySelector('.hero-top').getBoundingClientRect(),count=document.getElementById('next-countdown'),range=document.createRange();range.selectNodeContents(count);const clock=range.getBoundingClientRect(),helmetRight=r.left+parseFloat(s.borderLeftWidth)+parseFloat(before.left)+parseFloat(before.width),helmetTop=r.bottom-parseFloat(s.borderBottomWidth)-parseFloat(before.bottom)-parseFloat(before.height),reactorLeft=r.right-parseFloat(s.borderRightWidth)-parseFloat(after.right)-parseFloat(after.width),rest=document.getElementById('rest-filter').getBoundingClientRect();return JSON.stringify({indent:Math.abs(time.left-other.left),titleClear:title.bottom<=helmetTop+1,clockClear:clock.left>=helmetRight-1&&clock.right<=reactorLeft+1,daySize:['day-today','day-next'].every(id=>{const b=document.getElementById(id).getBoundingClientRect();return Math.abs(b.width-rest.width)<1&&Math.abs(b.height-rest.height)<1})})})()";returnByValue=$true}
            $layoutReport=$layout.result.value|ConvertFrom-Json
            if($layoutReport.indent -gt 1 -or -not $layoutReport.titleClear -or -not $layoutReport.clockClear -or -not $layoutReport.daySize){throw $layout.result.value}
            Write-Output $layout.result.value
            $heading=Send-CDP 'Runtime.evaluate' @{expression="JSON.stringify({allHeight:document.getElementById('all-alarm').getBoundingClientRect().height,fixedHeight:document.getElementById('fixed-filter').getBoundingClientRect().height,fill:getComputedStyle(document.getElementById('frame-heading')).backgroundImage,opacity:getComputedStyle(document.getElementById('frame-heading')).opacity})";returnByValue=$true}
            $headingReport=$heading.result.value|ConvertFrom-Json
            if([Math]::Abs($headingReport.allHeight-$headingReport.fixedHeight) -gt 1 -or $headingReport.opacity -ne '1'){throw $heading.result.value}
            Write-Output $heading.result.value
            if($Font15){
                $fit=Send-CDP 'Runtime.evaluate' @{expression="['rest-filter','fixed-filter','tab-theme'].every(id=>{const n=document.getElementById(id),s=getComputedStyle(n),inner=n.getBoundingClientRect().height-parseFloat(s.borderTopWidth)-parseFloat(s.borderBottomWidth)-parseFloat(s.paddingTop)-parseFloat(s.paddingBottom);return ['flex','inline-flex'].includes(s.display)&&s.alignItems==='center'&&s.justifyContent==='center'&&inner>=parseFloat(s.lineHeight)-.1})";returnByValue=$true}
                if(-not $fit.result.value){throw 'Font 15 schedule buttons do not center or fit their line boxes'}
            }
            $capture=Send-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
            [IO.File]::WriteAllBytes((Join-Path $PSScriptRoot "avengers-preview-$width.png"),[Convert]::FromBase64String($capture.data))
            if($width -eq 390){
                $motion=Send-CDP 'Runtime.evaluate' @{expression="JSON.stringify({running:[...document.querySelectorAll('.avengers-reactor-energy')].every(n=>getComputedStyle(n,'::before').animationPlayState==='running'),names:[...document.querySelectorAll('.boss-name')].every(n=>getComputedStyle(n).color===({major:'rgb(255, 113, 91)',fixed:'rgb(121, 232, 255)'}[n.dataset.bossRank]||'rgb(255, 242, 174)')),shell:getComputedStyle(document.querySelector('.schedule-app'),'::before').borderImageSource.includes('blob:')})";returnByValue=$true}
                $m=$motion.result.value|ConvertFrom-Json
                if(-not $m.running -or -not $m.names -or -not $m.shell){throw $motion.result.value}
                # Freeze pulsing to prove that the rotating layer itself changes painted pixels.
                $ring=Send-CDP 'Runtime.evaluate' @{expression="(()=>{const n=document.querySelector('.avengers-hero-energy');window.ringProbe=document.createElement('style');ringProbe.textContent='.avengers-hero-energy::after{animation:none!important;opacity:.5!important}';document.head.append(ringProbe);const r=n.getBoundingClientRect();return JSON.stringify({x:r.x,y:r.y,width:r.width,height:r.height,scale:1,transform:getComputedStyle(n,'::before').transform})})()";returnByValue=$true}
                $ringData=$ring.result.value|ConvertFrom-Json
                $ringClip=@{x=$ringData.x;y=$ringData.y;width=$ringData.width;height=$ringData.height;scale=1}
                $ringFirst=Send-CDP 'Page.captureScreenshot' @{format='png';clip=$ringClip}
                Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,1100))';awaitPromise=$true} | Out-Null
                $ringNext=Send-CDP 'Page.captureScreenshot' @{format='png';clip=$ringClip}
                $ringTransform=Send-CDP 'Runtime.evaluate' @{expression="getComputedStyle(document.querySelector('.avengers-hero-energy'),'::before').transform";returnByValue=$true}
                Write-Output (ConvertTo-Json -Compress @{first=$ringData.transform;next=$ringTransform.result.value;pixelsChanged=($ringFirst.data -ne $ringNext.data);clip=$ringClip})
                [IO.File]::WriteAllBytes((Join-Path $PSScriptRoot 'avengers-reactor-motion-0.png'),[Convert]::FromBase64String($ringFirst.data))
                [IO.File]::WriteAllBytes((Join-Path $PSScriptRoot 'avengers-reactor-motion-1.png'),[Convert]::FromBase64String($ringNext.data))
                if($ringFirst.data -eq $ringNext.data -or $ringData.transform -eq $ringTransform.result.value){throw 'Reactor rotation must visibly change pixels while pulse is frozen'}
                Send-CDP 'Runtime.evaluate' @{expression='ringProbe.remove()'} | Out-Null
                Write-Output 'Reactor rotation changes actual painted pixels independently of pulse: OK'
                $size=Send-CDP 'Page.getLayoutMetrics'
                $full=Send-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$true;clip=@{x=0;y=0;width=390;height=$size.cssContentSize.height;scale=1}}
                [IO.File]::WriteAllBytes((Join-Path $PSScriptRoot 'avengers-preview-full.png'),[Convert]::FromBase64String($full.data))
                Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=480;deviceScaleFactor=1;mobile=$true} | Out-Null
                Send-CDP 'Runtime.evaluate' @{expression='window.scrollTo(0,document.documentElement.scrollHeight);new Promise(r=>setTimeout(r,300))';awaitPromise=$true} | Out-Null
                $off=Send-CDP 'Runtime.evaluate' @{expression="JSON.stringify({scroll:scrollY,height:innerHeight,items:[...document.querySelectorAll('.avengers-reactor-energy')].map(n=>({inView:n.getAttribute('data-in-view'),top:n.getBoundingClientRect().top,bottom:n.getBoundingClientRect().bottom,state:getComputedStyle(n,'::before').animationPlayState}))})";returnByValue=$true}
                $offReport=$off.result.value|ConvertFrom-Json
                if(($offReport.items|Where-Object {$_.state -ne 'paused'}).Count){throw $off.result.value}
                Send-CDP 'Emulation.setEmulatedMedia' @{features=@(@{name='prefers-reduced-motion';value='reduce'})} | Out-Null
                Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=1100;deviceScaleFactor=1;mobile=$true} | Out-Null
                Send-CDP 'Runtime.evaluate' @{expression='window.scrollTo(0,0);new Promise(r=>setTimeout(r,200))';awaitPromise=$true} | Out-Null
                $reduce=Send-CDP 'Runtime.evaluate' @{expression="[...document.querySelectorAll('.avengers-reactor-energy')].every(n=>getComputedStyle(n,'::before').animationName==='avengers-reactor-turn'&&getComputedStyle(n,'::before').animationDuration==='12s'&&getComputedStyle(n,'::after').animationName==='none')";returnByValue=$true}
                if(-not $reduce.result.value){throw 'Reduced motion must use slow ring rotation without pulsing'}
                $slowFirst=Send-CDP 'Page.captureScreenshot' @{format='png';clip=$ringClip}
                Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,1100))';awaitPromise=$true} | Out-Null
                $slowNext=Send-CDP 'Page.captureScreenshot' @{format='png';clip=$ringClip}
                if($slowFirst.data -eq $slowNext.data){throw 'Slow rotation must remain visible with reduced motion enabled'}
                Send-CDP 'Emulation.setEmulatedMedia' @{features=@(@{name='prefers-reduced-motion';value='no-preference'})} | Out-Null
                Write-Output 'Outer shell, pale-yellow boss names, visible rotation, off-screen pause and reduced-motion slow ring without pulse: OK'
                if($Font15){
                    Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=1100;deviceScaleFactor=1;mobile=$true} | Out-Null
                    Send-CDP 'Runtime.evaluate' @{expression="window.scrollTo(0,0);document.getElementById('tab-theme').click();new Promise(r=>setTimeout(r,300))";awaitPromise=$true} | Out-Null
                    $fonts=Send-CDP 'Runtime.evaluate' @{expression="(()=>{const c=document.createElement('canvas').getContext('2d');return JSON.stringify({family:getComputedStyle(document.body).fontFamily,selected:document.getElementById('web-font-select').value,controls:['theme-avengers','theme-ivory','web-font-select','web-font-size-plus','web-font-size-default'].map(id=>{const n=document.getElementById(id),s=getComputedStyle(n),text=n.tagName==='SELECT'?n.selectedOptions[0].textContent:n.textContent;c.font=s.fontWeight+' '+s.fontSize+' '+s.fontFamily;const m=c.measureText(text);return{id,height:n.getBoundingClientRect().height,line:s.lineHeight,padding:s.padding,border:s.borderWidth,ascent:m.actualBoundingBoxAscent,descent:m.actualBoundingBoxDescent,fontAscent:m.fontBoundingBoxAscent,fontDescent:m.fontBoundingBoxDescent}})})})()";returnByValue=$true}
                    Write-Output $fonts.result.value
                    $fontReport=$fonts.result.value|ConvertFrom-Json
                    if($fontReport.selected -ne 'cafe24-dongdong' -or -not $fontReport.family.Contains('BossTimerFont_cafe24_dongdong')){throw 'Font 15 was not actually loaded'}
                    $fit=Send-CDP 'Runtime.evaluate' @{expression="['theme-avengers','theme-ivory','web-font-select','web-font-size-plus','web-font-size-default'].every(id=>{const n=document.getElementById(id),s=getComputedStyle(n),inner=n.getBoundingClientRect().height-parseFloat(s.borderTopWidth)-parseFloat(s.borderBottomWidth)-parseFloat(s.paddingTop)-parseFloat(s.paddingBottom);return inner>=(parseFloat(s.lineHeight)||parseFloat(s.fontSize)*1.2)-.1})&&parseFloat(getComputedStyle(document.getElementById('web-font-size-plus')).fontSize)>=24";returnByValue=$true}
                    if(-not $fit.result.value){throw 'Font 15 settings controls clip their line boxes or symbols are too small'}
                    $capture=Send-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
                    [IO.File]::WriteAllBytes((Join-Path $PSScriptRoot 'avengers-font-settings.png'),[Convert]::FromBase64String($capture.data))
                }
            }
        }
    }
    if($HelpChrome){
        Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=1000;height=1000;deviceScaleFactor=1;mobile=$false} | Out-Null
        Send-CDP 'Page.navigate' @{url=($Url.TrimEnd('/')+'/preview')} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(r,2000)).then(()=>{document.getElementById('tab-theme').click();document.getElementById('theme-help-button').scrollIntoView({block:'center'})})";awaitPromise=$true} | Out-Null
        $position=Send-CDP 'Runtime.evaluate' @{expression="(()=>{const r=document.getElementById('theme-help-button').getBoundingClientRect();return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2})})()";returnByValue=$true}
        $point=$position.result.value|ConvertFrom-Json
        Send-CDP 'Input.dispatchMouseEvent' @{type='mouseMoved';x=$point.x;y=$point.y} | Out-Null
        $help=Send-CDP 'Runtime.evaluate' @{expression="(()=>{const b=document.getElementById('theme-help-button'),p=document.getElementById('hidden-help-balloon'),s=getComputedStyle(b),a=getComputedStyle(p);return JSON.stringify({expanded:b.getAttribute('aria-expanded'),buttonRadius:s.borderRadius,border:s.borderTopColor,popupRadius:a.borderRadius,frame:a.borderImageSource.includes('blob:'),arrow:getComputedStyle(p,'::before').display,panels:p.querySelectorAll('.hidden-help-panel:not([hidden])').length,errors:window.previewErrors})})()";returnByValue=$true}
        Write-Output $help.result.value
        $value=$help.result.value|ConvertFrom-Json
        if($value.expanded -ne 'true' -or $value.buttonRadius -ne '0px' -or $value.border -ne 'rgba(0, 0, 0, 0)' -or $value.popupRadius -ne '0px' -or -not $value.frame -or $value.arrow -ne 'none' -or $value.panels -ne 1 -or $value.errors.Count){throw 'Help rollover has mixed rounded chrome or duplicate content'}
        $capture=Send-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
        [IO.File]::WriteAllBytes((Join-Path $PSScriptRoot 'avengers-help-preview.png'),[Convert]::FromBase64String($capture.data))
    }
    if($Cinematic){
        $seedCaches=Send-CDP 'Runtime.evaluate' @{expression="(async()=>{await caches.delete('BossTimer.cinematic.v2');const old=await caches.open('BossTimer.cinematic.v1');await old.put('/cinematic/stale.mp4',new Response('old'));const keep=await caches.open('BossTimer.fixture.keep');await keep.put('/keep',new Response('keep'));return true})()";awaitPromise=$true;returnByValue=$true}
        if($seedCaches.exceptionDetails){throw ($seedCaches.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
        # Seed only this isolated test document; production chooses a random first movie.
        $catalogue=(Invoke-RestMethod ($Url.TrimEnd('/')+'/cinematic/catalog.json')).clips
        $seed=([double](0.25/$catalogue.Count)).ToString('R',[Globalization.CultureInfo]::InvariantCulture)
        $cacheProbe=@'
window.cinematicCacheProbe={bodyReads:0};
(()=>{const tagged=new WeakSet(),match=Cache.prototype.match,blob=Response.prototype.blob;
Cache.prototype.match=async function(...args){const r=await match.apply(this,args);if(r)tagged.add(r);return r;};
Response.prototype.blob=function(...args){if(tagged.has(this))++cinematicCacheProbe.bodyReads;return blob.apply(this,args);};})();
'@
        Send-CDP 'Page.addScriptToEvaluateOnNewDocument' @{source=("Math.random=()=>($seed);"+$cacheProbe)} | Out-Null
        Send-CDP 'Emulation.setEmulatedMedia' @{features=@(@{name='prefers-reduced-motion';value='reduce'})} | Out-Null
        Send-CDP 'Emulation.setTimezoneOverride' @{timezoneId='Asia/Seoul'} | Out-Null
        Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=1100;deviceScaleFactor=1;mobile=$true} | Out-Null
        Send-CDP 'Page.navigate' @{url=($Url.TrimEnd('/')+'/preview?font=15')} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(r,2000)).then(()=>new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='/cinematic-check.js';s.onload=resolve;s.onerror=reject;document.head.append(s)}))";awaitPromise=$true} | Out-Null
        $checks=Send-CDP 'Runtime.evaluate' @{expression='runCinematicChecks().then(v=>JSON.stringify(v))';awaitPromise=$true;returnByValue=$true;userGesture=$true}
        $decode=Send-CDP 'Runtime.evaluate' @{expression='JSON.stringify(globalThis.cinematicDecodeReport||[])';returnByValue=$true}
        [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'docs/cinematic_decode_audit.json'),$decode.result.value,[Text.UTF8Encoding]::new($false))
        if($checks.exceptionDetails){throw ($checks.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
        Write-Output $checks.result.value
        $capture=Send-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
        [IO.File]::WriteAllBytes((Join-Path $PSScriptRoot 'avengers-cinematic-hold.png'),[Convert]::FromBase64String($capture.data))
        $stored=Send-CDP 'Runtime.evaluate' @{expression="(async()=>JSON.stringify((await(await caches.open('BossTimer.cinematic.v2')).keys()).map(r=>new URL(r.url).pathname)))()";awaitPromise=$true;returnByValue=$true}
        $cachedPaths=@($stored.result.value|ConvertFrom-Json)
        Send-CDP 'Page.reload' @{ignoreCache=$true} | Out-Null
        $cached=Send-CDP 'Runtime.evaluate' @{expression="new Promise((resolve,reject)=>{const until=Date.now()+15000;const poll=()=>{const s=document.getElementById('cinematic-status')?.textContent||'',m=s.match(/(\d+)\/(\d+)/);if(m&&+m[1]>0&&m[1]===m[2])resolve(JSON.stringify({status:s,moviePaths:performance.getEntriesByType('resource').filter(e=>/\/cinematic\/[0-9a-f]+\.mp4$/.test(e.name)).map(e=>new URL(e.name).pathname),videoSource:document.getElementById('ironman-cinematic-video').getAttribute('src'),cacheBodyReads:cinematicCacheProbe.bodyReads}));else if(Date.now()>until)reject(Error('persistent cache reload timeout: '+s));else setTimeout(poll,50)};poll()})";awaitPromise=$true;returnByValue=$true}
        if($cached.exceptionDetails){throw ($cached.exceptionDetails|ConvertTo-Json -Depth 8 -Compress)}
        $cacheReport=$cached.result.value|ConvertFrom-Json
        if(@($cacheReport.moviePaths|Where-Object {$cachedPaths -contains $_}).Count -or $cacheReport.videoSource -or $cacheReport.cacheBodyReads){throw 'Reload should reuse cached movie keys without downloads, decoding or reading every movie body'}
        Write-Output $cached.result.value
    }
    if($CinematicAudio){
        $startAudio=@'
(()=>{const api=WebIronmanCinematic;api.enable(true,()=>true);const r={id:'audio-check-'+Date.now(),at:Date.now()+3600000,kind:'boss'};api.update(Date.now(),r,[r],'audio-check');document.getElementById('notice-sound-enabled').checked=true;document.getElementById('notice-sound-volume').value='35';return api.previewNext()})()
'@
        Send-CDP 'Runtime.evaluate' @{expression=$startAudio;userGesture=$true;awaitPromise=$true} | Out-Null
        $audible=Send-CDP 'Runtime.evaluate' @{expression="new Promise((resolve,reject)=>{const end=Date.now()+3000;const check=()=>{const v=document.getElementById('ironman-cinematic-video');if(!v.paused&&!v.muted)resolve(true);else if(Date.now()>end)reject(Error('native audible movie did not start'));else setTimeout(check,25)};check()})";awaitPromise=$true;returnByValue=$true}
        if($audible.exceptionDetails){throw ($audible.exceptionDetails|ConvertTo-Json -Depth 6 -Compress)}
        $ownership=Send-CDP 'Runtime.evaluate' @{expression="(()=>{window.audioPeer=new BroadcastChannel('BossTimer.cinematic.audio.v1');audioPeer.postMessage({type:'claim',id:'other-window',at:Date.now()+1000});return new Promise(r=>setTimeout(()=>{WebIronmanCinematic.refreshAudio();r(document.getElementById('ironman-cinematic-video').muted)},150))})()";awaitPromise=$true;returnByValue=$true}
        if(-not $ownership.result.value){throw 'Another cinematic window must silence the previous audible channel'}
        $duplicate=Send-CDP 'Runtime.evaluate' @{expression="(()=>{const original=WebIronmanCinematic;return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='/cinematic/player.js';s.onload=()=>resolve(WebIronmanCinematic===original&&document.querySelectorAll('#ironman-cinematic-video').length===1);s.onerror=reject;document.head.append(s)})})()";awaitPromise=$true;returnByValue=$true}
        if(-not $duplicate.result.value){throw 'Duplicate runtime must not create an unmanaged video player'}
        Send-CDP 'Runtime.evaluate' @{expression="document.getElementById('ironman-cinematic-video').style.display='none'"} | Out-Null
        $invisible=Send-CDP 'Runtime.evaluate' @{expression="new Promise((resolve,reject)=>{const end=Date.now()+2500;const check=()=>{const v=document.getElementById('ironman-cinematic-video');if(v.paused&&v.muted)resolve(true);else if(Date.now()>end)reject(Error('CSS-hidden movie kept playing'));else setTimeout(check,25)};check()})";awaitPromise=$true;returnByValue=$true}
        if($invisible.exceptionDetails){throw ($invisible.exceptionDetails|ConvertTo-Json -Depth 6 -Compress)}
        Send-CDP 'Runtime.evaluate' @{expression="document.getElementById('ironman-cinematic-video').style.removeProperty('display');audioPeer.close();"} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression=$startAudio;userGesture=$true;awaitPromise=$true} | Out-Null
        $stall=Send-CDP 'Runtime.evaluate' @{expression="new Promise((resolve,reject)=>{const v=document.getElementById('ironman-cinematic-video'),end=Date.now()+5500;let blocked=false;const check=()=>{if(!blocked&&!v.paused&&!v.muted){blocked=true;window.nativeFrameCallback=v.requestVideoFrameCallback.bind(v);v.requestVideoFrameCallback=()=>2147000000;}if(blocked&&!v.hasAttribute('src')&&v.muted){v.requestVideoFrameCallback=nativeFrameCallback;resolve(true);}else if(Date.now()>end)reject(Error('movie with stalled presented frames kept audio alive'));else setTimeout(check,25)};check()})";awaitPromise=$true;returnByValue=$true}
        if($stall.exceptionDetails){throw ($stall.exceptionDetails|ConvertTo-Json -Depth 6 -Compress)}
        Write-Output 'Audible native video: foreign-channel silence, singleton runtime, CSS-hidden pause and mid-play frame-stall cleanup passed'
    }
    if($CustomHeader){
        Send-CDP 'Emulation.setTimezoneOverride' @{timezoneId='Asia/Seoul'} | Out-Null
        Send-CDP 'Page.navigate' @{url=($Url.TrimEnd('/')+'/preview?font=15')} | Out-Null
        Send-CDP 'Runtime.evaluate' @{expression="new Promise(r=>setTimeout(r,1800)).then(()=>{document.getElementById('tab-custom').click();document.getElementById('day-next').click()})";awaitPromise=$true} | Out-Null
        foreach($width in @(500,390,320,280,1440)){
            Send-CDP 'Emulation.setDeviceMetricsOverride' @{width=$width;height=1100;deviceScaleFactor=1;mobile=($width -lt 760)} | Out-Null
            Send-CDP 'Runtime.evaluate' @{expression='new Promise(r=>setTimeout(r,200))';awaitPromise=$true} | Out-Null
            $expression=@'
(()=>{const ids=['schedule-title','clock','tomorrow-date','custom-open','all-alarm'];
 const rect=n=>{const r=n.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom}};
 const boxes=Object.fromEntries(ids.map(id=>[id,rect(document.getElementById(id))]));
 const overlaps=ids.some((id,i)=>ids.slice(i+1).some(other=>{const a=boxes[id],b=boxes[other];return Math.min(a.right,b.right)>Math.max(a.x,b.x)+1&&Math.min(a.bottom,b.bottom)>Math.max(a.y,b.y)+1}));
 const textFit=ids.every(id=>{const n=document.getElementById(id),r=document.createRange();r.selectNodeContents(n);const a=r.getBoundingClientRect(),b=n.getBoundingClientRect();return a.left>=b.left-1&&a.right<=b.right+1&&a.top>=b.top-1&&a.bottom<=b.bottom+1});
 const button=document.getElementById('custom-open'),font=getComputedStyle(document.body).fontFamily;
 return JSON.stringify({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,boxes,overlaps,textFit,marker:!document.getElementById('tomorrow-date').hidden,settingsInHeader:button.parentElement.id==='custom-clock-actions',font,errors:window.previewErrors})})()
'@
            $result=Send-CDP 'Runtime.evaluate' @{expression=$expression;returnByValue=$true}
            Write-Output $result.result.value
            $report=$result.result.value|ConvertFrom-Json
            if($report.scrollWidth -gt $width -or $report.overlaps -or -not $report.textFit -or -not $report.marker -or -not $report.settingsInHeader -or $report.boxes.'custom-open'.h -lt 40 -or $report.errors.Count){throw 'Custom header overflow, overlap or missing real selection/font fixture'}
            if($width -eq 390 -and $report.boxes.'all-alarm'.y -le $report.boxes.'custom-open'.y+1){throw 'Alarm should wrap before settings'}
            if($width -eq 320 -and [Math]::Abs($report.boxes.'all-alarm'.y-$report.boxes.'custom-open'.y) -gt 2){throw 'Narrow settings should share the alarm row'}
            $capture=Send-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
            [IO.File]::WriteAllBytes((Join-Path $PSScriptRoot "avengers-custom-$width.png"),[Convert]::FromBase64String($capture.data))
        }
        Send-CDP 'Runtime.evaluate' @{expression="document.getElementById('custom-open').click();window.customEditorOpened=!document.getElementById('custom-editor').hidden;document.getElementById('custom-close').click()"} | Out-Null
        $click=Send-CDP 'Runtime.evaluate' @{expression="window.customEditorOpened&&document.getElementById('custom-editor').hidden&&document.activeElement.id==='custom-open'";returnByValue=$true}
        if(-not $click.result.value){throw 'Reflowed settings button did not open/close and restore focus'}
    }
} finally {
    if($socket.State -eq [Net.WebSockets.WebSocketState]::Open){Send-CDP 'Browser.close' | Out-Null}
    $socket.Dispose()
}

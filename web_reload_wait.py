"""Shared, CSP-approved countdown for rejected document reloads."""
import base64
import hashlib


SCRIPT = r"""(()=>{'use strict';
  const seconds=Number(document.documentElement.dataset.retrySeconds);
  const milliseconds=Number(document.documentElement.dataset.retryMs || seconds*1000);
  if(!Number.isFinite(milliseconds) || milliseconds<1 || milliseconds>300000)return;
  const deadline=Date.now()+milliseconds;
  let timer=0,navigating=false;
  function tick(){
    clearTimeout(timer);timer=0;
    if(navigating)return;
    const wait=Math.max(0,deadline-Date.now());
    const count=document.getElementById('reload-wait-count');
    if(count)count.textContent=String(Math.ceil(wait/1000));
    if(!wait){navigating=true;window.location.replace('/');return;}
    if(!document.hidden)timer=setTimeout(tick,Math.min(wait,wait%1000 || 1000));
  }
  document.addEventListener('visibilitychange',tick);
  document.addEventListener('DOMContentLoaded',tick);
  window.addEventListener('pageshow',tick);
  window.addEventListener('pagehide',()=>{clearTimeout(timer);timer=0;});
  tick();
})();""".encode('utf-8')

SCRIPT_SOURCE = "'sha256-" + base64.b64encode(hashlib.sha256(SCRIPT).digest()).decode('ascii') + "'"


def render_reload_wait(seconds):
    import math
    if type(seconds) not in {int, float} or not 0 < seconds <= 300:
        raise ValueError('invalid reload wait')
    milliseconds = math.ceil(seconds * 1000)
    seconds = math.ceil(seconds)
    return (f'<!doctype html><html lang="ko" data-retry-seconds="{seconds}" data-retry-ms="{milliseconds}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>새로고침 대기</title><script>').encode('utf-8') + SCRIPT + (
            f'</script><link rel="stylesheet" href="/style.css">'
            f'<noscript><meta http-equiv="refresh" content="{seconds};url=/"></noscript>'
            f'</head><body><main>'
            f'<h1>새로고침 대기</h1><p>접속 순서에 따라 자동으로 갱신합니다. 새로고침은 5초에 한 번 가능합니다.</p>'
            f'<p role="status">남은 대기 시간 <span id="reload-wait-count">{seconds}</span>초</p>'
            f'<p>남은 시간이 끝나면 자동으로 열립니다.</p>'
            f'</main></body></html>').encode('utf-8')

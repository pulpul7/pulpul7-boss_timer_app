"""Private desktop/worker control plane for a persistent web listener.

No public control endpoint, GUI imports, plaintext credentials or PID-name kills.
The worker owns router rules for its entire lifetime, including GUI reconnects.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from functools import lru_cache
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import quote, urlencode
from urllib.request import Request

from web_service_common import read_json, write_json, worker_paths, diagnostic
from web_direct_hosting import DirectHostingService

RUNTIME_PROTOCOL = 1


def control_path(root, run_id):
    return root / ('runtime_control.' + run_id + '.json')


@contextmanager
def independent_worker_spawn():
    """Opt out only this worker from debugpy's injection and launcher job.

    CREATE_NEW_PROCESS_GROUP alone does not escape the launcher's kill-on-close
    job. Its BREAKAWAY_OK job explicitly permits independent child processes.
    The pydevd context is thread-local: other children keep their debug settings.
    Never import or enable a debugger in normal packaged/source execution.
    """
    monkey = sys.modules.get('_pydev_bundle.pydev_monkey')
    skip = getattr(monkey, 'skip_subprocess_arg_patch', None)
    flags = getattr(subprocess,'CREATE_NO_WINDOW',0) | getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    if os.name == 'nt' and monkey is not None:
        flags |= getattr(subprocess,'CREATE_BREAKAWAY_FROM_JOB',0x01000000)
    with skip() if callable(skip) else nullcontext():
        yield flags


@lru_cache(maxsize=1)
def runtime_version():
    """Content identity, independent of install directory and file timestamps."""
    root = Path(__file__).resolve().parent
    files = [Path(sys.executable)] if getattr(sys, 'frozen', False) else sorted(root.glob('web_*.py'))
    digest = hashlib.sha256()
    for path in files:
        if not getattr(sys, 'frozen', False):
            digest.update(path.name.encode())
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
    return digest.hexdigest()


class AttachedProcess:
    """Retain a process handle; PID reuse must never target a different process."""
    def __init__(self, pid):
        if type(pid) is not int or pid <= 0 or pid == os.getpid():
            raise ValueError('invalid web process')
        self.pid = pid
        self.handle = None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            for name, args, result in (
                ('OpenProcess', (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
                ('WaitForSingleObject', (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD),
                ('TerminateProcess', (wintypes.HANDLE, wintypes.UINT), wintypes.BOOL),
                ('CloseHandle', (wintypes.HANDLE,), wintypes.BOOL)):
                fn = getattr(self.kernel, name); fn.argtypes = args; fn.restype = result
            self.handle = self.kernel.OpenProcess(0x00100001, False, pid)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())

    def poll(self):
        if self.handle:
            return None if self.kernel.WaitForSingleObject(self.handle, 0) == 0x102 else 0
        try:
            os.kill(self.pid, 0)
            return None
        except ProcessLookupError:
            return 0

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout if timeout is not None else 86400)
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('owned web worker', timeout)
            time.sleep(.05)
        return 0

    def terminate(self):
        if self.handle:
            if not self.kernel.TerminateProcess(self.handle, 1):
                raise OSError('web process termination failed')
        else:
            import signal
            os.kill(self.pid, signal.SIGTERM)

    kill = terminate

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle); self.handle = None


def discover(root):
    try:
        data = read_json(root / 'persistent_worker.json')
        run_id = data['run_id']
        if (data.get('runtime_protocol') != RUNTIME_PROTOCOL or not isinstance(run_id, str)
                or len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id)):
            raise ValueError('unsupported persistent web runtime')
        state = read_json(worker_paths(root, run_id)[0])
        checked = datetime.fromisoformat(state['checked_at']).timestamp()
        if (state.get('run_id') != run_id or state.get('pid') != data.get('pid')
                or state.get('port') != data.get('port') or state.get('state') != 'running'
                or type(state.get('port')) is not int or not 1024<=state['port']<=65535
                or not -3 <= time.time() - checked < 12):
            return None
        return state
    except FileNotFoundError:
        return None


class RemotePublicService(DirectHostingService):
    """GUI facade. Only the web worker creates or removes network resources."""
    def __init__(self, manager):
        super().__init__(manager.root, manager._public_context)
        self.manager = manager
        self.last_control = 0
        self.controller_id = uuid.uuid4().hex

    def _ensure_thread(self):
        pass

    def status(self):
        with self.lock:
            return deepcopy(self.view)

    def configure(self, enabled):
        with self.lock:
            self.settings['enabled'] = bool(enabled)
            write_json(self.path, self.settings)
            self.view.update(enabled=bool(enabled), busy=True)
        self.send(force=True)

    def set_active(self, active):
        self.active = bool(active)
        self.send()

    def worker_stopped(self, process):
        """Replace a stale heartbeat only after process exit and local cleanup proof."""
        if process is not self.manager.process or process.poll() is None:
            return False
        try:
            if self.resources_path.exists():
                raise ValueError('public resources remain')
            try:
                listener = read_json(self.root / 'public_listener.json')
            except FileNotFoundError:
                listener = None
            if listener is not None and (listener.get('schema') != 1 or listener.get('enabled') is not False):
                raise ValueError('public listener shutdown unverified')
        except (OSError, ValueError) as exc:
            with self.lock:
                self.view.update(state='error', busy=False, address='',
                    message='웹 프로세스는 종료됐지만 외부 공개 규칙 정리를 확인하지 못했습니다. 진단 로그를 확인해주세요.')
            self.manager._log('web_public_stop_unverified', error_type=type(exc).__name__)
            return False
        with self.lock:
            self.active = False
            self.view.update(state='off', busy=False, enabled=self.settings['enabled'], address='', external_port=0,
                firewall='공개 규칙 해제', mapping='공개 규칙 해제', https='로컬 전용', external='외부 공개 중지',
                message='웹 종료 완료 · 웹실행으로 다시 시작할 수 있습니다.')
        self.manager._log('web_public_stop_cache_cleared')
        return True

    def send(self, *, force=False, detach=False):
        m = self.manager
        if not m.run_id or not m.process or m.closed.is_set() and not detach:
            return
        if not force and time.monotonic() - self.last_control < 1:
            return
        if getattr(m, 'background_protected', False):
            # A worker's observed status is not an OFF command. Keep the saved
            # publication intent across transient lease/network recovery.
            active = m.desired and not m.public_paused
        else:
            active = self.active
        write_json(control_path(self.root, m.run_id), dict(schema=RUNTIME_PROTOCOL,
            run_id=m.run_id, controller_id=self.controller_id, controller_pid=os.getpid(),
            controller_until=0 if detach else time.time()+5, public_active=active,
            keep_background=getattr(m,'background_protected',False),
            public_enabled=self.settings['enabled'], external_port=m.settings.get('external_port')))
        self.last_control = time.monotonic()

    def close(self, *, wait=False):
        self.closed.set()  # Never remove worker-owned resources from the GUI.


class RemoteRecord:
    """Small, bounded GitHub contents client; credentials stay in user DPAPI."""
    def __init__(self, config):
        from web_secret_store import unprotect_secret
        self.config = config
        self.token = unprotect_secret(config['protected_token'])

    def request(self, method, payload=None):
        from https_transport import urlopen_verified
        c = self.config
        url = 'https://api.github.com/repos/' + quote(c['owner'], safe='') + '/' + quote(c['repo'], safe='')
        url += '/contents/' + quote(c['path'], safe='/')
        if method == 'GET':
            url += '?' + urlencode(dict(ref=c['branch']))
        request = Request(url, method=method, data=None if payload is None else json.dumps(payload).encode(),
            headers={'User-Agent':'BossTimer-Web-Runtime','Accept':'application/vnd.github+json',
                     'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        with urlopen_verified(request, timeout=4) as response:
            raw = response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:
            raise ValueError('authority response too large')
        return json.loads(raw)

    def read(self):
        data = self.request('GET')
        content = json.loads(base64.b64decode(data['content']))
        if not isinstance(content, dict) or str(content.get('scope',{}).get('guild')) != self.config['guild']:
            raise ValueError('web authority scope changed')
        return content, data['sha']

    def write(self, content, sha):
        self.request('PUT', dict(branch=self.config['branch'],sha=sha,
            message='BossTimer background web state',
            content=base64.b64encode(json.dumps(content,ensure_ascii=False).encode()).decode()))


class WorkerRuntime:
    def __init__(self, root, run_id, logger, context):
        from web_ddns_service import DDNSService
        from web_certificate_service import CertificateService
        self.root, self.run_id, self.logger, self.context = root, run_id, logger, context
        self.public = DirectHostingService(root, context)
        self.ddns, self.certificate = DDNSService(root), CertificateService(root)
        self.control = {}; self.detached = False
        self.remote_valid_until = 0; self.remote_stop = False
        self.closed = threading.Event(); self.lock = threading.RLock()
        self.remote_thread = None; self.next_remote = 0; self.record = None
        self.config_signature = None; self.config = None; self.next_marker = 0
        self.stop_requested = False
        self.service_signatures = {}
        self.remote_failures = 0
        self.remote_initialized = False

    def _load_config(self):
        path = self.root / ('background.'+self.run_id+'.json')
        signature = (path.stat().st_mtime_ns,path.stat().st_size)
        if signature != self.config_signature:
            config = read_json(path)
            if config.get('run_id') != self.run_id or config.get('schema') != 1:
                raise ValueError('invalid background binding')
            self.config, self.record = config, RemoteRecord(config)
            self.config_signature = signature
            self.next_marker = 0
        return self.config

    def _remote_check(self):
        try:
            from web_standalone_hosting import web_host_stamp
            c = self._load_config()
            data, sha = self.record.read()
            if not self._still_background(c):
                return
            stamp = (c['client'],c['runtime'],c['web_id'])
            owned = web_host_stamp(data) == stamp
            pending = data.get('web_operation') or {}
            marker = data.get('web_background') or {}
            stopping = (marker.get('stop_requested') or
                pending.get('owner') and pending.get('owner') != c['client'] and pending.get('expires',0)>time.time())
            if not owned or stopping:
                self.remote_stop = True
                self.remote_valid_until = 0
                write_json(self.root/'authority_lease.json',dict(schema=1,enabled=False,run_id=self.run_id,valid_until=0))
                self.ddns.set_active(False)
                self.certificate.set_active(False)
                self.public.set_active(False)
                deadline = time.monotonic()+35
                while (self.public.status().get('state') != 'off' or self.public.status().get('busy')
                       or self.ddns.status().get('busy')):
                    if self.closed.wait(.1) or time.monotonic()>deadline:
                        raise ValueError('background web stop incomplete')
                # Re-read before acknowledging: never erase a newer owner's record.
                data, sha = self.record.read()
                if web_host_stamp(data) == stamp:
                    data['web_standalone']['state']='stopped'
                    member=data.setdefault('members',{}).setdefault(c['client'],{})
                    member.update(web_closed_runtime=c['runtime'],web_closed_at=time.time())
                    data.pop('web_background',None)
                    self.record.write(data,sha)
                self.stop_requested = True
                return
            self.remote_valid_until=time.monotonic()+15
            self.remote_initialized=True
            self.remote_stop=False
            self.remote_failures=0
            if time.monotonic()>=self.next_marker:
                data['web_background']=dict(protocol=1,owner=c['client'],runtime=c['runtime'],id=c['web_id'],
                    valid_until=time.time()+90)
                try:
                    self.record.write(data,sha)
                    self.next_marker=time.monotonic()+30
                except Exception as exc:
                    # An optimistic-write conflict does not undo the owner we
                    # just read. Retry the marker without extending its expiry.
                    self.next_marker=time.monotonic()+5
                    diagnostic(self.logger,'web_background_marker_update_failed',error_type=type(exc).__name__)
        except Exception as exc:
            self.remote_failures+=1
            if self.remote_failures==1:
                diagnostic(self.logger,'web_background_authority_check_failed',error_type=type(exc).__name__)
        finally:
            self.next_remote=time.monotonic()+min(30,5*2**min(3,self.remote_failures))

    def _still_background(self, config):
        """Discard a reply from before the new GUI attached or rebound its session."""
        if self.closed.is_set():
            return False
        try:
            current=read_json(self.root/('background.'+self.run_id+'.json'))
            control=read_json(control_path(self.root,self.run_id))
            return (all(current.get(k)==config.get(k) for k in ('client','runtime','web_id'))
                and (control.get('controller_until',0)<time.time() or control.get('keep_background') is True))
        except (OSError,ValueError):
            return False

    def refresh(self):
        from web_authority_gate import external_allowed
        try:
            control=read_json(control_path(self.root,self.run_id))
            busy=(self.control.get('controller_until',0)>time.time()
                  and self.control.get('controller_id')!=control.get('controller_id'))
            if control.get('schema')==1 and control.get('run_id')==self.run_id and not busy:
                self.control=control
        except (OSError,ValueError):
            pass
        self.detached=self.control.get('controller_until',0)<time.time() or self.control.get('keep_background') is True
        if self.detached:
            if (time.monotonic()>=self.next_remote and not self.closed.is_set()
                    and not (self.remote_thread and self.remote_thread.is_alive())):
                self.remote_thread=threading.Thread(target=self._remote_check,name='web-background-authority',daemon=True)
                self.remote_thread.start()
            proven=not self.remote_stop and self.remote_valid_until>time.monotonic()
            # Preserve the GUI's already-verified lease during the first bounded
            # remote check. Never extend that grace without fresh remote proof.
            allowed=proven or (not self.remote_initialized and not self.remote_stop
                               and external_allowed(self.root,self.run_id))
            if proven:
                write_json(self.root/'authority_lease.json',dict(schema=1,enabled=True,run_id=self.run_id,valid_until=time.time()+10))
            elif self.remote_initialized or self.remote_stop:
                write_json(self.root/'authority_lease.json',dict(schema=1,enabled=False,run_id=self.run_id,valid_until=0))
        else:
            allowed=external_allowed(self.root,self.run_id)
        active=bool(allowed and self.control.get('public_active') and self.control.get('public_enabled'))
        if self.public.settings['enabled'] != self.control.get('public_enabled',self.public.settings['enabled']):
            self.public.configure(self.control['public_enabled'])
        # Credential/certificate changes are written by desktop jobs, not cached forever.
        for name,filename in (('ddns','ddns_settings.json'),('certificate','https_settings.json')):
            try:
                signature=hashlib.sha256((self.root/filename).read_bytes()).digest()
            except OSError:
                continue
            if self.service_signatures.get(name)!=signature:
                service=getattr(self,name); service.close()
                setattr(self,name,type(service)(self.root))
            self.service_signatures[name]=signature
        self.public.set_active(active)
        self.ddns.set_active(active and self.public.status()['state']=='prepared')
        self.certificate.set_active(self.detached and active)

    def status(self):
        return dict(runtime_protocol=RUNTIME_PROTOCOL,runtime_version=runtime_version(),
            controller_id=self.control.get('controller_id',''),background=self.detached,
            background_authorized=self.remote_valid_until>time.monotonic() and not self.remote_stop,
            public_status=self.public.status(),ddns_status=self.ddns.status())

    def close(self):
        self.closed.set(); self.ddns.close(); self.certificate.close(); self.public.close(wait=True)
        for service in (self.ddns,self.certificate):
            if service.thread and service.thread is not threading.current_thread():
                service.thread.join(timeout=6)

    def acknowledge_manual_stop(self):
        """After listener cleanup, retire only this worker's exact shared host stamp."""
        from web_standalone_hosting import web_host_stamp
        try:
            if self.remote_thread and self.remote_thread is not threading.current_thread():
                self.remote_thread.join(timeout=10)
                if self.remote_thread.is_alive():
                    raise ValueError('background authority request still running')
            if any(service.thread and service.thread.is_alive()
                   for service in (self.public, self.ddns, self.certificate)):
                raise ValueError('web publication cleanup incomplete')
            config = self._load_config()
            stamp = (config['client'], config['runtime'], config['web_id'])
            for attempt in range(2):
                data, sha = self.record.read()
                if web_host_stamp(data) != stamp:
                    return True  # A newer host must never be overwritten.
                scope = data['scope']
                data['web_standalone'] = dict(protocol=1, state='stopped',
                    owner=stamp[0], runtime=stamp[1], id=stamp[2],
                    **{k: str(scope[k]) for k in ('guild', 'server', 'season')})
                data.pop('web_delegate', None)
                data.pop('web_return_pending', None)
                data.pop('web_background', None)
                member = data.setdefault('members', {}).setdefault(stamp[0], {})
                member.update(web_closed_runtime=stamp[1], web_closed_at=time.time())
                try:
                    self.record.write(data, sha)
                    diagnostic(self.logger, 'web_tray_shared_stop_confirmed', run_id=self.run_id)
                    return True
                except Exception:
                    if attempt:
                        raise
        except Exception as exc:
            diagnostic(self.logger, 'web_tray_shared_stop_pending', error_type=type(exc).__name__)
        return False


def save_background_context(manager, bridge):
    """Called only on the controller thread, never Tk. Persist no plaintext token."""
    from web_standalone_hosting import standalone_info
    from web_secret_store import protect_secret
    coordinator=getattr(bridge.app,'discord_handover',None)
    if coordinator is None or not coordinator.alive or not manager.run_id:
        return
    if manager.background_protected:
        # Web runtime identity survives bot/GUI sessions. Do not rewrite it merely
        # because a new Discord runtime connected: the worker already owns it.
        with coordinator.lock:
            data,_=coordinator.record.read() if not manager.background_scope_verified else (bridge.standalone.record,None)
        old=standalone_info(data)
        if old and old['state']=='active' and old['owner']==coordinator.client and old['id']==manager.background_web_id:
            if old['server']!=str(coordinator.scope['server']) or old['season']!=str(coordinator.scope['season']):
                manager.background_scope_verified=False
                return  # Keep the old site while a different profile is open.
            if not manager.background_scope_verified:
                manager.background_scope_verified=True
                manager.snapshot_version+=1
                bridge.standalone.observe(data,coordinator)
                manager._log('web_background_reconnected',run_id=manager.run_id)
        else:
            manager.background_protected=False
            manager.set_authority((),message='Web owner changed')
            return
    if not manager.publication_allowed() or manager.public.status()['state']!='prepared':
        return
    info=standalone_info(bridge.standalone.record)
    if not info or info['owner']!=coordinator.client or (info['runtime']!=coordinator.runtime and not manager.background_protected):
        return
    settings=bridge.app._get_github_data_settings()
    credential_version=hashlib.sha256(json.dumps(settings,sort_keys=True).encode()).hexdigest()
    binding=(manager.run_id,info['id'],info['runtime'],credential_version)
    if getattr(manager,'background_binding',None)==binding:
        return
    config={k:settings[k] for k in ('owner','repo','branch')}
    config.update(schema=1,run_id=manager.run_id,path=coordinator.record.path,guild=coordinator.scope['guild'],
        client=coordinator.client,runtime=info['runtime'],web_id=info['id'],protected_token=protect_secret(settings['token']))
    write_json(manager.root/('background.'+manager.run_id+'.json'),config)
    def mark(data):
        current=standalone_info(data)
        if not current or current['id']!=info['id'] or current['runtime']!=info['runtime']:
            raise ValueError('web owner changed before background enable')
        data['web_background']=dict(protocol=1,owner=info['owner'],runtime=info['runtime'],id=info['id'],valid_until=time.time()+90)
    with coordinator.lock:
        data=coordinator.record.mutate(mark)
    bridge.standalone.observe(data,coordinator)
    manager.background_binding=binding
    manager.background_web_id=info['id']
    manager._log('web_background_ready',run_id=manager.run_id)

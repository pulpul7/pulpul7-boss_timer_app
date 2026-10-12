"""Explicit web-only hosting. No voice ownership changes or schedule imports."""
from copy import deepcopy
import threading
import time
import uuid

from web_delegated_hosting import delegation_info, valid_token
from web_service_common import read_json
from discord_connection_policy import ConnectionPolicy


def standalone_info(data):
    item = data.get('web_standalone') if isinstance(data, dict) else None
    if (not isinstance(item, dict) or item.get('protocol') != 1
            or item.get('state') not in {'active', 'stopped'}
            or not all(valid_token(item.get(key)) for key in ('id', 'owner', 'runtime'))
            or not all(isinstance(item.get(key), str) and item[key] for key in ('guild', 'server', 'season'))
            or item.get('guild') != str(data.get('scope', {}).get('guild'))):
        return None
    return deepcopy(item)


def web_host(data):
    explicit = standalone_info(data)
    if explicit:
        return explicit['owner'] if explicit['state'] == 'active' else ''
    delegated = delegation_info(data)
    if delegated and delegated['state'] != 'stopped':
        return delegated['host']
    return data.get('owner') or ''


def automatic_delegation(data, client, runtime, scope, generation, request, verified_replacement=None):
    """Return to normal hosting on an explicit configured voice acquisition."""
    info = standalone_info(data)
    if info is None:
        return False, None
    if info['state'] == 'stopped':
        return True, None  # A verified stopped web no longer needs a deny override.
    host = data.get('members', {}).get(info['owner'], {})
    if (host.get('runtime') == info['runtime'] and host.get('online') is False):
        return True, None  # Durable shutdown of that exact session, not a probe timeout.
    if (info['owner'] == client and host.get('runtime') == runtime and host.get('online')
            and info['runtime'] != runtime):
        # This installation has registered its replacement bot session. Its
        # previous local web lease is denied by the new manager; it cannot be
        # retained as a remote host. Missing/unknown remote sessions stay fenced.
        return True, None
    if (verified_replacement == (info['id'], info['owner'], host.get('runtime'))
            and valid_token(host.get('runtime')) and host.get('online')
            and info['runtime'] != host['runtime']):
        return True, None  # The existing authenticated probe found the replacement session.
    if info['server'] != str(scope['server']) or info['season'] != str(scope['season']):
        return False, None
    if not host.get('online') or host.get('runtime') != info['runtime']:
        return False, None
    if info['owner'] == client and info['runtime'] == runtime:
        return True, None
    from web_delegated_hosting import build_delegation
    # The existing standalone host can differ from the voice owner. Give the
    # pure builder its actual host identity; never change voice fields here.
    shadow = dict(data, owner=info['owner'], owner_runtime=info['runtime'],
                  phase='active', scope=dict(scope))
    shadow.pop('web_delegate', None)
    try:
        return True, build_delegation(shadow, client, runtime, scope, generation, request)
    except ValueError:
        return False, None


class StandaloneHosting:
    def __init__(self, bridge):
        self.bridge, self.manager, self.app = bridge, bridge.manager, bridge.app
        self.lock = threading.RLock()
        self.record = {}
        self.busy = False
        self.cancelled = threading.Event()
        self.shared_signature = self.shared_attempt_signature = None
        self.shared_failures = 0
        self.next_shared_check = 0
        self.resume_stopped_key = None
        self.stopped_resume_serial = getattr(self.manager, 'public_resume_serial', 0)
        self.stage, self.message = 'idle', '단독 실행은 이 PC의 현재 스케줄을 사용합니다.'

    def status(self):
        with self.lock:
            info = standalone_info(self.record)
            coordinator = getattr(self.app, 'discord_handover', None)
            return dict(busy=self.busy, stage=self.stage, message=self.message,
                        owner=info['owner'] if info else '',
                        owner_name=((self.record.get('members', {}).get(info['owner'], {}).get('name')
                                    or info['owner'][:8]) if info else '—'),
                        local=bool(info and coordinator and info['owner'] == coordinator.client and info['runtime'] == coordinator.runtime),
                        state=info['state'] if info else '')

    def observe(self, data, coordinator):
        with self.lock:
            previous = standalone_info(self.record)
            self.record = deepcopy(data)
            current = standalone_info(data)
            if (previous or {}).get('id') != (current or {}).get('id'):
                self.shared_signature = self.shared_attempt_signature = None
                self.shared_failures, self.next_shared_check = 0, 0
                self.stopped_resume_serial = getattr(self.manager, 'public_resume_serial', 0)
        info = standalone_info(data)
        if info:
            self.bridge.retained_host = None
        elif previous:
            self._progress('idle', '새 디코 접속 요청 · 일반 웹 자동 승계로 복귀합니다.')
        if info and (info['state'] != 'active' or info['owner'] != coordinator.client
                     or info['runtime'] != coordinator.runtime):
            if getattr(self.manager, 'authority_key', None):
                self.manager.set_authority((), message='웹 단독 담당 변경 또는 웹 종료')
        self.bridge.wake.set()

    def suppresses_normal(self):
        with self.lock:
            return self.busy or standalone_info(self.record) is not None

    def resume_stopped(self, key, coordinator):
        """Bridge worker only. Explicit ON restores an already active voice owner."""
        with self.lock:
            info = standalone_info(self.record)
        configured = (self.manager.status().get('enabled') and self.manager.public.status().get('enabled')
                      and self.manager.ddns_status().get('configured') and not self.manager.public_paused)
        if not configured:
            self.resume_stopped_key = None
            return False
        if self.busy or not info or info['state'] != 'stopped':
            return False
        # A remote logout must stay stopped even if this standby PC's boxes
        # were already ON. Resume only after a subsequent explicit ON action.
        if getattr(self.manager, 'public_resume_serial', 0) == self.stopped_resume_serial:
            return False
        attempt = (info['id'], key)
        if attempt == self.resume_stopped_key:
            return False
        self.resume_stopped_key = attempt
        try:
            coordinator._assert_profile()
            if not ConnectionPolicy.has_authority(coordinator.policy.snapshot(), key[2]):
                return False
            def resume(data):
                current = standalone_info(data)
                if (current != info or data.get('phase') != 'active'
                        or data.get('owner') != key[1] or data.get('owner_runtime') != key[2]
                        or data.get('generation') != key[3]):
                    raise ValueError('웹 중지 기록 또는 송출 담당자가 바뀌어 자동 재개를 취소했습니다.')
                data.pop('web_standalone', None)
                data.pop('web_standalone_previous', None)
            with coordinator.lock:
                data = coordinator.record.mutate(resume)
            coordinator._process_record(data, coordinator._status())
            self._notify(coordinator, data)
            self.manager._log('web_stopped_mode_resumed', generation=key[3])
            return True
        except Exception as exc:
            self.manager._log('web_stopped_mode_resume_failed', error_type=type(exc).__name__)
            return False

    def _coordinator(self):
        coordinator = getattr(self.app, 'discord_handover', None)
        if coordinator is None or not coordinator.alive:
            raise ValueError('봇을 연결한 뒤 웹을 실행해주세요. 디코 송출권한은 필요하지 않습니다.')
        coordinator._assert_profile()
        policy = coordinator.policy.snapshot()
        if (policy.get('runtime_id') != coordinator.runtime
                or policy.get('query_until', 0) <= time.monotonic()):
            raise ValueError('봇 연결 확인이 필요합니다. 연결 후 다시 시도해주세요.')
        return coordinator

    def _progress(self, stage, message):
        with self.lock:
            self.stage, self.message = stage, message
        self.manager._log('web_standalone_stage', stage=stage)

    def start(self):
        self._coordinator()
        if not self.manager.ddns_status()['configured']:
            raise ValueError('무료 주소에서 DuckDNS 주소와 Token을 먼저 저장해주세요.')
        with self.lock:
            if self.busy:
                raise ValueError('웹 단독 실행 또는 종료를 처리 중입니다.')
            self.busy = True
            self.cancelled.clear()
        threading.Thread(target=self._start_worker, name='web-standalone-start', daemon=True).start()

    def _start_worker(self):
        operation = uuid.uuid4().hex
        coordinator = None
        local_preparing = False
        try:
            coordinator = self._coordinator()
            self._progress('preparing', '이 PC HTTPS 준비 확인 · 기존 웹은 유지합니다.')
            def reserve(data):
                if data.get('phase') in {'requested', 'joining'}:
                    raise ValueError('디코 승계가 끝난 뒤 웹 단독 실행을 눌러주세요.')
                pending = data.get('web_operation') or {}
                if pending.get('expires', 0) > time.time():
                    raise ValueError('다른 PC에서 웹 실행을 처리 중입니다.')
                member = data.get('members', {}).get(coordinator.client, {})
                if not member.get('online') or member.get('runtime') != coordinator.runtime:
                    raise ValueError('이 PC의 관리자 연결이 변경되었습니다.')
                data['web_operation'] = dict(id=operation, owner=coordinator.client,
                    runtime=coordinator.runtime, generation=data.get('generation'), expires=time.time()+150)
            with coordinator.lock:
                data = coordinator.record.mutate(reserve)
            self._notify(coordinator, data)
            def guard():
                if self.cancelled.is_set():
                    raise ValueError('웹 종료 요청으로 이전 단독 실행을 취소했습니다.')
                if self._coordinator() is not coordinator:
                    raise ValueError('관리자 실행 연결이 바뀌어 이전 웹 실행 요청을 취소했습니다.')
                if self.manager.closed.is_set():
                    raise ValueError('웹서비스가 종료되었습니다.')
            stamp = (coordinator.scope['guild'], coordinator.client, coordinator.runtime, int(data.get('generation', 0)))
            envelope, _, error = self.app._github_get_json_file(self.bridge._shared_path(stamp), timeout=5)
            if error:
                raise ValueError('공동 인증서·비밀번호를 확인하지 못했습니다. 기존 웹은 유지합니다.')
            if envelope is not None and not (web_host(data) == coordinator.client and self.manager.publication_allowed()):
                from web_shared_state import import_state
                import_state(self.manager, envelope, stamp, guard)
            local_preparing = True
            self.manager.public_paused = False
            worker = self.manager.status()
            if not worker.get('enabled') or worker['state'] not in {'running', 'starting'}:
                self.manager.set_enabled(True)
            deadline = time.monotonic()+35
            while True:
                guard()
                self.manager.prepare_local_https(stamp, 10)
                state = self.manager.https_status()
                if state.get('https_state') == 'running':
                    from web_direct_hosting import check_local_https
                    check_local_https(state['domain'], state['port'])
                    break
                if state.get('https_state') == 'error' or self.manager.status()['state'] == 'error':
                    raise ValueError('이 PC HTTPS 준비에 실패했습니다. 기존 웹은 유지합니다.')
                if time.monotonic() >= deadline or self.manager.closed.wait(.25):
                    raise ValueError('이 PC HTTPS 준비 시간이 초과되었습니다. 기존 웹은 유지합니다.')
            with coordinator.lock:
                latest, _ = coordinator.record.read()
            self._check_operation(latest, operation, coordinator)
            host = web_host(latest)
            if host and host != coordinator.client:
                self._progress('stopping_peer', '상대 PC에 웹 로그아웃 요청 · 종료 확인 대기')
                result, finished = [], threading.Event()
                coordinator.web_control.request(dict(operation='administrator_web_stop',
                    request_id=str(uuid.uuid4().int)[:23], target_client_id=host,
                    expected_target_runtime=latest.get('members', {}).get(host, {}).get('runtime', ''),
                    handover_operation=operation),
                    lambda ok, message: (result.append((ok, message)), finished.set()))
                if not finished.wait(80) or not result or not result[0][0]:
                    raise ValueError(result[0][1] if result else '상대 웹 종료 응답이 없습니다. 단독 공개를 시작하지 않았습니다.')
                # The old host saves its latest PIN/certificate before stopping.
                envelope, _, error = self.app._github_get_json_file(self.bridge._shared_path(stamp), timeout=5)
                if error:
                    raise ValueError('상대 웹 종료 후 최신 공동 설정을 확인하지 못했습니다. 웹 단독 실행을 다시 눌러주세요.')
                if envelope is not None:
                    from web_shared_state import import_state
                    import_state(self.manager, envelope, stamp, guard)
            guard()
            def activate(current):
                if self.cancelled.is_set():
                    raise ValueError('웹 종료 요청으로 이전 단독 실행을 취소했습니다.')
                self._check_operation(current, operation, coordinator)
                if web_host(current) not in {'', coordinator.client, host}:
                    raise ValueError('웹 담당자가 바뀌어 단독 실행을 취소했습니다.')
                current['web_standalone'] = dict(protocol=1, id=operation, state='active',
                    owner=coordinator.client, runtime=coordinator.runtime,
                    guild=coordinator.scope['guild'], server=coordinator.scope['server'],
                    season=coordinator.scope['season'])
                current.pop('web_delegate', None)
                current.pop('web_return_pending', None)
                current.pop('web_operation', None)
            with coordinator.lock:
                data = coordinator.record.mutate(activate)
            coordinator._process_record(data, coordinator._status())
            self._notify(coordinator, data)
            self.bridge.retained_host = None
            with self.manager.lock:
                self.manager.delegated_snapshot_only = False
            def snapshot():
                guard()
                from web_schedule_snapshot import collect_snapshot
                self.manager.offer_snapshot(collect_snapshot(self.app))
            coordinator._ui(snapshot)
            self.manager.public_preference_touched = True
            self.manager.public.configure(True)
            self._progress('starting', '상대 웹 종료 확인 완료 · 이 PC 스케줄로 단독 공개 준비')
        except Exception as exc:
            if local_preparing:
                self.manager.set_authority((), message='웹 단독 실행 실패 · 진단 로그 확인')
                self.manager.public_paused = True
            self._progress('error', str(exc) if isinstance(exc, ValueError) else '웹 단독 실행 실패 · 진단 로그를 확인해주세요.')
            self.manager._log('web_standalone_start_failed', error_type=type(exc).__name__)
        finally:
            if coordinator is not None:
                try:
                    def release(data):
                        if (data.get('web_operation') or {}).get('id') == operation:
                            data.pop('web_operation', None)
                    with coordinator.lock:
                        data = coordinator.record.mutate(release)
                    self._notify(coordinator, data)
                except Exception as exc:
                    self.manager._log('web_standalone_operation_release_failed', error_type=type(exc).__name__)
            with self.lock:
                self.busy = False
            self.bridge.wake.set()

    @staticmethod
    def _check_operation(data, operation, coordinator):
        pending = data.get('web_operation') or {}
        if (pending.get('id') != operation or pending.get('owner') != coordinator.client
                or pending.get('runtime') != coordinator.runtime or pending.get('expires', 0) <= time.time()
                or pending.get('generation') != data.get('generation')
                or data.get('members', {}).get(coordinator.client, {}).get('runtime') != coordinator.runtime
                or data.get('phase') in {'requested', 'joining'}):
            raise ValueError('웹 실행 요청 또는 관리자 연결이 변경되었습니다.')

    def tick(self):
        with self.lock:
            info = standalone_info(self.record)
        if self.busy or not info or info['state'] != 'active':
            return
        coordinator = getattr(self.app, 'discord_handover', None)
        if (coordinator is None or info['owner'] != coordinator.client
                or info['runtime'] != coordinator.runtime or self.manager.public_paused):
            return
        try:
            self._coordinator()
            if info['server'] != coordinator.scope['server'] or info['season'] != str(self.app.current_season_no):
                return
            self.manager.set_authority((info['guild'], info['owner'], info['runtime'], 'solo:'+info['id']),
                ready=self.manager.snapshot_version > 0, message='웹 단독 운영 · 이 PC 스케줄 사용')
            public = self.manager.public.status()
            if self.manager.publication_allowed() and public['state'] == 'prepared':
                self._progress_once('running', '웹 단독 공개 중 · 디코 송출권한과 별도로 운영합니다.')
            elif public['state'] == 'error' or self.manager.status()['state'] == 'error':
                self._progress_once('error', '단독 웹 공개 실패 · 웹 상태와 진단 로그 확인 후 다시 실행해주세요.')
        except Exception as exc:
            self.manager._log('web_standalone_lease_failed', error_type=type(exc).__name__)

    def publish_shared(self):
        """Bridge worker only: upload encrypted settings on actual file changes."""
        with self.lock:
            info = standalone_info(self.record)
            generation = int(self.record.get('generation', 0))
        coordinator = getattr(self.app, 'discord_handover', None)
        if (self.busy or not info or info['state'] != 'active' or coordinator is None
                or info['owner'] != coordinator.client or info['runtime'] != coordinator.runtime
                or not self.manager.publication_allowed()
                or self.manager.public.status()['state'] != 'prepared'
                or time.monotonic() < self.next_shared_check):
            return
        self.next_shared_check = time.monotonic()+10
        try:
            self._coordinator()
            self.manager.pin_public_port(self.manager.public.status().get('external_port'))
            signature = tuple((path.stat().st_mtime_ns, path.stat().st_size) for path in (
                self.manager.root/'access_settings.json', self.manager.root/'https'/'current.json',
                self.manager.settings_path))
            if signature == self.shared_signature:
                return
            if signature != self.shared_attempt_signature:
                self.shared_attempt_signature, self.shared_failures = signature, 0
            if self.shared_failures >= 3:
                return
            stamp = (info['guild'], info['owner'], info['runtime'], generation)
            from web_shared_state import export_state
            payload = export_state(self.manager, stamp)
            path = self.bridge._shared_path(stamp)
            _, sha, error = self.app._github_get_json_file(path, timeout=5)
            if error:
                raise ValueError('단독 웹 공동 설정 저장 준비 실패')
            with coordinator.lock:
                latest, _ = coordinator.record.read()
            if standalone_info(latest) != info or not self.manager.publication_allowed():
                raise ValueError('웹 담당자가 바뀌어 공동 설정 저장을 중단했습니다.')
            ok, error = self.app._github_put_json_file(path, payload, sha=sha,
                message='BossTimer encrypted standalone web settings', timeout=5)
            if not ok:
                raise ValueError('단독 웹 공동 설정 저장 실패')
            self.shared_signature = signature
            self.manager._log('web_standalone_shared_state_published', request=info['id'])
        except Exception as exc:
            self.shared_failures += 1
            self.manager._log('web_standalone_shared_state_failed', error_type=type(exc).__name__)

    def _progress_once(self, stage, message):
        if self.stage != stage:
            self._progress(stage, message)

    def stop(self):
        with self.lock:
            if self.busy:
                raise ValueError('웹 실행을 처리 중입니다. 완료 후 종료해주세요.')
            self.busy = True
        def worker():
            try:
                self.stop_sync()
            except Exception as exc:
                self._progress('error', str(exc) if isinstance(exc, ValueError) else '웹 종료 확인 실패 · 진단 로그를 확인해주세요.')
            finally:
                self.busy = False
        threading.Thread(target=worker, name='web-standalone-stop', daemon=True).start()

    def stop_sync(self, *, for_handover=False):
        self.cancelled.set()
        self._progress('stopping', '이 PC 웹 종료 · 외부 접근과 주소 갱신 중지 확인')
        self._save_settings_before_stop()
        self.manager.emergency_stop(standby=for_handover)
        self.manager.set_authority((), message='웹 대기 · 다른 PC에서 공개합니다.' if for_handover else
                                   '웹 종료 · 단독 강제 실행으로 다시 시작할 수 있습니다.')
        self.manager._flush_authority(force=True)
        if read_json(self.manager.root/'authority_lease.json').get('enabled') is not False:
            raise ValueError('웹 외부 접근 차단을 확인하지 못했습니다.')
        deadline = time.monotonic()+35
        while ((not for_handover and self.manager.status()['state'] != 'stopped') or self.manager.ddns.status().get('busy')
               or self.manager.public.status().get('busy') or self.manager.public.status()['state'] != 'off'):
            if self.manager.closed.is_set() or time.monotonic() >= deadline:
                raise ValueError('웹 프로세스·외부 연결·주소 갱신 종료를 확인하지 못했습니다.')
            time.sleep(.2)
        coordinator = getattr(self.app, 'discord_handover', None)
        if coordinator is not None and coordinator.alive:
            def mark(data):
                if data.get('members', {}).get(coordinator.client, {}).get('runtime') != coordinator.runtime:
                    raise ValueError('관리자 실행 세션이 바뀌어 이전 웹 종료 결과를 기록하지 않았습니다.')
                if web_host(data) == coordinator.client:
                    data['web_standalone'] = dict(protocol=1, id=uuid.uuid4().hex, state='stopped',
                        owner=coordinator.client, runtime=coordinator.runtime,
                        guild=coordinator.scope['guild'], server=coordinator.scope['server'],
                        season=coordinator.scope['season'])
                    data.pop('web_delegate', None)
                    data.pop('web_return_pending', None)
            with coordinator.lock:
                data = coordinator.record.mutate(mark)
            coordinator._process_record(data, coordinator._status())
            self._notify(coordinator, data)
        self._progress('stopped', '웹 공개 인계 완료 · 웹서비스 설정을 유지하고 대기합니다.' if for_handover else
                       '이 PC 웹 종료 완료 · 봇 연결과 디코 송출은 유지합니다.')

    def _save_settings_before_stop(self):
        coordinator = getattr(self.app, 'discord_handover', None)
        if (coordinator is None or not coordinator.alive or not self.manager.publication_allowed()
                or self.manager.status()['state'] != 'running'):
            return
        try:
            with coordinator.lock:
                data, _ = coordinator.record.read()
            if (web_host(data) != coordinator.client or
                    data.get('members', {}).get(coordinator.client, {}).get('runtime') != coordinator.runtime):
                return
            stamp = (coordinator.scope['guild'], coordinator.client, coordinator.runtime, int(data.get('generation', 0)))
            from web_shared_state import export_state
            payload = export_state(self.manager, stamp)
            path = self.bridge._shared_path(stamp)
            _, sha, error = self.app._github_get_json_file(path, timeout=5)
            if error:
                raise ValueError('최신 공동 설정 저장 준비 실패')
            ok, error = self.app._github_put_json_file(path, payload, sha=sha,
                message='BossTimer encrypted web settings before logout', timeout=5)
            if not ok:
                raise ValueError('최신 공동 설정 저장 실패')
            self.manager._log('web_stop_shared_state_saved')
        except Exception as exc:
            # Web logout must still work if a settings upload fails.
            self.manager._log('web_stop_shared_state_save_failed', error_type=type(exc).__name__)

    def _notify(self, coordinator, data):
        # Existing bounded event-delivery retries; no remote-record polling.
        with self.bridge.delegation.lock:
            self.bridge.delegation.notice = data
            self.bridge.delegation.failures, self.bridge.delegation.next_attempt = 0, 0
        self.bridge.delegation.wake.set()

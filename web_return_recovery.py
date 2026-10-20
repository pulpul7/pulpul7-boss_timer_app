"""Prepare local TLS before a web switch and restore the previous host on failure."""
from copy import deepcopy
import math
import threading
import time
import uuid

from discord_connection_policy import ConnectionPolicy
from web_delegated_hosting import delegation_info, valid_token
from web_service_common import read_json
from web_publication_confirmation import address_confirmed

TRIAL_SECONDS = 120

def pending_return(info):
    backup = dict(info, state='active', keep_host=True)
    started = time.time()
    return dict(schema=1, id=uuid.uuid4().hex, backup=backup,
                started_at=started, deadline=started+TRIAL_SECONDS)


def recovery_info(data, key):
    if not isinstance(data, dict):
        return None
    pending = data.get('web_return_pending', {})
    if (not isinstance(pending, dict) or pending.get('schema') != 1
            or not valid_token(pending.get('id'))
            or type(pending.get('deadline')) not in (int, float)
            or not math.isfinite(pending['deadline'])
            or type(pending.get('started_at')) not in (int, float)
            or not math.isfinite(pending['started_at'])
            or pending['deadline'] < pending['started_at']):
        return None
    info = delegation_info(dict(data, web_delegate=pending.get('backup')))
    if (not info or data.get('phase') != 'active'
            or (info['guild'], info['source'], info['source_runtime'], info['generation']) != key):
        return None
    return deepcopy(pending)


class WebReturnRecovery:
    def __init__(self, bridge):
        self.bridge, self.manager, self.app = bridge, bridge.manager, bridge.app
        self.lock = threading.RLock()
        self.record = {}
        self.stage, self.message = 'idle', ''
        self.failed = None
        self.verified_trial = None
        self.attempts, self.next_attempt = 0, 0

    def observe(self, data):
        with self.lock:
            previous = self.record.get('web_return_pending')
            self.record = deepcopy(data)
            current = data.get('web_return_pending')
            if (isinstance(current, dict) and valid_token(current.get('id'))
                    and (not isinstance(previous, dict) or current['id'] != previous.get('id'))):
                self.failed = None
                self.attempts, self.next_attempt = 0, 0
                self.stage, self.message = 'trial', '새 웹 연결 확인 · 실패 시 기존 웹 담당 복구'

    def _guard(self, key, coordinator, *, allow_joining=False):
        coordinator._assert_profile()
        manual = getattr(self.bridge, 'standalone', None)
        if manual is not None and manual.suppresses_normal():
            raise ValueError('웹 단독 실행 또는 종료로 기존 웹 승계 작업을 취소했습니다.')
        state = coordinator.policy.snapshot()
        if (self.bridge.closed.is_set() or self.manager.closed.is_set() or not coordinator.alive
                or not ConnectionPolicy.has_authority(state, key[2], allow_joining=allow_joining)
                or state.get('authority_generation') != key[3]
                or str(self.app.current_season_no) != coordinator.scope['season']):
            raise ValueError('담당자 또는 실행 세션이 바뀌어 이전 웹 복구 작업을 취소했습니다.')

    def preflight(self, info, coordinator):
        """Uses the last encrypted shared state; the existing host stays active."""
        phase = 'shared-settings'
        try:
            with self.bridge.delegation.lock:
                members = self.bridge.delegation.record.get('members', {})
            if members.get(info['host'], {}).get('web_recovery_protocol') != 1:
                raise ValueError('기존 웹 담당자의 GUI를 최신 버전으로 교체한 뒤 웹 승계를 요청해주세요.')
            self.bridge.refresh_context()
            with self.bridge.lock:
                context = self.bridge.context
            if not context or context[1] is not coordinator:
                raise ValueError('현재 송출권한의 웹 준비 연결을 확인하지 못했습니다.')
            key, _, season = context
            self._guard(key, coordinator, allow_joining=True)
            self.stage, self.message = 'preflight', '기존 웹 유지 · 새 PC 인증서·HTTPS 확인'
            self.bridge.operation_local.delegated_info = info
            self.bridge.operation_local.serial = self.bridge.retry_serial
            data = self.bridge._read_owner(key, coordinator)
            phase = 'prepare'
            self.bridge.operation_local.prepare_phase = phase
            self.bridge._prepare(key, coordinator, data, season)
            phase = 'local-https'
            if not self.manager.status()['enabled']:
                self.manager.set_enabled(True)
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                self._guard(key, coordinator, allow_joining=True)
                self.bridge.delegation._guard(info, 'source', allow_joining=True)
                state = coordinator.policy.snapshot()
                self.manager.prepare_local_https(key, state['authority_until']-time.monotonic())
                https = self.manager.https_status()
                if https.get('https_state') == 'running':
                    from web_direct_hosting import check_local_https
                    check_local_https(https['domain'], https['port'])
                    self.manager._log('web_return_preflight_ready', delegate=info['id'])
                    return True
                if self.manager.status()['state'] == 'error' or https.get('https_state') == 'error':
                    raise ValueError('새 PC의 로컬 HTTPS 준비에 실패했습니다.')
                if self.manager.closed.wait(.5):
                    raise ValueError('웹서비스가 종료되었습니다.')
            raise ValueError('새 PC의 HTTPS 준비가 아직 완료되지 않았습니다.')
        except Exception as exc:
            if phase == 'prepare':
                phase = getattr(self.bridge.operation_local, 'prepare_phase', phase)
            self.stage = 'preflight_failed'
            reason = str(exc)[:300] if isinstance(exc, ValueError) else '진단 로그를 확인해주세요.'
            self.message = '새 웹 준비 실패 · 기존 PC의 웹 유지 · ' + reason
            self.bridge.delegation.message = self.message
            # Never clear a newer authority key if voice ownership changed while preparing.
            try:
                self.bridge.delegation._guard(info, 'source', allow_joining=True)
                self.manager.set_authority((), message=self.message)
            except Exception:
                pass
            self.manager._log('web_return_preflight_failed', phase_name=phase, error_type=type(exc).__name__, reason=reason)
            try:
                self.bridge.delegation._send(info, 'preflight_failed', reason=reason, phase_name=phase)
            except Exception as notify_error:
                self.manager._log('web_return_failure_notice_failed', error_type=type(notify_error).__name__)
            return False
        finally:
            self.bridge.operation_local.delegated_info = None

    def monitor(self, key, coordinator, data=None):
        if data is not None:
            self.observe(data)
        with self.lock:
            pending = recovery_info(self.record, key)
        if not pending:
            return
        public, state = self.manager.public.status(), self.manager.status()
        ddns = self.manager.publication_ddns_status()
        # Public listener readiness alone does not prove that the shared address
        # points to this PC. Wait for this trial's provider acknowledgement.
        address_ready = address_confirmed(ddns, pending['started_at'])
        trial_ready = (state['state'] == 'running' and public['state'] == 'prepared'
                       and self.manager.publication_allowed() and address_ready)
        if trial_ready:
            self.verified_trial = pending['id']
        if not self.failed and trial_ready:
            self._complete(key, coordinator, pending)
            return
        # Ignore an error left by an earlier attempt until this trial is activated.
        activated = (self.manager.authority_key == key and self.manager.authority_ready)
        if (not self.failed and ((activated and (state['state'] == 'error' or public['state'] == 'error'
                or (ddns.get('enabled', True) and ddns.get('state') == 'error')))
                or time.time() >= pending['deadline'])):
            self.failed = (key, coordinator, pending)
        if self.failed and self.attempts < 3 and time.monotonic() >= self.next_attempt:
            self._rollback(*self.failed)

    def fail(self, key, coordinator, exc):
        from web_handover_bridge import WebOperationCancelled
        if isinstance(exc, WebOperationCancelled):
            return  # A replacement can share the same voice key; it is not a failed trial.
        with self.lock:
            pending = recovery_info(self.record, key)
        if pending:
            self.manager._log('web_return_trial_failed', error_type=type(exc).__name__)
            self.failed = (key, coordinator, pending)
            self.monitor(key, coordinator)

    def _complete(self, key, coordinator, pending):
        self._guard(key, coordinator)
        def change(data):
            current = recovery_info(data, key)
            if not current or current['id'] != pending['id']:
                raise ValueError('새 웹 승계가 있어 이전 성공 확인을 취소했습니다.')
            data.pop('web_return_pending', None)
        with coordinator.lock:
            data = coordinator.record.mutate(change)
        coordinator._process_record(data, coordinator._status())
        self.stage, self.message = 'completed', '새 웹 공개 확인 완료'
        self._notify(coordinator, data)
        self.manager._log('web_return_trial_completed', request=pending['id'])

    def _rollback(self, key, coordinator, pending):
        self.attempts += 1
        self.next_attempt = time.monotonic()+15
        try:
            self._guard(key, coordinator)
            # Reverify before stopping anything; an older failure cannot stop a newer web.
            with coordinator.lock:
                current, _ = coordinator.record.read()
            verified = recovery_info(current, key)
            if not verified or verified['id'] != pending['id']:
                self.failed = None
                return
            if (self.manager.authority_key == key and self.manager.authority_ready
                    and self.manager.status()['state'] == 'running' and self.manager.publication_allowed()
                    and self.manager.public.status()['state'] == 'prepared'
                    and (self.verified_trial == verified['id']
                         or address_confirmed(self.manager.publication_ddns_status(), verified['started_at']))):
                self.failed = None
                self._complete(key, coordinator, verified)
                return  # The trial recovered while a previous failure was queued.
            self.stage, self.message = 'restoring', '새 웹 실패 · 기존 웹 담당 자동 복구 요청'
            self.manager.emergency_stop(standby=True)
            self.manager.set_authority((), message=self.message)
            self.manager._flush_authority(force=True)
            if read_json(self.manager.root/'authority_lease.json').get('enabled') is not False:
                raise ValueError('실패한 새 웹의 공개 차단을 확인하지 못했습니다.')
            deadline = time.monotonic()+20
            while (self.manager.ddns.status().get('busy')
                   or getattr(self.manager, 'background_busy', lambda: False)()
                   or self.manager.public.status().get('busy')
                   or self.manager.public.status()['state'] != 'off'):
                self._guard(key, coordinator)
                if self.manager.closed.wait(.2) or time.monotonic() >= deadline:
                    raise ValueError('새 PC의 주소 갱신 종료를 기다립니다.')
            self._guard(key, coordinator)
            def change(data):
                verified = recovery_info(data, key)
                if not verified or verified['id'] != pending['id']:
                    raise ValueError('담당자가 변경되어 이전 웹 복구를 취소했습니다.')
                info = dict(verified['backup'], id=uuid.uuid4().hex, state='active',
                            keep_host=True, restore_after_failure=True)
                host = data.get('members', {}).get(info['host'], {})
                if not host.get('online') or host.get('runtime') != info['host_runtime']:
                    raise ValueError('기존 웹 담당 PC의 실행 연결을 확인하지 못했습니다.')
                data['web_delegate'] = info
                data.pop('web_return_pending', None)
            with coordinator.lock:
                data = coordinator.record.mutate(change)
            coordinator._process_record(data, coordinator._status())
            self.failed = None
            self._notify(coordinator, data)
            self.manager._log('web_previous_host_restore_requested', request=pending['id'])
        except Exception as exc:
            self.manager._log('web_previous_host_restore_delayed', attempt=self.attempts, error_type=type(exc).__name__)

    def _notify(self, coordinator, data):
        # Retry event delivery after CAS success; a missed first send must not strand the old host.
        relay = self.bridge.delegation
        with relay.lock:
            relay.notice = data
            relay.failures, relay.next_attempt = 0, 0
        relay.wake.set()

"""One-shot Discord web retries over the existing verified authority event path."""
from collections import deque
from copy import deepcopy
import threading
import time

from discord_connection_policy import ConnectionPolicy
from web_delegated_hosting import delegation_info
from web_standalone_hosting import standalone_info


class DiscordWebControl:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.condition = threading.Condition()
        self.latest = {}
        self.started = deque(maxlen=256)

    def observe(self, data, status):
        coordinator = self.coordinator
        with self.condition:
            self.latest = deepcopy(data)
            self.condition.notify_all()
            call = data.get('members', {}).get(coordinator.client, {}).get('web_call', {})
            if (not isinstance(call, dict) or not isinstance(call.get('id'), str)
                    or not call['id'].isdigit() or len(call['id']) > 24
                    or not isinstance(call.get('expires'), (int, float))):
                return
            if (not status.get('online') or call.get('state') != 'pending'
                    or call.get('runtime') != status.get('runtime_id')
                    or time.time() >= call.get('expires', 0) or call.get('id') in self.started):
                return
            self.started.append(call['id'])
        threading.Thread(target=self._perform, args=(deepcopy(call),),
                         name='discord-web-once', daemon=True).start()

    def request(self, payload, done):
        def worker():
            coordinator = self.coordinator
            from discord_authority import AuthorityError
            finish = time.monotonic()+80
            try:
                coordinator._assert_profile()
                selector = str(payload.get('target_administrator') or payload.get('target_client_id') or '').strip()
                if not selector:
                    raise ValueError('관리자 이름 또는 ID를 입력해주세요. /보탐 관리자에서 확인할 수 있습니다.')
                call_id = str(payload['request_id'])
                action = {'administrator_web_stop': 'stop', 'administrator_web_force': 'force'}.get(
                    payload.get('operation'), 'retry')
                if not call_id.isdigit() or len(call_id) > 24:
                    raise ValueError('웹 요청 번호가 올바르지 않습니다.')
                with coordinator.lock:
                    data, _ = coordinator.record.read()
                target = coordinator.record.resolve_member(coordinator._live_data(data), selector)
                def publish(record):
                    if record.get('phase') in {'requested', 'joining'}:
                        raise ValueError('관리자 승계 중입니다. 완료 후 웹서버 명령을 다시 실행하세요.')
                    member = coordinator.record.online_members(record).get(target)
                    if not member:
                        raise ValueError('대상 관리자 연결이 끊겼습니다. 기존 웹은 유지합니다.')
                    if action in {'stop', 'force'} and member.get('web_standalone_protocol') != 1:
                        raise ValueError('대상 PC의 GUI와 봇을 새 버전으로 교체해주세요. 웹 종료 명령 지원을 확인하지 못했습니다.')
                    old = member.get('web_call', {})
                    if old.get('state') in {'pending', 'accepted'} and time.time() < old.get('expires', 0):
                        raise ValueError('해당 PC에서 웹 요청을 처리 중입니다.')
                    member['web_call'] = dict(id=call_id, runtime=member['runtime'], action=action,
                                             state='pending', expires=time.time()+10)
                with coordinator.lock:
                    published = coordinator.record.mutate(publish)
                self.observe(published, coordinator._status())
                coordinator._notify(published)
                self._wait(target, call_id, done, finish)
            except Exception as exc:
                done(False, str(exc) if isinstance(exc, (ValueError, AuthorityError)) else '웹 요청 전달 실패 · 관리자 연결과 진단 로그를 확인하세요.')
        threading.Thread(target=worker, name='discord-web-request', daemon=True).start()

    def _wait(self, target, call_id, done, finish):
        coordinator = self.coordinator
        checked = set()
        while coordinator.alive:
            with self.condition:
                member = self.latest.get('members', {}).get(target, {})
                call = member.get('web_call', {})
                if call.get('id') != call_id or member.get('runtime') != call.get('runtime'):
                    raise ValueError('대상 관리자 실행 상태가 변경되어 웹 요청을 취소했습니다.')
                if call.get('state') == 'done':
                    if call.get('action') == 'stop' and call.get('ok') and call.get('stopped') is not True:
                        raise ValueError('상대 웹 종료 확인이 없어 단독 공개를 시작하지 않았습니다.')
                    done(bool(call.get('ok')), str(call.get('message') or '웹 요청 완료'))
                    return
                pending = call.get('state') == 'pending'
                remaining = min(finish-time.monotonic(), call.get('expires', 0)-time.time()) if pending else finish-time.monotonic()
                if remaining > 0:
                    self.condition.wait(remaining)
                    continue
            # Only one read at each deadline if a pushed event was lost. No GitHub polling.
            phase = 'pending' if pending else 'final'
            if phase in checked:
                raise ValueError('대상 PC의 응답을 받지 못했습니다. 자동 재시도하지 않습니다. 웹 설정과 상태를 확인하세요.')
            checked.add(phase)
            with coordinator.lock:
                latest, _ = coordinator.record.read()
            self.observe(latest, coordinator._status())
        raise ValueError('프로그램 연결이 종료되어 웹 요청을 취소했습니다.')

    def _perform(self, call):
        coordinator = self.coordinator
        try:
            def claim(data):
                member = data.get('members', {}).get(coordinator.client, {})
                current = member.get('web_call', {})
                if (current.get('id') != call['id'] or current.get('runtime') != call['runtime']
                        or member.get('runtime') != call['runtime']
                        or current.get('state') != 'pending' or time.time() >= current.get('expires', 0)):
                    raise ValueError('웹 요청이 만료되거나 변경되었습니다.')
                current.update(state='accepted', expires=time.time()+70)
            with coordinator.lock:
                coordinator._assert_profile()
                data = coordinator.record.mutate(claim)
            self.observe(data, coordinator._status())
            coordinator._notify(data)
            if data.get('phase') not in {'active', 'idle'}:
                raise ValueError('관리자 승계 중입니다. 완료 후 웹서버 명령을 다시 실행하세요.')
            manager = getattr(coordinator.app, 'web_service_manager', None)
            if manager is None or coordinator.busy or coordinator.releasing:
                raise ValueError('웹 모듈이 준비되지 않았거나 관리자 승계 중입니다.')
            action = call.get('action', 'retry')
            if action == 'stop':
                manager.stop_web_verified()
                manager._log('discord_web_stop_verified', request=call['id'])
                self._finish(call, True, '웹 서버 종료 완료 · 외부 접근과 주소 갱신을 중지했습니다. 봇과 디코 송출은 유지합니다.', stopped=True)
                return
            if action == 'force':
                manager.force_start_web()
                manager._log('discord_web_standalone_requested', request=call['id'])
                self._finish(call, True, '웹 단독 강제 실행 요청 · 상대 웹 종료 확인 후 이 PC 스케줄로 공개합니다.')
                return
            if action != 'retry':
                raise ValueError('지원하지 않는 웹 요청입니다.')
            role = manager.delegation.status()['role']
            info = delegation_info(data)
            hosts_web = bool(role == 'host' and info and info['state'] == 'active'
                             and info['host'] == coordinator.client and info['host_runtime'] == call['runtime'])
            owns_voice = (data.get('phase') == 'active' and data.get('owner') == coordinator.client
                          and data.get('owner_runtime') == call['runtime']
                          and ConnectionPolicy.has_authority(coordinator.policy.snapshot(), call['runtime']))
            manual = standalone_info(data)
            owns_standalone = bool(manual and manual['owner'] == coordinator.client and manual['runtime'] == call['runtime'])
            if not owns_voice and not hosts_web and not owns_standalone:
                raise ValueError('현재 Discord 송출 담당자 또는 별도로 지정된 웹 담당자를 선택하세요. 음성 권한은 변경하지 않습니다.')
            if not manager.ddns_status()['configured']:
                raise ValueError('대상 PC의 웹 → 무료 주소에서 DuckDNS 주소와 Token을 먼저 저장해주세요.')
            public = manager.public.status()
            if manager.publication_allowed() and public['state'] == 'prepared' and public.get('address'):
                self._finish(call, True, '이미 웹 공개 중입니다.\n'+public['address'])
                return
            manager.retry_public(once=True)
            manager._log('discord_web_retry_once', request=call['id'])
            finish = time.monotonic()+60
            while coordinator.alive and time.monotonic() < finish:
                coordinator._assert_profile()
                policy = coordinator.policy.snapshot()
                if policy.get('runtime_id') != call['runtime'] or policy.get('query_until', 0) <= time.monotonic():
                    raise ValueError('대상 PC의 봇 연결이 변경되어 웹 시도 완료를 확인하지 못했습니다.')
                public = manager.public.status()
                state = manager.status()
                recovery = getattr(manager, 'web_return_recovery', None)
                if recovery is not None and recovery.stage in {'preflight_failed', 'restoring'}:
                    raise ValueError(recovery.message)
                if manager.publication_allowed() and public['state'] == 'prepared' and public.get('address'):
                    self._finish(call, True, '웹 공개 준비 완료 · 대상 PC에서 1회 시도했습니다.\n'+public['address'])
                    return
                if state['state'] == 'error' or public['state'] == 'error':
                    raise ValueError('웹 공개 1회 시도 실패 · 자동 재시도하지 않습니다. 대상 PC의 웹 상태와 진단 로그를 확인하세요.')
                time.sleep(.5)  # Local cached status/control port only.
            raise ValueError('60초 내 웹 공개 완료를 확인하지 못했습니다. 다시 명령을 보내기 전에 대상 PC의 웹 상태를 확인하세요.')
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else '웹 공개 시도 실패 · 대상 PC의 진단 로그를 확인하세요.'
            self._finish(call, False, message)

    def _finish(self, call, ok, message, *, stopped=False):
        coordinator = self.coordinator
        try:
            def update(data):
                member = data.get('members', {}).get(coordinator.client, {})
                current = member.get('web_call', {})
                if (current.get('id') != call['id'] or current.get('runtime') != call['runtime']
                        or member.get('runtime') != call['runtime']
                        or current.get('state') != 'accepted'):
                    raise ValueError('새 웹 요청이 있어 이전 결과를 기록하지 않습니다.')
                current.update(state='done', ok=bool(ok), message=message, stopped=bool(stopped))
            with coordinator.lock:
                coordinator._assert_profile()
                data = coordinator.record.mutate(update)
            self.observe(data, coordinator._status())
            coordinator._notify(data)
        except Exception as exc:
            coordinator.app._append_debug_log('discord_web_result_failed type='+type(exc).__name__)

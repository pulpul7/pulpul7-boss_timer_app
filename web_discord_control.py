"""One-shot Discord web retries over the existing verified authority event path."""
from collections import deque
from copy import deepcopy
import threading
import time

from discord_connection_policy import ConnectionPolicy
from web_delegated_hosting import delegation_info, valid_token
from web_standalone_hosting import StandaloneHosting, standalone_info, web_host_stamp

UPDATE_SECONDS = 240
PROGRAM_UPDATE_SECONDS = 600
RESULT_ACK_SECONDS = 30


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
            program_update = payload.get('operation') == 'administrator_program_update'
            refreshing = payload.get('operation') == 'administrator_web_refresh'
            updating = payload.get('operation') == 'administrator_web_update' or program_update
            host_request = updating or refreshing
            finish = time.monotonic()+(PROGRAM_UPDATE_SECONDS+20 if program_update else UPDATE_SECONDS+20 if updating else 80)
            try:
                coordinator._assert_profile()
                selector = str(payload.get('target_administrator') or payload.get('target_client_id') or '').strip()
                if not selector and not host_request:
                    raise ValueError('관리자 이름 또는 ID를 입력해주세요. /보탐 관리자에서 확인할 수 있습니다.')
                call_id = str(payload['request_id'])
                action = {'administrator_web_stop': 'stop', 'administrator_web_force': 'force',
                          'administrator_web_update': 'update', 'administrator_web_refresh': 'refresh',
                          'administrator_program_update': 'program_update'}.get(
                    payload.get('operation'), 'retry')
                if not call_id.isdigit() or len(call_id) > 24:
                    raise ValueError('웹 요청 번호가 올바르지 않습니다.')
                with coordinator.lock:
                    data, _ = coordinator.record.read()
                handover = payload.get('handover_operation')
                expected_runtime = payload.get('expected_target_runtime')
                expected_host = None
                if host_request:
                    live = coordinator._live_data(data)
                    members = coordinator.record.online_members(live)
                    expected_host = web_host_stamp(data)
                    if program_update and data.get('phase') == 'active' and data.get('owner'):
                        target, expected_runtime = data['owner'], data.get('owner_runtime')
                    elif expected_host:
                        target, expected_runtime = expected_host[:2]
                    elif data.get('phase') == 'active' and data.get('owner'):
                        target, expected_runtime = data['owner'], data.get('owner_runtime')
                    elif len(members) == 1:
                        target, member = next(iter(members.items()))
                        expected_runtime = member.get('runtime')
                    else:
                        raise ValueError('웹 업데이트를 받을 관리자를 정할 수 없습니다. 웹 담당자를 연결하고 다시 요청해주세요.')
                    if target not in members or members[target].get('runtime') != expected_runtime:
                        raise ValueError('웹 담당 관리자 프로그램이 연결되어 있지 않습니다. 해당 프로그램을 켜고 다시 요청해주세요.')
                elif action == 'stop' and valid_token(handover) and valid_token(expected_runtime) and valid_token(payload.get('target_client_id')):
                    target = payload['target_client_id']  # Exact session is fenced again by the CAS and target claim.
                else:
                    target = coordinator.record.resolve_member(coordinator._live_data(data), selector)
                def publish(record):
                    member = coordinator.record.online_members(record).get(target)
                    if not member:
                        raise ValueError('대상 관리자 연결이 끊겼습니다. 기존 웹은 유지합니다.')
                    if host_request:
                        if member.get('runtime') != expected_runtime or (not program_update and web_host_stamp(record) != expected_host):
                            raise ValueError('웹 담당자가 변경되어 업데이트 요청을 취소했습니다. 다시 요청해주세요.')
                        if program_update and record.get('phase') == 'active' and record.get('owner') and (
                                record.get('owner') != target or record.get('owner_runtime') != expected_runtime):
                            raise ValueError('전체 업데이트 담당자가 변경되어 요청을 취소했습니다.')
                        protocol = 'program_update_protocol' if program_update else 'web_refresh_protocol' if refreshing else 'web_update_protocol'
                        if member.get(protocol) != 1:
                            raise ValueError('대상 관리자 프로그램을 새 버전으로 교체해주세요. 요청한 Discord 업데이트를 지원하지 않습니다.')
                    if handover:
                        operation = record.get('web_operation') or {}
                        if (action != 'stop' or operation.get('id') != handover
                                or operation.get('owner') != coordinator.client
                                or operation.get('runtime') != getattr(coordinator, 'runtime', None)
                                or operation.get('expires', 0) <= time.time()
                                or member.get('runtime') != expected_runtime):
                            raise ValueError('웹 인계 요청 또는 대상 실행 세션이 변경되었습니다.')
                    if action in {'stop', 'force'} and member.get('web_standalone_protocol') != 1:
                        raise ValueError('대상 PC의 GUI와 봇을 새 버전으로 교체해주세요. 웹 종료 명령 지원을 확인하지 못했습니다.')
                    old = member.get('web_call', {})
                    if old.get('state') in {'pending', 'accepted'} and time.time() < old.get('expires', 0):
                        raise ValueError('해당 PC에서 웹 요청을 처리 중입니다.')
                    member['web_call'] = dict(id=call_id, runtime=member['runtime'], action=action,
                                             state='pending', expires=time.time()+10)
                    if host_request:
                        member['web_call']['update_host'] = list(expected_host) if expected_host else None
                    if program_update:
                        member['web_call']['requester_runtime'] = payload.get('runtime_id') or coordinator._status().get('runtime_id')
                    if handover:
                        member['web_call']['handover_operation'] = handover
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
                    if call.get('action') == 'program_update' and call.get('ok'):
                        threading.Thread(target=self._ack_program_reply, args=(target, deepcopy(call)),
                                         name='program-update-delivery-ack', daemon=True).start()
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
                if call.get('action') in {'update', 'refresh'}:
                    expected = tuple(call['update_host']) if call.get('update_host') else None
                    if web_host_stamp(data) != expected:
                        raise ValueError('웹 담당자가 변경되어 이전 업데이트 요청을 취소했습니다.')
                current.update(state='accepted', expires=time.time()+(PROGRAM_UPDATE_SECONDS+10 if call.get('action') == 'program_update' else UPDATE_SECONDS+10 if call.get('action') == 'update' else 70))
            with coordinator.lock:
                coordinator._assert_profile()
                data = coordinator.record.mutate(claim)
            self.observe(data, coordinator._status())
            if call.get('action') == 'program_update':
                coordinator._notify(data)
                service = coordinator._ui(lambda: coordinator.app._get_program_update_service())
                result = service.prepare()
                message = result['message']
                if result['state'] == 'prepared':
                    message += ' · 안내 전달 확인 후 본체와 봇을 다시 시작합니다. 기존 웹서버는 유지합니다.'
                if self._finish(call, True, message) and result['state'] == 'prepared':
                    if self._wait_program_reply(call):
                        coordinator._ui(lambda: service.schedule(result), wait=False)
                    else:
                        coordinator.app._append_debug_log('program_update_apply_deferred_reply_not_confirmed')
                return
            manager = getattr(coordinator.app, 'web_service_manager', None)
            if manager is None:
                raise ValueError('웹 모듈이 준비되지 않았습니다.')
            queue_notice = getattr(getattr(manager, 'standalone', None), '_notify', None)
            if callable(queue_notice):
                queue_notice(coordinator, data)  # Accepted progress must not delay the physical stop.
            else:
                coordinator._notify(data)
            action = call.get('action', 'retry')
            if action == 'refresh':
                refresh = getattr(manager, 'request_browser_refresh', None)
                if not callable(refresh):
                    raise ValueError('대상 관리자 프로그램이 화면 갱신 명령을 지원하지 않습니다.')
                self._finish(call, True, refresh())
                return
            if action == 'update':
                updater = getattr(manager, 'page_updater', None)
                if not callable(getattr(updater, 'request_and_wait', None)):
                    raise ValueError('대상 관리자 프로그램이 Discord 웹 업데이트를 지원하지 않습니다.')
                manager._log('discord_web_update_requested', request=call['id'])
                result = updater.request_and_wait(timeout=UPDATE_SECONDS)
                manager._log('discord_web_update_completed', request=call['id'], state=result['state'])
                name = data.get('members', {}).get(coordinator.client, {}).get('name') or '관리자'
                self._finish(call, True, f"{name} · {result['message']}")
                return
            if action == 'stop':
                handover = call.get('handover_operation')
                if handover:
                    operation = data.get('web_operation') or {}
                    if operation.get('id') != handover or operation.get('expires', 0) <= time.time():
                        raise ValueError('웹 인계 요청이 종료되어 이전 중지 명령을 취소했습니다.')
                    manager.stop_web_verified(for_handover=True, record_stop=False)
                else:
                    manager.stop_web_verified()
                manager._log('discord_web_stop_verified', request=call['id'])
                self._finish(call, True, '웹 서버 종료 완료 · 외부 접근과 주소 갱신을 중지했습니다. 봇과 디코 송출은 유지합니다.',
                             stopped=True, for_handover=bool(handover))
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
            independent = getattr(getattr(manager.delegation, 'bridge', None), 'independent', False)
            if not independent and not owns_voice and not hosts_web and not owns_standalone:
                raise ValueError('현재 Discord 송출 담당자 또는 별도로 지정된 웹 담당자를 선택하세요. 음성 권한은 변경하지 않습니다.')
            if not manager.ddns_status()['configured']:
                raise ValueError('대상 PC의 웹 → 무료 주소에서 DuckDNS 주소와 Token을 먼저 저장해주세요.')
            public = manager.public.status()
            if manager.publication_allowed() and public['state'] == 'prepared' and public.get('address'):
                self._finish(call, True, '이미 웹 공개 중입니다.\n'+public['address'])
                return
            if independent:
                manager.start_web(approved=True)
            else:
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

    def _finish(self, call, ok, message, *, stopped=False, for_handover=False):
        coordinator = self.coordinator
        try:
            def update(data):
                member = data.get('members', {}).get(coordinator.client, {})
                current = member.get('web_call', {})
                if (current.get('id') != call['id'] or current.get('runtime') != call['runtime']
                        or member.get('runtime') != call['runtime']
                        or current.get('state') not in ({'pending', 'accepted'} if call.get('action') in {'update', 'refresh', 'program_update'} and not ok else {'accepted'})):
                    raise ValueError('새 웹 요청이 있어 이전 결과를 기록하지 않습니다.')
                if for_handover and ok and stopped:
                    operation = data.get('web_operation') or {}
                    if (operation.get('id') != call.get('handover_operation')
                            or operation.get('expires', 0) <= time.time()):
                        raise ValueError('웹 인계 요청이 바뀌어 이전 종료 결과를 기록하지 않았습니다.')
                    # Commit stopped host and verified RPC result together; the
                    # recipient can proceed after a single durable event.
                    StandaloneHosting.mark_stopped(data, coordinator)
                current.update(state='done', ok=bool(ok), message=message, stopped=bool(stopped))
            with coordinator.lock:
                coordinator._assert_profile()
                data = coordinator.record.mutate(update)
            self.observe(data, coordinator._status())
            coordinator._notify(data)
            return True
        except Exception as exc:
            coordinator.app._append_debug_log('discord_web_result_failed type='+type(exc).__name__)
            return False

    def _ack_program_reply(self, target, call):
        from pathlib import Path
        import json
        coordinator = self.coordinator
        path = Path(coordinator.app._get_discord_schedule_request_dir()) / 'results' / (call['id'] + '.delivered.json')
        deadline = time.monotonic() + RESULT_ACK_SECONDS
        while coordinator.alive and time.monotonic() < deadline:
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                if value.get('request_id') != call['id'] or value.get('runtime') != call['requester_runtime']:
                    raise ValueError('업데이트 결과 전달 확인의 실행 세션이 다릅니다.')
                def acknowledge(data):
                    member = data.get('members', {}).get(target, {})
                    current = member.get('web_call', {})
                    if member.get('runtime') != call['runtime'] or current.get('id') != call['id'] or current.get('runtime') != call['runtime'] or current.get('state') != 'done' or not current.get('ok'):
                        raise ValueError('전체 업데이트 요청이 변경되었습니다.')
                    current['notified'] = True
                with coordinator.lock:
                    record = coordinator.record.mutate(acknowledge)
                self.observe(record, coordinator._status())
                coordinator._notify(record)
                path.unlink(missing_ok=True)
                return
            except FileNotFoundError:
                time.sleep(.1)
            except (OSError, ValueError, KeyError, AttributeError) as exc:
                coordinator.app._append_debug_log('program_update_reply_confirmation_failed type=' + type(exc).__name__)
                return
        coordinator.app._append_debug_log('program_update_reply_confirmation_timeout')

    def _wait_program_reply(self, call):
        deadline = time.monotonic() + RESULT_ACK_SECONDS
        with self.condition:
            while self.coordinator.alive:
                member = self.latest.get('members', {}).get(self.coordinator.client, {})
                current = member.get('web_call', {})
                if member.get('runtime') != call['runtime'] or current.get('id') != call['id'] or current.get('runtime') != call['runtime']:
                    return False
                if current.get('notified'):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
        return False

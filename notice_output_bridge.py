"""Authenticated localhost control of the GUI-owned bot; no notice policies."""
import asyncio
import json
import time
import urllib.request
from pathlib import Path


def request_notice(port, secret, pid, command):
    data = dict(command, pid=pid, sent_at=time.time())
    request = urllib.request.Request(f'http://127.0.0.1:{int(port)}/notice',
        data=json.dumps(data).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', 'X-Notice-Secret': secret})
    with urllib.request.urlopen(request, timeout=4) as response:
        result = json.loads(response.read(65536))
    if result.get('pid') != pid or result.get('id') != command.get('id'):
        raise RuntimeError('알리미 출력 응답의 프로세스/요청이 일치하지 않습니다.')
    return result


async def handle_notice_command(bot, command, status, *, clock=time.monotonic):
    """Only the current secret holder can call this on the bot event loop."""
    identity = str(command.get('id') or '')
    if not identity or len(identity) > 100:
        raise ValueError('알리미 요청 ID가 없습니다.')
    if command.get('pid') != status.pid or abs(time.time() - float(command.get('sent_at', 0))) > 5:
        raise ValueError('만료되었거나 다른 봇의 요청입니다.')
    action = command.get('action')
    output = getattr(bot, 'notice_output', None)
    scope = 'notice:' + identity
    owns = output is not None and output.scope_id == scope
    accepted = False
    if action == 'stop':
        accepted = await output.stop() if owns else True
        return dict(pid=status.pid, id=identity, accepted=accepted, state='stopped' if accepted else 'failed')
    blocked = (status.standby or status.handover_hold or status.shutdown_requested.is_set()
               or not status.voice_connected or not status.online
               or command.get('guild_id') != status.guild_id)
    if blocked:
        if owns:
            await output.stop()
        return dict(pid=status.pid, id=identity, accepted=False, state='failed')
    if action == 'prepare':
        if not owns:
            path = Path(str(command.get('path') or ''))
            if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in {'.wav', '.mp3', '.ogg'}:
                raise ValueError('준비된 로컬 음성 파일이 필요합니다.')
            if output is not None and not output.released:
                raise RuntimeError('이전 알리미 출력이 아직 종료되지 않았습니다.')
            source = bot._create_audio_source(str(path))
            try:
                accepted = await bot._install_prepared_notice_output(source, scope_id=scope, deadline=clock() + 3)
            finally:
                if not accepted:
                    source.cleanup()
            output = bot.notice_output
        else:
            accepted = True  # Retry of the same silent prepare never resets position.
    elif owns:
        # The controller checks validity before every heartbeat. If it dies or
        # loses ownership, audio becomes silent within three seconds.
        output.deadline = clock() + 3
        if action in {'play', 'resume'}:
            accepted = await output.play()
        elif action == 'pause':
            async with bot.voice_transition_lock:
                if output.owns(bot.current_voice_playback_token):
                    _, _, accepted = await bot._stop_current_voice_playback_and_wait(reason='notice_preempt')
                else:
                    accepted = True
        elif action == 'status':
            accepted = True
        else:
            raise ValueError('지원하지 않는 알리미 명령입니다.')
    state = output.state if output is not None and output.scope_id == scope else 'failed'
    if state == 'pausing':
        state = 'playing'  # Not silent until the bot confirms the transition.
    return dict(pid=status.pid, id=identity, accepted=bool(accepted), state=state)

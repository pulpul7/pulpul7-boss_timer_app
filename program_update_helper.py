"""Detached updater: never stops the persistent web process or overwrites its EXE."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import program_update_guard as guard
from web_service_common import file_transaction
from runtime_storage import atomic_write
from program_updater import (APPLY_FLAG, FILES, GUI, BOT, POINTER, contained, load_job, read,
                             write, verify_version, pointer_for, spawn_gui, digest_file)
from program_update_process import identity, alive, wait_exited, reap_bots, stop_trial, trial_processes

CONFIG_SUFFIXES = {'.ini', '.json', '.txt', '.toml'}
SKIP_DIRS = {'update_program', 'web_service', 'tts_캐시', 'voice', 'archive_logs',
             'cache', 'shared_schedules', 'boss_capture_records', 'season_prestart_logs',
             'discord_schedule_requests', 'user_voice', '음성로그', 'backups', 'versions'}
ATTEMPT_ENV = 'BOSS_TIMER_HELPER_ATTEMPT'


def config_files(data):
    """Snapshot mutable settings; live web sessions/certificates remain untouched."""
    for directory, dirs, files in os.walk(data, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (Path(directory) / d).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if (path.suffix.lower() in CONFIG_SUFFIXES and not path.is_symlink()
                    and '.connection.' not in name and '.request' not in name):
                yield path.relative_to(data).as_posix()
    # Only these mutable web defaults belong to the deployment transaction.
    # Sessions, passwords, HTTPS, router ownership and the live worker stay out.
    for name in ('boss_spectrum.json', 'font_settings.json'):
        path = Path(data) / 'web_service' / name
        if path.is_file() and not path.is_symlink():
            yield path.relative_to(data).as_posix()


def backup_settings(job, folder):
    data = Path(job['data'])
    inventory = {}
    for relative in config_files(data):
        target = contained(folder / 'settings', relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with contained(data, relative).open('rb') as source, target.open('wb') as output:
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        inventory[relative] = digest_file(target)
    write(folder / 'settings_inventory.json', inventory)


def restore_settings(job, folder):
    inventory_path = folder / 'settings_inventory.json'
    if not inventory_path.exists():
        return
    data = Path(job['data'])
    inventory = read(inventory_path)
    if not isinstance(inventory, dict):
        raise ValueError('설정 백업의 무결성 정보를 확인하지 못했습니다.')
    for relative, digest in inventory.items():
        if digest_file(contained(folder / 'settings', relative)) != digest:
            raise ValueError('설정 백업이 변경되어 기존 설정을 덮어쓰지 않았습니다.')
    for relative in config_files(data):
        if relative not in inventory:
            contained(data, relative).unlink()  # Only mutable config created by the failed trial.
    for relative in inventory:
        target = contained(data, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, contained(folder / 'settings', relative).read_bytes())


def spawn_helper(path, *, recovery_parent=None):
    job = load_job(path)
    folder = Path(path).parent
    helper = folder / ('update_helper_' + uuid.uuid4().hex + '.exe')
    shutil.copy2(sys.executable, helper)
    job['helper_sha256'] = digest_file(helper)
    if recovery_parent:
        job['recovery_parent'] = recovery_parent
    write(path, job)
    environment = os.environ.copy()
    environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    environment['PYINSTALLER_SUPPRESS_SPLASH_SCREEN'] = '1'
    environment.pop('BOSS_TIMER_UPDATE_JOB', None)
    environment[ATTEMPT_ENV] = job['attempt']
    process = subprocess.Popen([str(helper), APPLY_FLAG, str(path)], cwd=str(folder), env=environment,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
    write(folder / 'helper_process.json', dict(attempt=job['attempt'], process=identity(process.pid)))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            ready = read(folder / 'helper_started.json')
            if (ready.get('ticket') == job['ticket'] and ready.get('attempt') == job['attempt']
                    and Path(ready['helper']['path']).resolve() == helper.resolve() and alive(ready['helper'])):
                return process
        except FileNotFoundError:
            pass
        if process.poll() is not None:
            raise ValueError('업데이트 도우미를 시작하지 못했습니다. 본체와 웹은 유지합니다.')
        time.sleep(.1)
    raise ValueError('업데이트 도우미의 준비 응답을 받지 못했습니다. 본체와 웹은 유지합니다.')


def recover_entry(fence):
    path = Path(fence['transaction'])
    job = load_job(path)
    if job['ticket'] != fence['ticket']:
        raise ValueError('업데이트 복구 기록이 일치하지 않습니다.')
    if fence.get('owner') and alive(fence['owner']):
        return False  # The original GUI owns the handoff; do not race its journal write.
    if job.get('attempt') != fence.get('attempt'):
        if job['phase'] == 'prepared':
            guard.finish(job['data'], job['ticket'], attempt=fence['attempt'])
            return True  # Power failed before the GUI armed the transaction.
        raise ValueError('업데이트 적용 시도가 변경되어 이전 도우미를 시작하지 않습니다.')
    helper_record = path.parent / 'helper_process.json'
    recorded = read(helper_record) if helper_record.exists() else {}
    current_helper = recorded.get('process') if recorded.get('attempt') == job.get('attempt') else None
    if current_helper and alive(current_helper):
        return False
    spawn_helper(path, recovery_parent=identity(os.getpid()))
    return False


def apply(path):
    with file_transaction(Path(path).parent / 'apply.lock'):
        _apply_locked(path)


def _apply_locked(path):
    job = load_job(path)
    folder = Path(path).parent
    state = guard.read(job['data'])
    if (state is None or state['ticket'] != job['ticket'] or state.get('attempt') != job.get('attempt')
            or (os.environ.get(ATTEMPT_ENV) is not None and os.environ[ATTEMPT_ENV] != job.get('attempt'))):
        raise ValueError('업데이트 유지보수 차단을 확인하지 못했습니다.')
    resume_phase = job['phase']
    job['helper'] = identity(os.getpid())
    write(path, job)
    write(folder / 'helper_started.json', dict(ticket=job['ticket'], attempt=job['attempt'], helper=job['helper']))
    cancelled = lambda: not guard.matches(job['data'], job['ticket'], job['attempt'])
    # GUI exit is cooperative; never kill a GUI while it may be saving schedules.
    if job.get('recovery_parent'):
        wait_exited(job['recovery_parent'], 120, cancelled=cancelled)
    wait_exited(job['parent'], 120, cancelled=cancelled)
    if cancelled():
        raise ValueError('이전 업데이트 시도가 취소되었습니다.')
    reap_bots(job['bots'], job['bot_path'])
    if resume_phase in {'complete', 'rolled_back'}:
        guard.finish(job['data'], job['ticket'], attempt=job['attempt'])
        spawn_gui(Path(job['destination']) / GUI if resume_phase == 'complete' else job['previous'], job['install'])
        return
    try:
        if resume_phase in {'switching', 'verifying', 'rollback', 'recovery_required'}:
            raise ValueError('중단된 업데이트를 이전 버전으로 복구합니다.')
        verify_version(job['destination'], job['manifest'])
        backup_settings(job, folder)
        job.update(phase='switching', message='새 프로그램으로 전환 중')
        write(path, job)  # Durable before the pointer can change.
        write(Path(job['install']) / POINTER, pointer_for(job))
        job.update(phase='verifying', message='새 프로그램과 봇 준비 확인 중')
        write(path, job)
        guard.phase(job['data'], job['ticket'], 'verifying', attempt=job['attempt'])
        process = spawn_gui(Path(job['destination']) / GUI, job['install'], job_path=path)
        job['trial'] = identity(process.pid)
        write(path, job)
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            ready_path = folder / 'ready.json'
            if ready_path.exists():
                ready = read(ready_path)
                if ready.get('attempt') != job['attempt']:
                    time.sleep(.25)
                    continue
                if (ready.get('ticket') != job['ticket'] or ready.get('version') != job['version']
                        or not alive(ready.get('gui'))
                        or Path(ready['gui']['path']).resolve() != Path(job['destination']) / GUI):
                    raise ValueError('새 프로그램의 준비 완료 신호가 일치하지 않습니다.')
                if job['resume'].get('bot') and not alive(ready.get('bot')):
                    raise ValueError('새 봇의 실행 상태를 확인하지 못했습니다.')
                if ready.get('bot') and Path(ready['bot']['path']).resolve() != Path(job['destination']) / BOT:
                    raise ValueError('새 버전 폴더의 봇이 아닌 응답은 완료로 인정하지 않습니다.')
                job.update(phase='complete', message='전체 업데이트 완료 · 이전 버전 보존')
                write(path, job)
                guard.finish(job['data'], job['ticket'], attempt=job['attempt'])
                return
            if process.poll() is not None:
                raise ValueError('새 프로그램이 준비 완료 전에 종료되었습니다.')
            time.sleep(.25)
        raise ValueError('새 프로그램의 준비 완료 신호를 받지 못했습니다.')
    except Exception as exc:
        job.update(phase='rollback', message=str(exc))
        write(path, job)
        guard.phase(job['data'], job['ticket'], 'rollback', attempt=job['attempt'])
        try:
            # Also finds the one-file launcher/child if power loss interrupted
            # publication of trial/ready identities. The old web uses another folder.
            for process in trial_processes(job['destination']):
                stop_trial(process)
            trial_bots = folder / 'trial_bots.json'
            if trial_bots.exists():
                reap_bots(read(trial_bots), str(Path(job['destination']) / BOT))
            ready_path = folder / 'ready.json'
            if ready_path.exists():
                stop_trial(read(ready_path).get('gui'))
            stop_trial(job.get('trial'))
            pointer = Path(job['install']) / POINTER
            if job['previous_pointer'] is None:
                pointer.unlink(missing_ok=True)
            else:
                write(pointer, job['previous_pointer'])
            restore_settings(job, folder)
            failures = Path(job['data']) / 'update_program' / 'failed_versions.json'
            failed = read(failures) if failures.exists() else {}
            failed[job['version']] = str(exc)
            write(failures, failed)
            job.update(phase='rolled_back', message='업데이트 실패 · 이전 버전 복원 완료')
            write(path, job)
            guard.finish(job['data'], job['ticket'], attempt=job['attempt'])
            spawn_gui(job['previous'], job['install'])
        except Exception as failure:
            job.update(phase='recovery_required', message='자동 복구 중단: ' + str(failure))
            write(path, job)
            raise


def main(argv):
    if len(argv) != 1:
        return 2
    try:
        apply(Path(argv[0]).resolve())
        return 0
    except Exception as exc:
        # Keep the fence on uncertain process/file states; next launch recovers.
        path = Path(argv[0])
        try:
            job = load_job(path)
            if os.environ.get(ATTEMPT_ENV) == job.get('attempt'):
                job['message'] = str(exc)
                write(path, job)
        except Exception:
            pass
        return 1

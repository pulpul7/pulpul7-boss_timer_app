"""GitHub-backed handover protocol. Remote writes use the existing Contents CAS.

No credentials, computer names or user names are published. A random local ID
identifies the administrator installation. UI and audio stay gated until BOTH
parallel incoming branches have completed and ownership is committed.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import queue
import threading
import time
import uuid

from discord_connection_policy import ConnectionPolicy

POLL_SECONDS = 5
HANDOVER_SECONDS = 180


class HandoverError(RuntimeError):
    pass


class HandoverRecord:
    def __init__(self, get, put, scope):
        self.get, self.put, self.scope = get, put, dict(scope)
        # One owner per Discord guild, even if someone selected another season.
        key = hashlib.sha256(str(scope["guild"]).encode()).hexdigest()[:24]
        self.path = f"data/handover/{key}.json"

    @staticmethod
    def abandoned_self_join(data, client, now=None):
        """Only an unsuccessful solo join, never another admin's handover."""
        if not data or not client:
            return False
        now = time.time() if now is None else now
        deadline = data.get("deadline")
        expired_join = (data.get("phase") == "joining" and type(deadline) in (int, float)
                        and deadline < now)
        return (data.get("owner") == client == data.get("receiver")
                and data.get("released") is True and not data.get("artifact")
                and not str(data.get("connection", "")).startswith("완료")
                and (data.get("phase") == "failed" or expired_join))

    def own_active_scope(self, data, client):
        """Completed ownership only, not another admin or an in-flight transfer."""
        return bool(data and client and data.get("phase") == "active"
                    and data.get("owner") == client
                    and data.get("receiver") in (None, "", client)
                    and isinstance(data.get("scope"), dict)
                    and data["scope"].get("guild") == self.scope["guild"])

    def read(self, *, recovery_client=None, inspect_own_active=False):
        data, sha, error = self.get(self.path)
        if error:
            raise HandoverError(error)
        if data is not None and (not isinstance(data, dict) or data.get("schema") != 1):
            raise HandoverError("인계 기록 형식을 확인할 수 없습니다.")
        # Ordinary requests/polling remain strict. Own active records may be
        # inspected for explicit recovery, but cannot be claimed by request().
        if (data and data.get("scope") != self.scope
                and not (data.get("phase") == "idle" and not data.get("owner"))
                and not self.abandoned_self_join(data, recovery_client)
                and not (inspect_own_active and self.own_active_scope(data, recovery_client))):
            raise HandoverError("다른 서버·시즌의 담당 기록입니다. 기존 담당자가 종료한 뒤 설정을 확인하세요.")
        return data, sha

    def release_previous_scope(self, client, expected_sha, *, stopped=False):
        """Release exactly the reviewed record, only after local shutdown succeeded."""
        if stopped is not True:
            raise HandoverError("기존 로컬 봇 종료 확인이 필요합니다.")
        data, sha = self.read(recovery_client=client, inspect_own_active=True)
        if sha != expected_sha or not self.own_active_scope(data, client) or data.get("scope") == self.scope:
            raise HandoverError("담당 기록이 변경되어 이전 시즌 기록 복구를 중단했습니다. 다시 확인해주세요.")
        data.update(owner="", receiver="", phase="idle", artifact=None, released=True,
                    updated=time.time(), message="동일 담당자의 이전 시즌 봇 종료 확인")
        self.write(data, sha)

    def write(self, data, sha):
        ok, error = self.put(self.path, data, sha=sha, message="BossTimer administrator handover")
        if not ok:
            raise HandoverError(error or "다른 관리자가 동시에 인계를 변경했습니다.")

    def request(self, client, now=None):
        now = time.time() if now is None else now
        old, sha = self.read(recovery_client=client)
        recovering = bool(old and old.get("phase") not in {"active", "idle"}
                          and old.get("receiver") == client and old.get("released")
                          and (old.get("phase") == "failed" or now > old.get("deadline", now)))
        if old and old.get("phase") not in {"active", "idle", "failed"} and not recovering:
            raise HandoverError("다른 인계가 진행 중이거나 미완료 상태입니다. 기존 담당자 확인이 필요합니다.")
        if old and old.get("phase") == "failed" and old.get("released") and not recovering:
            raise HandoverError("이전 인계가 로그아웃 후 실패했습니다. 자동으로 이전 자료를 사용하지 않습니다. 복구 확인이 필요합니다.")
        solo = not old or not old.get("owner") or old.get("owner") == client or recovering
        data = dict(schema=1, scope=self.scope, owner=(old or {}).get("owner") or client,
                    receiver=client, request=uuid.uuid4().hex, phase="joining" if solo else "requested",
                    deadline=now + HANDOVER_SECONDS, updated=now, released=solo,
                    message="접속 준비" if solo else "기존 관리자 응답 대기", artifact=old.get("artifact") if recovering else None,
                    connection="대기", sync="기존 로컬 자료 유지" if solo else "대기")
        self.write(data, sha)
        return data

    def change(self, request_id, actor, *, phases, **changes):
        data, sha = self.read()
        if not data or data.get("request") != request_id or data.get("phase") not in phases:
            raise HandoverError("인계 요청이 변경되었거나 이미 종료되었습니다.")
        if actor not in {data.get("owner"), data.get("receiver")}:
            raise HandoverError("인계 담당자가 일치하지 않습니다.")
        if time.time() > data["deadline"] and changes.get("phase") != "failed":
            raise HandoverError("인계 제한시간을 초과했습니다.")
        data.update(changes, updated=time.time())
        self.write(data, sha)
        return data


class HandoverProgress:
    """A local Tk grab locks every app window, but never blocks the event loop."""
    def __init__(self, app, outgoing=False):
        import tkinter as tk
        from tkinter import ttk
        parent = app.schedule_window if app._widget_available(app.schedule_window) else app.root
        self.window = tk.Toplevel(parent)
        self.window.title("관리자 스케줄 인계")
        self.window.configure(bg="#eff6ff")
        self.window.transient(parent)
        self.window.resizable(False, False)
        app._center_window_over_parent(self.window, parent, 570, 280)
        tk.Label(self.window, text="스케줄 인계 중" if outgoing else "스케줄 승계 · 봇 접속",
                 bg="#1e3a8a", fg="white", font=(app.current_font_family, 15, "bold"), pady=15).pack(fill="x")
        self.text = tk.StringVar(value="담당 관리자 확인 중...")
        tk.Label(self.window, textvariable=self.text, bg="#eff6ff", fg="#17365d", justify="left",
                 anchor="w", wraplength=530, padx=20, pady=15).pack(fill="both", expand=True)
        self.bar = ttk.Progressbar(self.window, mode="indeterminate")
        self.bar.pack(fill="x", padx=20, pady=6)
        self.bar.start(40)
        self.footer = tk.Label(self.window, text="접속·동기화가 모두 완료될 때까지 편집과 송출이 잠깁니다.", bg="#eff6ff", fg="#475569")
        self.footer.pack(pady=10)
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)
        self.window.grab_set()
        self.window.focus_force()

    def update(self, record):
        elapsed = max(0, int(time.time() - (record.get("deadline", time.time()) - HANDOVER_SECONDS)))
        self.text.set(f"{record.get('message', '')}\n\n동기화: {record.get('sync', '대기')}\n봇 접속: {record.get('connection', '대기')}\n경과: {elapsed}초 / 최대 {HANDOVER_SECONDS}초")

    def close(self):
        self.bar.stop()
        self.window.grab_release()
        self.window.destroy()


class DiscordHandover:
    def __init__(self, app):
        self.app = app
        self.profile = app._get_discord_bot_config_storage_path()
        self.policy = ConnectionPolicy(self.profile)
        state = self.policy.snapshot()
        identity = app._get_administrator_identity()
        self.client = identity["client_id"]
        if state["client_id"] != self.client:
            self.policy.update(client_id=self.client)
        self.entry = dict(app._get_current_github_upload_server_entry())
        self.scope = dict(guild=str(app.discord_bot_server_id), server=str(self.entry["id"]), season=str(app.current_season_no))
        self.record = HandoverRecord(app._github_get_json_file, app._github_put_json_file, self.scope)
        self.events = queue.Queue()
        self.busy = False
        self.dialog = None
        self.alive = True
        self.current = None
        self.poll_inflight = False
        self.remote_lock = threading.Lock()
        self.app.root.after(100, self._drain)
        self.app.root.after(1000, self._poll)

    def _assert_profile(self):
        if (self.app._get_discord_bot_config_storage_path() != self.profile
                or str(self.app.current_season_no) != self.scope["season"]
                or str(self.app._get_current_github_upload_server_entry()["id"]) != self.scope["server"]
                or str(self.app.discord_bot_server_id) != self.scope["guild"]):
            raise HandoverError("서버 설정이 변경되어 인계를 중단했습니다.")

    def _ui(self, callback, wait=True):
        done, cancelled, result = threading.Event(), threading.Event(), []
        def run():
            if cancelled.is_set():
                return
            try:
                self._assert_profile()
                result.append((True, callback()))
            except Exception as exc:
                result.append((False, exc))
            finally:
                done.set()
        self.events.put(run)
        if not wait:
            return
        if not done.wait(HANDOVER_SECONDS):
            cancelled.set()
            raise HandoverError("화면 처리 응답시간을 초과했습니다.")
        ok, value = result[0]
        if not ok:
            raise value
        return value

    def _drain(self):
        if not self.alive:
            return
        while True:
            try:
                callback = self.events.get_nowait()
            except queue.Empty:
                break
            callback()
        if self.dialog and self.current:
            self.dialog.update(self.current)
        self.app.root.after(100, self._drain)

    def _show(self, outgoing=False):
        self._assert_profile()
        if self.busy:
            raise HandoverError("이미 인계를 진행하고 있습니다.")
        self.busy = True
        self.app.discord_handover_busy = True
        try:
            self.original_signature = (self.app._get_schedule_restore_snapshot_signature(self.app._create_schedule_restore_snapshot())
                                       if hasattr(self.app, "_get_schedule_restore_snapshot_signature") else None)
            self.policy.update(handover_hold=True, handover_role="outgoing" if outgoing else "incoming")
            self.dialog = HandoverProgress(self.app, outgoing)
        except Exception:
            self.busy = False
            self.app.discord_handover_busy = False
            raise

    def _display(self, data):
        # Delayed Tk updates must not overwrite newer worker/protocol state.
        if self.current and data.get("updated", 0) < self.current.get("updated", 0):
            data = self.current
        if self.dialog:
            self.dialog.update(data)

    def _change(self, **changes):
        with self.remote_lock:
            data = self.record.change(self.current["request"], self.client,
                                      phases={"requested", "uploading", "released", "joining"}, **changes)
            self.current = data
        self._ui(lambda: self._display(data), wait=False)
        return data

    def start(self):
        self.current = None  # Never publish a new failure onto an earlier request.
        self._show()
        threading.Thread(target=self._incoming, daemon=True, name="discord-handover-in").start()

    def _incoming(self):
        try:
            existing, existing_sha = self.record.read(recovery_client=self.client, inspect_own_active=True)
            if (existing and existing.get("scope") != self.scope
                    and self.record.own_active_scope(existing, self.client)):
                confirmed = self._ui(lambda: self.app._show_centered_messagebox(
                    "askyesno", "이전 시즌 담당 기록 정리",
                    "이 담당자 ID의 이전 서버·시즌 운영 기록이 남아 있습니다.\n"
                    "기존 봇을 종료하고 현재 시즌으로 이어서 접속할까요?\n"
                    "이전 시즌 스케줄을 가져오지 않으며 현재 설정과 스케줄은 유지합니다.\n"
                    "같은 담당자 ID를 다른 PC에 복사했다면 그 PC의 봇도 먼저 종료해주세요.",
                    parent=self.dialog.window))
                if not confirmed:
                    raise HandoverError("이전 시즌 담당 기록 정리를 취소했습니다.")
                stopped = self._ui(lambda: self.app._stop_discord_bot_runtime_core())
                self.record.release_previous_scope(self.client, existing_sha, stopped=stopped)
            if existing and existing.get("scope") != self.scope and self.record.abandoned_self_join(existing, self.client):
                confirmed = self._ui(lambda: self.app._show_centered_messagebox(
                    "askyesno", "이전 접속 실패 기록 복구",
                    "이 PC가 이전 설정으로 접속하다 실패한 기록이 남아 있습니다.\n"
                    "실패한 접속 기록을 현재 설정으로 다시 등록할까요?\n"
                    "스케줄과 보스설정은 변경하지 않습니다.", parent=self.dialog.window))
                if not confirmed:
                    raise HandoverError("이전 접속 기록 복구를 취소했습니다.")
                if not self._ui(lambda: self.app._stop_discord_bot_runtime_core()):
                    raise HandoverError("기존 로컬 봇 종료를 확인하지 못해 기록 복구를 중단했습니다.")
            if existing is None:
                confirmed = self._ui(lambda: self.app._show_centered_messagebox(
                    "askyesno", "인계 최초 등록",
                    "이 서버에는 담당 관리자 기록이 없습니다. 구버전 봇은 자동 확인할 수 없습니다.\n"
                    "다른 PC의 봇이 종료된 것을 확인하셨습니까? 확인한 경우에만 접속합니다.",
                    parent=self.dialog.window))
                if not confirmed:
                    raise HandoverError("기존 봇 종료 확인 전에는 접속하지 않습니다.")
            self.current = self.record.request(self.client)
            while not self.current["released"]:
                self._ui(lambda data=self.current: self._display(data), wait=False)
                if time.time() >= self.current["deadline"]:
                    raise HandoverError("기존 관리자 응답 또는 업로드 완료를 기다리다 제한시간을 초과했습니다.")
                time.sleep(POLL_SECONDS)
                data, _ = self.record.read()
                if not data or data.get("request") != self.current["request"]:
                    raise HandoverError("인계 요청이 다른 요청으로 변경되었습니다.")
                self.current = data
                if data["phase"] == "failed":
                    raise HandoverError(data.get("message") or "기존 관리자 인계 실패")
            self._change(phase="joining", message="봇 접속과 스케줄 동기화를 병행합니다.")
            # Neither branch enables output. Both must complete first.
            with ThreadPoolExecutor(max_workers=2) as executor:
                sync = executor.submit(self._download_apply)
                connect = executor.submit(self._connect)
                sync.result()
                connect.result()
            self._change(phase="active", owner=self.client, connection="완료", sync="완료", message="인계 완료 · 운영 시작")
            self.policy.update(handover_hold=False, handover_role="")
            self._ui(lambda: self._finish("인계 완료: 스케줄 적용과 봇 접속이 모두 끝났습니다."))
        except Exception as exc:
            self._fail(exc, outgoing=False)

    def _connect(self):
        self._change(connection="연결 중")
        if not self._ui(lambda: self.app._start_discord_bot_runtime(handover_approved=True)):
            raise HandoverError("봇 실행에 실패했습니다.")
        while time.time() < self.current["deadline"]:
            status = self.app._query_discord_bot_status_port(timeout=.5)
            if status.get("configuration_error"):
                raise HandoverError(status["configuration_error"])
            if (status.get("online") and status.get("voice_connected")
                    and str(status.get("guild_id")) == self.scope["guild"]):
                self._change(connection="완료 (동기화 완료까지 송출 잠금)")
                return
            time.sleep(.5)
        raise HandoverError("봇 접속 제한시간을 초과했습니다.")

    def _download_apply(self):
        artifact = self.current.get("artifact")
        if artifact is None:
            self._change(sync="완료 (기존 담당자 없음 · 로컬 자료 유지)")
            return
        self._change(sync="원본 다운로드·검증 중")
        payload, sha, error = self.app._github_get_json_file(artifact["path"])
        if error or sha != artifact["sha"] or not isinstance(payload, dict):
            raise HandoverError(error or "인계 이후 스케줄 파일이 변경되어 적용을 중단했습니다.")
        if str(payload.get("dataVersion")) != artifact["version"] or self.app._get_github_schedule_content_hash(payload) != artifact["hash"]:
            raise HandoverError("인계 스케줄 버전·내용 검증에 실패했습니다.")
        decoded = self.app._unwrap_github_schedule_payload(payload)
        if not isinstance(decoded, dict) or str(decoded.get("season_no")) != self.scope["season"] or str(decoded.get("share_prefix")) != self.scope["server"]:
            raise HandoverError("스케줄 서버·시즌이 일치하지 않습니다.")
        def apply():
            if time.time() >= self.current["deadline"]:
                raise HandoverError("제한시간 이후 자료는 적용하지 않습니다.")
            if (self.original_signature is not None and self.original_signature !=
                    self.app._get_schedule_restore_snapshot_signature(self.app._create_schedule_restore_snapshot())):
                raise HandoverError("인계 중 로컬 스케줄이 변경되어 덮어쓰기를 중단했습니다.")
            before = self.app._create_schedule_full_state_snapshot()
            try:
                if not self.app._apply_loaded_schedule_shared_payload(decoded, source_label="관리자 인계",
                            source_path=artifact["path"], history_label="관리자 인계 전",
                            sync_shared_export=False, mark_github_dirty=False, save_state=False):
                    raise HandoverError("로컬 백업 또는 스케줄 적용에 실패했습니다.")
                self.app._update_github_import_meta(server_id=self.scope["server"], server_name=str(self.entry["name"]),
                            schedule_path=artifact["path"], schedule_version=artifact["version"],
                            boss_config_path="", boss_config_version="")
                self.app._save_schedule_state(mark_github_dirty=False, sync_shared_export=False, raise_on_error=True)
            except Exception:
                self.app._restore_schedule_full_state_snapshot(before)
                raise
            if hasattr(self.app, "_save_github_local_payload_cache"):
                cache_entry = dict(self.entry, scheduleVersion=artifact["version"], scheduleHash=artifact["hash"])
                if not self.app._save_github_local_payload_cache(cache_entry, schedule_payload=decoded):
                    raise HandoverError("스케줄은 적용됐으나 로컬 동기화 캐시 저장에 실패했습니다. 송출을 중단합니다.")
        self._ui(apply)
        self._change(sync="완료 (봇 접속 완료까지 송출 잠금)")

    def _poll(self):
        if not self.alive:
            return
        if self.app._get_discord_bot_config_storage_path() != self.profile:
            self.alive = False
            return
        if not self.busy and not self.poll_inflight:
            self.poll_inflight = True
            def worker():
                try:
                    data, _ = self.record.read()
                    if data and data.get("owner") == self.client and data.get("phase") == "requested":
                        self.current = data
                        self._ui(lambda: self._show(outgoing=True))
                        self._outgoing()
                    elif (data and data.get("owner") != self.client and not self.policy.snapshot()["standby"]
                          and getattr(self.app, "discord_bot_expected_running", False)):
                        self.policy.pause()
                        self.policy.update(handover_hold=True, handover_role="outgoing")
                        self._ui(lambda: self.app._stop_discord_bot_runtime_core())
                except Exception as exc:
                    self.app._append_debug_log(f"handover_poll_failed type={type(exc).__name__}")
                finally:
                    self.poll_inflight = False
            threading.Thread(target=worker, daemon=True, name="discord-handover-poll").start()
        self.app.root.after(POLL_SECONDS * 1000, self._poll)

    def release_after_stop(self):
        """A confirmed manual logout leaves no stale live-owner record."""
        if self.busy:
            return
        def release():
            try:
                data, sha = self.record.read()
                if data and data.get("owner") == self.client and data.get("phase") == "active":
                    data.update(owner="", receiver="", phase="idle", artifact=None, released=True,
                                updated=time.time(), message="담당 관리자 종료")
                    self.record.write(data, sha)
            except Exception as exc:
                self.app._append_debug_log(f"handover_release_failed type={type(exc).__name__}")
        threading.Thread(target=release, daemon=True, name="discord-handover-release").start()

    def _outgoing(self):
        released = False
        logout_confirmed = False
        try:
            self._change(phase="uploading", message="기존 관리자 스케줄 업로드 중", sync="업로드 중", connection="기존 관리자 연결 유지")
            expected = self._ui(lambda: deepcopy(self.app._build_github_schedule_payload("0.0.0")))
            expected["payload"].update(share_prefix=self.scope["server"], server_name=self.entry["name"])
            expected_hash = self.app._get_github_schedule_content_hash(expected)
            ok, message, entry = self.app._upload_current_schedule_to_github_data(self.entry,
                progress_callback=lambda text: self._ui(lambda: self._display(dict(self.current, message=text)), wait=False))
            if not ok:
                raise HandoverError(message)
            path = str((entry or {}).get("schedule") or self.entry["schedule"])
            payload, sha, error = self.app._github_get_json_file(path)
            if error or not sha or self.app._get_github_schedule_content_hash(payload) != expected_hash:
                raise HandoverError(error or "업로드가 생략되었거나 현재 스케줄과 서버 자료가 다릅니다. 기존 운영을 유지합니다.")
            current = self._ui(lambda: self.app._build_github_schedule_payload("0.0.0"))
            current["payload"].update(share_prefix=self.scope["server"], server_name=self.entry["name"])
            if self.app._get_github_schedule_content_hash(current) != expected_hash:
                raise HandoverError("업로드 중 스케줄이 변경되어 인계를 중단했습니다.")
            artifact = dict(path=path, sha=sha, hash=expected_hash, version=str(payload["dataVersion"]))
            self._change(message="업로드 검증 완료 · 기존 봇 로그아웃 중", sync="업로드 완료", artifact=artifact)
            self.policy.pause()
            released = True  # From here never automatically resume the old owner.
            def logout():
                self.app.discord_bot_expected_running = False
                return self.app._stop_discord_bot_runtime_core()
            if not self._ui(logout):
                raise HandoverError("기존 봇 로그아웃을 확인하지 못했습니다. 새 관리자 접속을 중단합니다.")
            logout_confirmed = True
            self._change(phase="released", released=True, connection="기존 관리자 로그아웃 완료", message="새 관리자 동기화·접속 대기")
            while time.time() < self.current["deadline"]:
                time.sleep(POLL_SECONDS)
                data, _ = self.record.read()
                if not data or data.get("request") != self.current["request"]:
                    raise HandoverError("인계 확인 기록이 변경되었습니다.")
                self.current = data
                self._ui(lambda data=data: self._display(data), wait=False)
                if data["phase"] == "active":
                    self._ui(lambda: self._finish("인계 완료 · 이 관리자는 대기 상태입니다."))
                    return
                if data["phase"] == "failed":
                    raise HandoverError(data["message"])
            raise HandoverError("새 관리자 완료 확인시간을 초과했습니다. 이 PC는 대기를 유지합니다.")
        except Exception as exc:
            self._fail(exc, outgoing=True, released=released, logout_confirmed=logout_confirmed)

    def _fail(self, exc, *, outgoing, released=False, logout_confirmed=False):
        message = str(exc)
        try:
            if self.current and self.current.get("phase") != "active":
                self._change(phase="failed", message=message, released=logout_confirmed or self.current.get("released", False))
        except Exception:
            pass  # Never overwrite a newer request merely to publish an error.
        try:
            if outgoing and not released:
                self.policy.update(handover_hold=False, handover_role="")
            else:
                self.policy.pause()
        except Exception as state_error:
            # A full disk/locked sidecar must not skip process shutdown or leave
            # a permanently grabbed progress window. The pre-existing hold stays.
            message += f"\n연결 상태 저장 실패 ({type(state_error).__name__}). 봇 상태를 확인해 주세요."
        if not outgoing:
            try:
                self._ui(lambda: self.app._stop_discord_bot_runtime_core())
            except Exception:
                pass
        self._ui(lambda: self._finish("인계 중단: " + message, error=True), wait=False)

    def _finish(self, message, error=False):
        if self.dialog:
            self.dialog.close()
            self.dialog = None
        self.busy = False
        self.app.discord_handover_busy = False
        self.app.schedule_status_var.set(message)
        if error:
            self.app._show_centered_messagebox("showerror", "관리자 인계", message,
                                               parent=self.app.schedule_window or self.app.root)

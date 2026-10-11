"""Daily/manual static-page updater; independent from Discord and GUI loops."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import re
import threading
import urllib.error
import urllib.request

from https_transport import urlopen_verified
from web_asset_store import FILES, MAX_FILE, current_version, git_hash, stage_version, version_id
from web_service_common import read_json, timestamp, write_json

REPOSITORY = "pulpul7/pulpul7-boss_timer_app"
API = f"https://api.github.com/repos/{REPOSITORY}"
SOURCE = "web_assets"
KST = timezone(timedelta(hours=9))


def fetch_json(url):
    if not url.startswith(API + "/"):
        raise ValueError("공식 저장소의 웹 업데이트 주소가 아닙니다.")
    request = urllib.request.Request(url, headers={"User-Agent": "BossTimer-Web-Updater/1",
        "Accept": "application/vnd.github+json"})
    with urlopen_verified(request, timeout=15, service="웹 업데이트") as response:
        if not response.url.startswith(API + "/"):
            raise ValueError("웹 업데이트 다운로드 주소가 변경됐습니다.")
        raw = response.read(MAX_FILE * 2 + 1)
    if len(raw) > MAX_FILE * 2:
        raise ValueError("웹 업데이트 응답 용량을 초과했습니다.")
    return json.loads(raw)


def source_rows(rows):
    if not isinstance(rows, list) or len(rows) > 64:
        raise ValueError("웹 업데이트 파일 목록이 올바르지 않습니다.")
    selected = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("name") not in FILES:
            continue
        name, sha = row["name"], row.get("sha", "")
        if (name in selected or row.get("type") != "file" or row.get("path") != SOURCE + "/" + name
                or not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None
                or type(row.get("size")) is not int or not 0 < row["size"] <= MAX_FILE):
            raise ValueError("웹 업데이트 파일 정보가 올바르지 않습니다.")
        selected[name] = row
    if set(selected) != set(FILES):
        raise ValueError("저장소의 web_assets 폴더에 필요한 웹 파일을 모두 올려주세요.")
    return selected


class WebPageUpdater:
    def __init__(self, manager):
        self.manager, self.root = manager, manager.root
        self.path = self.root / "page_update_settings.json"
        self.lock = threading.RLock()
        self.wake, self.closed = threading.Event(), threading.Event()
        self.thread = None
        self.manual = False
        self.settings = dict(schema=1, enabled=True, hour=3, minute=0, last_auto_date="")
        self.view = dict(busy=False, state="idle", message="웹 업데이트 확인 전", checked_at="", applied_at="",
                         version="", source=f"github.com/{REPOSITORY}/tree/main/{SOURCE}")
        self.settings_error = False
        try:
            saved = read_json(self.path)
            self._validate(saved)
            self.settings.update(saved)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.settings_error = True
            self.view.update(state="error", message="웹 자동 업데이트 설정을 읽지 못했습니다. 다시 저장해주세요.")

    @staticmethod
    def _validate(data):
        if (not isinstance(data, dict) or data.get("schema") != 1 or type(data.get("enabled")) is not bool
                or type(data.get("hour")) is not int or not 0 <= data["hour"] < 24
                or type(data.get("minute")) is not int or not 0 <= data["minute"] < 60
                or not isinstance(data.get("last_auto_date", ""), str)):
            raise ValueError("자동 업데이트 시간을 확인해주세요. (00:00~23:59)")

    def status(self):
        with self.lock:
            return dict(deepcopy(self.view), enabled=self.settings["enabled"], hour=self.settings["hour"],
                        minute=self.settings["minute"])

    def configure(self, enabled, hour, minute):
        with self.lock:
            saved = dict(self.settings, enabled=bool(enabled), hour=hour, minute=minute)
            self._validate(saved)
            self.root.mkdir(parents=True, exist_ok=True)
            write_json(self.path, saved)
            self.settings, self.settings_error = saved, False
        self.start()
        self.wake.set()
        self.manager._log("web_page_update_settings_saved", enabled=bool(enabled), hour=hour, minute=minute)

    def start(self):
        with self.lock:
            if not self.closed.is_set() and (self.thread is None or not self.thread.is_alive()):
                self.thread = threading.Thread(target=self._loop, name="web-page-updater", daemon=True)
                self.thread.start()

    def request(self):
        with self.lock:
            if self.closed.is_set() or self.manager.closed.is_set():
                raise ValueError("웹서비스가 종료 중입니다.")
            if self.view["busy"] or self.manual:
                raise ValueError("웹 업데이트가 진행 중입니다. 잠시 기다려주세요.")
            self.manual = True
            self.view.update(busy=True, state="checking", message="웹 업데이트를 확인합니다…")
        self.start()
        self.wake.set()

    def due(self, now):
        with self.lock:
            return (not self.settings_error and self.settings["enabled"] and self.manager.status()["enabled"]
                and self.settings["last_auto_date"] != now.date().isoformat()
                and (now.hour, now.minute) >= (self.settings["hour"], self.settings["minute"]))

    def _check_cancelled(self):
        if self.closed.is_set() or self.manager.closed.is_set():
            raise ValueError("프로그램 종료로 웹 업데이트를 취소했습니다.")

    def _update(self):
        self._check_cancelled()
        self.manager._log("web_page_update_check_begin")
        rows = source_rows(fetch_json(API + "/contents/" + SOURCE + "?ref=main"))
        version = version_id({name: row["sha"] for name, row in rows.items()})
        try:
            installed = current_version(self.root)
        except (OSError, ValueError, KeyError, TypeError):
            installed = ""  # A manual update can repair a damaged installed page.
        if installed == version:
            with self.lock:
                self.view.update(state="current", version=version[:12], message="최신 웹 파일입니다. 복사·재시작을 생략했습니다.")
            self.manager._log("web_page_update_unchanged", version=version[:12])
            return
        files = {}
        for name in FILES:
            self._check_cancelled()
            row = rows[name]
            blob = fetch_json(API + "/git/blobs/" + row["sha"])
            if (not isinstance(blob, dict) or blob.get("encoding") != "base64" or blob.get("sha") != row["sha"]
                    or blob.get("size") != row["size"] or not isinstance(blob.get("content"), str)):
                raise ValueError("웹 파일 다운로드 정보를 확인하지 못했습니다.")
            raw = base64.b64decode("".join(blob["content"].split()), validate=True)
            if len(raw) != row["size"] or git_hash(raw) != row["sha"]:
                raise ValueError("다운로드한 웹 파일이 저장소 내용과 일치하지 않습니다.")
            files[name] = raw
        self._check_cancelled()
        with self.lock:
            self.view.update(state="copying", message="웹 파일을 복사하고 검증합니다…")
        stage_version(self.root, version, files)
        self._check_cancelled()
        with self.lock:
            self.view.update(state="restarting", message="복사 완료 · 웹서비스를 다시 시작합니다…")
        self.manager.apply_page_update(version)
        with self.lock:
            self.view.update(state="applied", version=version[:12], applied_at=timestamp(),
                message="웹 업데이트 적용 완료 · 웹서비스 재시작 완료" if self.manager.status()["enabled"] else
                "웹 파일 적용 완료 · 웹서비스를 켜면 새 화면을 사용합니다.")
        self.manager._log("web_page_update_applied", version=version[:12])

    def _loop(self):
        while not self.closed.is_set() and not self.manager.closed.is_set():
            try:
                now = datetime.now(KST)
                with self.lock:
                    manual, self.manual = self.manual, False
                automatic = not manual and self.due(now)
                if manual or automatic:
                    with self.lock:
                        if automatic:
                            saved = dict(self.settings, last_auto_date=now.date().isoformat())
                            self.root.mkdir(parents=True, exist_ok=True)
                            write_json(self.path, saved)
                            self.settings = saved  # One daily attempt, including failures.
                        self.view.update(busy=True, state="checking", checked_at=timestamp(), message="웹 업데이트를 확인합니다…")
                    self._update()
            except Exception as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                    message = "공식 저장소 main 브랜치의 web_assets 폴더에 웹 파일을 먼저 올려주세요."
                elif isinstance(exc, ValueError):
                    message = str(exc)
                else:
                    message = "웹 업데이트에 실패했습니다. 기존 웹 파일은 유지합니다. 진단 로그를 확인해주세요."
                with self.lock:
                    self.view.update(state="error", message=message)
                self.manager._log("web_page_update_failed", error_type=type(exc).__name__)
            finally:
                with self.lock:
                    self.view["busy"] = bool(self.manual)
            self.wake.wait(15)
            self.wake.clear()

    def close(self):
        self.closed.set()
        self.wake.set()

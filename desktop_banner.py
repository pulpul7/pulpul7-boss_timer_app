"""Local Discord notice relay and asynchronous Windows toast output.

No Discord API calls, speech, or Tk work take place in this module.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
import uuid

APP_ID = "BossTimer.DesktopNotifications"
MAX_AGE = 30.0


def readable_text(value: object) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(value or ""))
    text = re.sub(r"```(?:ansi|\w+)?\s*\n?", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"<@!?\d+>", "담당자", text)
    text = re.sub(r"<@&\d+>", "알림 대상", text)
    text = re.sub(r"<#\d+>", "채널", text)
    text = re.sub(r"<a?:([\w]+):\d+>", r"\1", text)
    text = re.sub(r"https?://\S+", "링크", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return text.replace("**", "").replace("__", "").replace("`", "").strip()


def compact_text(value: object, limit: int) -> str:
    text = " ".join(readable_text(value).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def notice_style(data: dict, body: str) -> dict:
    """Carry Discord ANSI colors as data; no fonts or GUI imports in the bot."""
    palette = {30: "#cbd5e1", 31: "#f87171", 32: "#4ade80", 33: "#fbbf24",
               34: "#60a5fa", 35: "#c084fc", 36: "#22d3ee", 37: "#e5e7eb"}
    text = compact_text(body, 180)
    characters = [(char, "#cbd5e1", False) for char in text]
    # Match the actual new line, including edits, rather than old warning lines.
    for raw in [data.get("content") or "", *[embed.get("description") or ""
                                            for embed in data.get("embeds") or []]]:
        raw = re.sub(r"```(?:ansi|\w+)?\s*\n?", "", str(raw))
        styled, color, bold = [], "#cbd5e1", False
        for token in re.split(r"(\x1b\[[0-9;]*m)", raw):
            if token.startswith("\x1b["):
                for code in (int(value or 0) for value in token[2:-1].split(";")):
                    if code == 0:
                        color, bold = "#cbd5e1", False
                    elif code in palette:
                        color = palette[code]
                    elif code == 39:
                        color = "#cbd5e1"
                    elif code in (1, 22):
                        bold = code == 1
                continue
            for char in token.replace("**", "").replace("__", "").replace("`", ""):
                if char.isspace():
                    if styled and styled[-1][0] != " ":
                        styled.append((" ", color, bold))
                elif ord(char) >= 32:
                    styled.append((char, color, bold))
        plain = "".join(char for char, _, _ in styled)
        needle = text[:-1].rstrip() if text.endswith("…") else text
        start = plain.rfind(needle) if needle else -1
        if start >= 0:
            characters[:len(needle)] = styled[start:start+len(needle)]
            break
    # Readable emphasis also works for ordinary notices without ANSI markup.
    for pattern, color in ((r"5분\s*(?:전|남았습니다)", "#fbbf24"),
                           (r"(?:1|일)분\s*(?:전|남았습니다)", "#fb7185"),
                           (r"젠|타임", "#4ade80")):
        for match in re.finditer(pattern, text):
            for index in range(match.start(), match.end()):
                characters[index] = (characters[index][0], color, True)
    spans = []
    for char, color, bold in characters:
        if spans and (spans[-1]["color"], spans[-1]["bold"]) == (color, bold):
            spans[-1]["text"] += char
        else:
            spans.append({"text": char, "color": color, "bold": bold})
    return {"spans": spans}


def message_notice(data: dict) -> tuple[str, str]:
    """Read visible text only; never expose internal embed footer payloads."""
    parts = [readable_text(data.get("content"))]
    title = "보탐매니저 안내"
    for embed in data.get("embeds") or []:
        if embed.get("title"):
            title = readable_text(embed["title"])
        description = readable_text(embed.get("description"))
        if title.startswith("✦") and description:
            # A fresh spawn message includes the older warning lines as well.
            # Notify the new stage, not all of those warnings a second time.
            description = description.splitlines()[-1]
        parts.append(description)
        for field in embed.get("fields") or []:
            parts.append(f"{readable_text(field.get('name'))}: {readable_text(field.get('value'))}")
    body = "\n".join(part for part in parts if part)
    if body == "보탐매니저 내부 연결 데이터":
        return "", ""
    if not body and title != "보탐매니저 안내":
        body = title
    return title, body


class BannerInbox:
    def __init__(self, app_root):
        self.directory = Path(app_root) / "desktop_banner_events"
        self.control = self.directory / "enabled.json"
        self.seen: dict[str, None] = {}

    def set_enabled(self, enabled: bool) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._write(self.control, {"enabled": bool(enabled), "since": time.time()})

    @staticmethod
    def _write(path: Path, payload: dict) -> None:
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def publish(self, title: str, body: str, key: str, channel_id: str, style=None) -> None:
        try:
            control = json.loads(self.control.read_text(encoding="utf-8"))
            if not control.get("enabled"):
                return
            now = time.time()
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
            self._write(self.directory / (digest + ".json"), {
                "title": compact_text(title, 70), "body": compact_text(body, 180),
                "key": digest, "created_at": now, "channel_id": str(channel_id),
                "style": style or notice_style({}, body),
            })
        except (OSError, ValueError, TypeError):
            pass  # A desktop notification must never break Discord operation.

    def drain(self, channel_id: str, enabled: bool) -> list[dict]:
        if not self.directory.is_dir():
            return []
        result = []
        now = time.time()
        try:
            since = float(json.loads(self.control.read_text(encoding="utf-8")).get("since", now))
            files = sorted(self.directory.glob("*.json"), key=lambda path: path.name)
        except (OSError, ValueError, TypeError):
            return []
        for path in files:
            if path.name == "enabled.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                created = float(data.get("created_at", 0))
                key = str(data.get("key") or "")
                if (enabled and since <= created <= now and now - created <= MAX_AGE
                        and data.get("channel_id") == str(channel_id)
                        and key and key not in self.seen and data.get("body")):
                    self.seen[key] = None
                    result.append(data)
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            finally:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        while len(self.seen) > 512:
            self.seen.pop(next(iter(self.seen)))
        return sorted(result, key=lambda data: data["created_at"])


class DiscordBannerRelay:
    def __init__(self, app_root):
        self.inbox = BannerInbox(app_root)
        self.messages: dict[str, dict] = {}

    def observe(self, data: dict, *, channel_id: str, bot_id: str, bot_ids=(), edited=False):
        key = str(data.get("id") or "")
        old = self.messages.get(key)
        if not key or (edited and old is None):
            return None
        merged = dict(old or {}, **data)
        if (str(merged.get("channel_id")) != str(channel_id)
                or str((merged.get("author") or {}).get("id")) not in {str(bot_id), *map(str, bot_ids)}
                or int(merged.get("flags") or 0) & 64):
            return None
        self.messages[key] = merged
        while len(self.messages) > 256:
            self.messages.pop(next(iter(self.messages)))
        title, body = message_notice(merged)
        if not body or (old and message_notice(old)[1] == body):
            return None
        if old:
            old_lines = set(message_notice(old)[1].splitlines())
            added = [line for line in body.splitlines() if line not in old_lines]
            if added:
                body = "\n".join(added)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return title, body, key + ":" + digest, str(channel_id), notice_style(merged, body)


# User-visible text is supplied as UTF-8 JSON on stdin, never as shell code.
_TOAST_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
$data = [Console]::In.ReadToEnd() | ConvertFrom-Json
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime]
$xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
$xml.LoadXml('<toast duration="short"><visual><binding template="ToastGeneric"><text/><text/></binding></visual><audio silent="true"/></toast>')
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).InnerText = [string]$data.title
$texts.Item(1).InnerText = [string]$data.body
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$toast.Tag = [string]$data.tag
$toast.Group = 'BossTimer'
$toast.SuppressPopup = [bool]$data.quiet
$toast.ExpirationTime = [DateTimeOffset]::Now.AddSeconds(30)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('BossTimer.DesktopNotifications')
if ([int]$notifier.Setting -ne 0) { throw ('notification_disabled_' + $notifier.Setting) }
$notifier.Show($toast)
'''

_TOAST_ENCODED = base64.b64encode(_TOAST_SCRIPT.encode("utf-16-le")).decode("ascii")


class WindowsBanner:
    """Lazy worker: notification setup/PowerShell never blocks Tk or Discord."""
    def __init__(self):
        self.enabled = False
        self.enabled_at = 0.0
        self.jobs = queue.Queue(maxsize=64)
        self.errors = queue.SimpleQueue()
        self.stop = threading.Event()
        self.thread = None
        self.process = None
        self.command = [str(Path(os.environ.get("SystemRoot", r"C:\Windows")) /
                       "System32/WindowsPowerShell/v1.0/powershell.exe"),
                       "-NoProfile", "-NonInteractive", "-EncodedCommand", _TOAST_ENCODED]

    def set_enabled(self, enabled: bool):
        if bool(enabled) != self.enabled:
            self.enabled_at = time.time()
        self.enabled = bool(enabled)

    def prepare(self):
        if not self.enabled or self.stop.is_set():
            return
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="desktop-banner", daemon=True)
            self.thread.start()

    def submit(self, title: str, body: str, *, created_at: float | None = None, quiet=False) -> None:
        if not self.enabled or self.stop.is_set():
            return
        self.prepare()
        try:
            self.jobs.put_nowait((created_at if created_at is not None else time.time(),
                                 compact_text(title, 70), compact_text(body, 180), bool(quiet)))
        except queue.Full:
            self.errors.put("banner_queue_full")

    def _run(self):
        registered = False
        while not self.stop.is_set():
            try:
                created, title, body, quiet = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if not self.enabled or created < self.enabled_at or time.time() - created > MAX_AGE:
                continue
            try:
                if os.name != "nt":
                    raise RuntimeError("windows_required")
                if not registered:
                    import winreg
                    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Classes\AppUserModelId" + "\\" + APP_ID) as key:
                        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "보스타이머")
                    registered = True
                self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                tag = hashlib.sha256(f"{created}:{title}:{body}".encode("utf-8")).hexdigest()[:16]
                payload = json.dumps({"title": title, "body": body, "tag": tag, "quiet": quiet},
                                     ensure_ascii=False).encode("utf-8")
                try:
                    _, error = self.process.communicate(payload, timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.communicate()
                    raise RuntimeError("notification_timeout")
                if self.process.returncode:
                    raise RuntimeError("notification_failed: " + error.decode("utf-8", errors="replace")[:350])
            except Exception as exc:
                self.errors.put(str(exc))
            finally:
                self.process = None

    def close(self):
        self.enabled = False
        self.stop.set()
        process = self.process
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass

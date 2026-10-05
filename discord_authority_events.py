"""Event-driven Discord hints and authenticated localhost control.

Hints never grant ownership: the GUI verifies the durable record on events.
No GitHub requests, periodic messages, credentials or schedule data here.
"""
import asyncio
import json
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime

from discord_connection_policy import CONTROL_PROTOCOL, ConnectionPolicy

MARKER = "BossTimer-Control-v3:"
KINDS = frozenset({"changed", "probe", "reply"})
CONTROL_THREAD_NAME = "보탐매니저 내부 연결"
CONTROL_TRANSPORT = "thread-v1"
COMPLETED_NOTICE_FOOTER = "보탐매니저 · 관리자 접속 완료"


def is_completed_authority_notice(message):
    return any(str(getattr(getattr(embed, "footer", None), "text", "") or "")
               == COMPLETED_NOTICE_FOOTER
               for embed in getattr(message, "embeds", []))


def is_control_message(message):
    return any(str(getattr(getattr(embed, "footer", None), "text", "") or "").startswith(MARKER)
               for embed in getattr(message, "embeds", []))


def request_control(port, secret, status, action, **values):
    identity = uuid.uuid4().hex
    payload = dict(values, id=identity, pid=status["pid"], runtime_id=status["runtime_id"],
                   sent_at=time.time(), action=action)
    request = urllib.request.Request(f"http://127.0.0.1:{int(port)}/authority",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "X-Notice-Secret": secret})
    try:
        with urllib.request.urlopen(request, timeout=7) as response:
            result = json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise RuntimeError("이 PC의 봇 제어 연결을 확인하지 못했습니다. 봇을 종료하고 이 프로그램에서 다시 시작하세요.") from exc
        raise RuntimeError("이 PC의 봇이 승계 제어 요청에 응답하지 못했습니다. 연결 상태를 확인하세요.") from exc
    if result.get("control_transport") != CONTROL_TRANSPORT:
        raise RuntimeError("구버전 봇이 실행 중입니다. 기존 봇을 종료하고 GUI와 봇을 함께 새 버전으로 교체하세요.")
    if (result.get("pid") != status["pid"] or result.get("runtime_id") != status["runtime_id"]
            or result.get("id") != identity or not result.get("ok")):
        raise RuntimeError(result.get("error") or "승계 알림의 실행 세션 확인에 실패했습니다.")
    return result


class AuthorityEvents:
    def __init__(self, bot, status, logger=None):
        self.bot, self.status = bot, status
        self.log = logger or (lambda message: None)
        self.probes = {}
        self.seen = {}
        self.resolved_channel_id = ""
        self.thread = None
        self.thread_lock = asyncio.Lock()
        self.notice_queue = asyncio.Queue()
        self.notice_worker = None
        self.notices = set()

    async def channel(self):
        if not self.bot.message_content_enabled:
            raise RuntimeError("승계 알림 수신을 위해 모든 관리자 봇의 Message Content Intent를 켜야 합니다.")
        channel = await self.bot._resolve_text_channel()
        if (channel is None or not self.bot._is_configured_guild(getattr(channel, "guild", None))
                or getattr(channel.guild, "unavailable", False)):
            raise RuntimeError("안내채팅 채널을 찾지 못했습니다. 안내채팅 ID·서버 ID·채널 보기 권한을 확인하세요. ID를 비우면 ‘보탐매니저’ 텍스트 채널을 찾습니다.")
        permissions = channel.permissions_for(channel.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            raise RuntimeError("관리자 연결·승계를 위해 안내채팅 채널의 채널 보기·메시지 보내기·링크 첨부 권한이 필요합니다.")
        self.resolved_channel_id = str(channel.id)
        self.bot.config["authority_control_channel_id"] = self.resolved_channel_id
        return channel

    def _matches_thread(self, thread, channel):
        return (str(getattr(thread, "parent_id", "")) == str(channel.id)
                and getattr(thread, "name", "") == CONTROL_THREAD_NAME
                and getattr(thread, "type", None) == self.bot.discord.ChannelType.public_thread)

    async def control_thread(self, channel):
        """Keep protocol traffic out of the shared announcement channel."""
        async with self.thread_lock:
            if self.thread is not None and self._matches_thread(self.thread, channel):
                # Prefer current Gateway metadata when the thread was archived.
                self.thread = channel.guild.get_thread(self.thread.id) or self.thread
            else:
                self.thread = None
            if self.thread is None or not self._matches_thread(self.thread, channel):
                threads = [thread for thread in channel.threads if self._matches_thread(thread, channel)]
                if not threads:
                    threads = [thread for thread in await channel.guild.active_threads()
                               if self._matches_thread(thread, channel)]
                if not threads:
                    async for thread in channel.archived_threads(limit=100):
                        if self._matches_thread(thread, channel):
                            threads.append(thread)
                            break
                if threads:
                    self.thread = min(threads, key=lambda thread: thread.id)
                else:
                    permissions = channel.permissions_for(channel.guild.me)
                    if not permissions.create_public_threads:
                        raise RuntimeError("봇의 안내채팅 채널에 ‘공개 스레드 만들기’ 권한이 필요합니다. 내부 연결 메시지는 일반 채팅에 보내지 않습니다.")
                    self.thread = await channel.create_thread(name=CONTROL_THREAD_NAME,
                        type=self.bot.discord.ChannelType.public_thread, auto_archive_duration=1440,
                        reason="BossTimer internal administrator transport")
            permissions = self.thread.permissions_for(channel.guild.me)
            if not (permissions.view_channel and permissions.send_messages_in_threads and permissions.embed_links):
                raise RuntimeError("봇의 안내채팅 채널에 ‘스레드에서 메시지 보내기’와 ‘링크 첨부’ 권한이 필요합니다.")
            if self.thread.locked:
                raise RuntimeError("보탐매니저 내부 연결 스레드가 잠겨 있습니다. Discord에서 잠금을 해제한 뒤 다시 연결하세요.")
            # Sending automatically unarchives an unlocked thread. All public
            # thread messages are delivered through the existing Gateway path.
            return self.thread

    def queue_notice(self, command):
        phase = command.get("notice")
        request = str(command.get("request") or "")
        if phase not in {"started", "completed"} or not request:
            return
        key = (request, phase)
        if key in self.notices:
            return
        self.notices.add(key)
        self.notice_queue.put_nowait(dict(command))
        if self.notice_worker is None or self.notice_worker.done():
            self.notice_worker = asyncio.create_task(self._notice_loop())

    async def _notice_loop(self):
        # A single worker preserves start/completion order without delaying
        # ownership changes or voice connection for announcement delivery.
        while not self.notice_queue.empty():
            command = self.notice_queue.get_nowait()
            try:
                await self._send_notice(command)
            except Exception as exc:
                self.log(f"administrator_notice_failed phase={command.get('notice')} error={exc!r}")
            finally:
                self.notice_queue.task_done()

    async def _send_notice(self, command):
        phase = command["notice"]
        details = command.get("notice_member") or {}
        channel_id = str(command.get("notice_voice_channel") or "")
        if phase == "completed":
            state = self.bot.connection_policy.snapshot()
            if not (self.status.online and getattr(self.status, "voice_connected", False)
                    and str(getattr(self.status, "voice_channel_id", "")) == channel_id
                    and getattr(self.status, "voice_authority_generation", None) == command.get("generation")
                    and state.get("authority_request") == command["request"]
                    and state.get("authority_generation") == command.get("generation")
                    and ConnectionPolicy.has_authority(state, self.status.runtime_id)):
                return  # A late notice cannot describe a superseded connection as active.
        name = self.bot.discord.utils.escape_markdown(str(details.get("name") or "담당자"))[:1024]
        description = f"{name} 담당자가 " + ("관리자 접속을 시작합니다." if phase == "started"
                                                  else "음성채널 접속을 완료했습니다.")
        embed = self.bot.discord.Embed(title="관리자 접속 시작" if phase == "started" else "관리자 접속 완료",
            description=description, color=0xF59E0B if phase == "started" else 0x22C55E,
            timestamp=datetime.now())
        embed.add_field(name="담당자", value=name[:1024], inline=True)
        embed.add_field(name="관리자 ID", value=self.status.client_id[:8], inline=True)
        server = self.bot.discord.utils.escape_markdown(str(details.get("server") or "미등록"))
        embed.add_field(name="시즌 · 서버", value=f"{details.get('season') or '?'}차 · {server}"[:1024], inline=True)
        embed.add_field(name="음성채널", value=f"<#{channel_id}>" if channel_id.isdigit() else "미등록", inline=True)
        if phase == "completed":
            embed.add_field(name="권한 상태", value="관리자 · 송출 중", inline=True)
            embed.add_field(name="스케줄", value="현재 담당자 PC의 스케줄 유지 · 동기화는 수동", inline=False)
        embed.set_footer(text=COMPLETED_NOTICE_FOOTER if phase == "completed" else "보탐매니저 · 관리자 접속 시작")
        channel = await self.channel()
        options = {} if phase == "completed" else {"delete_after": 60}
        await channel.send(embed=embed, allowed_mentions=self.bot.discord.AllowedMentions.none(), **options)

    def member(self):
        state = self.bot.connection_policy.snapshot()
        own = next((row for row in state["query_members"] if row.get("client_id") == self.status.client_id), {})
        return dict(own, client_id=self.status.client_id, runtime=self.status.runtime_id,
                    application_id=self.status.application_id, protocol=CONTROL_PROTOCOL,
                    bot_user_id=str(getattr(self.status, "bot_user_id", "") or self.status.application_id),
                    connected_at=self.status.connected_at, online=self.status.online,
                    sending=bool(self.status.online and state["voice_requested"]
                                 and ConnectionPolicy.has_authority(state, self.status.runtime_id)),
                    joining=bool(self.status.online and state["voice_requested"] and not state["authority_active"]
                                 and ConnectionPolicy.has_authority(state, self.status.runtime_id, allow_joining=True)))

    async def publish(self, kind, **values):
        if kind not in KINDS or not self.status.online:
            raise RuntimeError("승계 알림 연결이 끊겼습니다. 봇 연결을 확인한 뒤 재시도하세요.")
        payload = dict(values, kind=kind, id=uuid.uuid4().hex, at=time.time(),
                       guild=self.bot._get_configured_server_id(), client=self.status.client_id,
                       runtime=self.status.runtime_id, protocol=CONTROL_PROTOCOL)
        embed = self.bot.discord.Embed(description="보탐매니저 내부 연결 데이터")
        embed.set_footer(text=MARKER + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        channel = await self.channel()
        thread = await self.control_thread(channel)
        try:
            await thread.send(embed=embed, allowed_mentions=self.bot.discord.AllowedMentions.none(),
                              silent=True, delete_after=60)
        except self.bot.discord.NotFound:
            self.thread = None
            thread = await self.control_thread(channel)
            await thread.send(embed=embed, allowed_mentions=self.bot.discord.AllowedMentions.none(),
                              silent=True, delete_after=60)
        return payload

    async def command(self, command):
        if (command.get("pid") != self.status.pid or command.get("runtime_id") != self.status.runtime_id
                or abs(time.time() - float(command.get("sent_at", 0))) > 5):
            raise ValueError("만료되었거나 다른 봇의 승계 요청입니다.")
        # An authenticated GUI request carries the current saved channel even
        # when the already logged-in bot still has its startup configuration.
        if "text_channel_id" in command:
            channel_id = str(command["text_channel_id"] or "").strip()
            if channel_id and not channel_id.isdigit():
                raise ValueError("안내채팅 ID는 숫자로 입력하세요.")
            if channel_id != str(self.bot.config.get("text_channel_id") or ""):
                self.resolved_channel_id = ""
            self.bot.config["text_channel_id"] = channel_id
            self.bot.config["authority_control_channel_id"] = channel_id
        channel = await self.channel()
        action = command.get("action")
        result = {}
        if action == "publish":
            await self.publish("changed", request=str(command.get("request") or ""),
                               generation=int(command.get("generation", 0)))
            self.queue_notice(command)
        elif action == "probe":
            probe = str(command["id"])
            self.probes[probe] = {self.status.client_id: self.member()}
            try:
                await self.publish("probe", probe=probe)
                await asyncio.sleep(1.5)
                result["members"] = list(self.probes[probe].values())
            finally:
                self.probes.pop(probe, None)
        elif action == "verify_absent":
            bot_user_id = str(command.get("bot_user_id") or "")
            if not bot_user_id.isdigit():
                raise ValueError("이전 담당자의 봇 ID를 확인할 수 없습니다.")
            guild = channel.guild
            try:
                member = guild.get_member(int(bot_user_id)) or await guild.fetch_member(int(bot_user_id))
                result["absent"] = getattr(getattr(member, "voice", None), "channel", None) is None
            except self.bot.discord.NotFound:
                result["absent"] = True
        elif action == "check":
            await self.control_thread(channel)
        else:
            raise ValueError("지원하지 않는 승계 알림 요청입니다.")
        return dict(result, ok=True, id=command["id"], pid=self.status.pid,
                    runtime_id=self.status.runtime_id,
                    control_transport=CONTROL_TRANSPORT,
                    channel_id=str(channel.id))

    async def receive(self, message):
        # Shared bot embeds require Message Content Intent on every peer.
        channel = getattr(message, "channel", None)
        expected_channel = str(self.bot.config.get("text_channel_id") or self.resolved_channel_id)
        thread_message = (str(getattr(channel, "parent_id", "")) == expected_channel
                          and getattr(channel, "name", "") == CONTROL_THREAD_NAME
                          and getattr(channel, "type", None) == self.bot.discord.ChannelType.public_thread)
        if ((str(getattr(channel, "id", "")) != expected_channel and not thread_message)
                or not self.bot._is_configured_guild(getattr(message, "guild", None))
                or not getattr(getattr(message, "author", None), "bot", False)):
            return False
        for embed in getattr(message, "embeds", []):
            footer = str(getattr(getattr(embed, "footer", None), "text", "") or "")
            if not footer.startswith(MARKER):
                continue
            if not thread_message:
                # Consume old main-channel traffic but never answer it with
                # another visible message. Peers must use the new transport.
                return True
            try:
                payload = json.loads(footer[len(MARKER):])
                if (payload.get("kind") not in KINDS or payload.get("protocol") != CONTROL_PROTOCOL
                        or str(payload.get("guild")) != self.bot._get_configured_server_id()
                        or abs(time.time() - float(payload.get("at", 0))) > 20):
                    return True
                identity = str(payload["id"])
                now = time.monotonic()
                self.seen = {key: at for key, at in self.seen.items() if now - at < 60}
                if identity in self.seen:
                    return True
                self.seen[identity] = now
                if payload.get("client") == self.status.client_id and payload.get("runtime") == self.status.runtime_id:
                    return True
                if payload["kind"] == "probe":
                    await self.publish("reply", probe=str(payload.get("probe") or ""), member=self.member())
                elif payload["kind"] == "reply":
                    members = self.probes.get(str(payload.get("probe") or ""))
                    member = payload.get("member") or {}
                    if (members is not None and member.get("runtime") == payload.get("runtime")
                            and member.get("client_id") == payload.get("client")
                            and str(member.get("bot_user_id") or member.get("application_id")) == str(message.author.id)):
                        members[str(payload["client"])] = member
                else:
                    await self.bot._queue_local_schedule_request(dict(
                        operation="administrator_event", request_id="event_" + identity,
                        server_id=self.bot._get_configured_server_id(), runtime_id=self.status.runtime_id,
                        created_at=time.time(), event=payload, raw_text="승계 변경 알림"))
                return True
            except (ValueError, TypeError, KeyError):
                return True
        return False

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

from discord_connection_policy import CONTROL_PROTOCOL, ConnectionPolicy

MARKER = "BossTimer-Control-v3:"
KINDS = frozenset({"changed", "probe", "reply"})


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
    if (result.get("pid") != status["pid"] or result.get("runtime_id") != status["runtime_id"]
            or result.get("id") != identity or not result.get("ok")):
        raise RuntimeError(result.get("error") or "승계 알림의 실행 세션 확인에 실패했습니다.")
    return result


class AuthorityEvents:
    def __init__(self, bot, status):
        self.bot, self.status = bot, status
        self.probes = {}
        self.seen = {}

    def channel(self):
        if not self.bot.message_content_enabled:
            raise RuntimeError("승계 알림 수신을 위해 모든 관리자 봇의 Message Content Intent를 켜야 합니다.")
        channel_id = str(self.bot.config.get("authority_control_channel_id") or "")
        if not channel_id.isdigit():
            raise RuntimeError("디스코드 설정에 모든 관리자 봇이 사용하는 승계 제어 채널 ID를 입력하세요.")
        channel = self.bot.client.get_channel(int(channel_id))
        if (channel is None or not self.bot._is_configured_guild(getattr(channel, "guild", None))
                or getattr(channel.guild, "unavailable", False)):
            raise RuntimeError("승계 제어 채널을 찾지 못했습니다. 서버 ID·채널 ID·채널 보기 권한을 확인하세요.")
        permissions = channel.permissions_for(channel.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            raise RuntimeError("승계 제어 채널의 채널 보기·메시지 보내기·링크 첨부 권한이 필요합니다.")
        return channel

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
        embed = self.bot.discord.Embed(description="관리자 연결·승계 알림")
        embed.set_footer(text=MARKER + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        await self.channel().send(embed=embed, allowed_mentions=self.bot.discord.AllowedMentions.none(),
                                  delete_after=60)
        return payload

    async def command(self, command):
        if (command.get("pid") != self.status.pid or command.get("runtime_id") != self.status.runtime_id
                or abs(time.time() - float(command.get("sent_at", 0))) > 5):
            raise ValueError("만료되었거나 다른 봇의 승계 요청입니다.")
        self.channel()
        action = command.get("action")
        result = {}
        if action == "publish":
            await self.publish("changed", request=str(command.get("request") or ""),
                               generation=int(command.get("generation", 0)))
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
            guild = self.channel().guild
            try:
                member = guild.get_member(int(bot_user_id)) or await guild.fetch_member(int(bot_user_id))
                result["absent"] = getattr(getattr(member, "voice", None), "channel", None) is None
            except self.bot.discord.NotFound:
                result["absent"] = True
        elif action != "check":
            raise ValueError("지원하지 않는 승계 알림 요청입니다.")
        return dict(result, ok=True, id=command["id"], pid=self.status.pid,
                    runtime_id=self.status.runtime_id,
                    channel_id=str(self.channel().id))

    async def receive(self, message):
        # Shared bot embeds require Message Content Intent on every peer.
        if (str(getattr(getattr(message, "channel", None), "id", ""))
                != str(self.bot.config.get("authority_control_channel_id") or "")
                or not self.bot._is_configured_guild(getattr(message, "guild", None))
                or not getattr(getattr(message, "author", None), "bot", False)):
            return False
        for embed in getattr(message, "embeds", []):
            footer = str(getattr(getattr(embed, "footer", None), "text", "") or "")
            if not footer.startswith(MARKER):
                continue
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

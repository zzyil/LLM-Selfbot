"""Discord server navigation and invite actions for the local owner interface."""
import asyncio
import re
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import discord


def invite_code(value):
    if not isinstance(value, str):
        raise ValueError("Paste a Discord invite link or invite code.")  # noqa: TRY004 - form validation
    value = value.strip()
    if "://" in value:
        url = urlsplit(value)
        if url.scheme not in {"https", "http"} or url.username or url.password or url.port:
            raise ValueError("Use a discord.gg or discord.com/invite link.")
        host, path = (url.hostname or "").lower(), url.path.strip("/").split("/")
        if host in {"discord.gg", "www.discord.gg"} and len(path) == 1:
            value = path[0]
        elif host in {"discord.com", "www.discord.com", "discordapp.com"} and len(path) == 2 and path[0] == "invite":
            value = path[1]
        else:
            raise ValueError("Use a discord.gg or discord.com/invite link.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", value):
        raise ValueError("That is not a Discord invite code.")
    return value


def snowflake(value):
    raw = str(value or "")
    if not re.fullmatch(r"[0-9]{1,20}", raw) or not 0 < int(raw) < 2**64:
        raise ValueError("A valid Discord ID is required.")
    return int(raw)


def guild_row(guild):
    return {"id": str(guild.id), "name": str(guild.name),
            "icon": str(guild.icon.url) if getattr(guild, "icon", None) else "",
            "members": getattr(guild, "member_count", None) or getattr(guild, "approximate_member_count", None)}


def error_result(exc):
    if "captcha" in type(exc).__name__.lower():
        return {"ok": False, "needs_discord": True,
                "reason": "Discord requires a CAPTCHA. Complete the join in Discord, then refresh servers."}
    if isinstance(exc, discord.NotFound):
        return {"ok": False, "reason": "This invite has expired or is invalid."}
    if isinstance(exc, discord.Forbidden):
        return {"ok": False, "needs_discord": True,
                "reason": "Discord refused this action. Check this account's access in Discord."}
    if isinstance(exc, (ValueError, LookupError)):
        return {"ok": False, "reason": str(exc)}
    return {"ok": False, "reason": "Discord could not complete this action. Refresh before trying again.",
            "code": getattr(exc, "code", None)}


class ServerAccess:
    def __init__(self, client, clock=time.monotonic, activity=None):
        self.client, self.clock = client, clock
        self.activity = activity
        self.watching = OrderedDict()
        self.join_lock = asyncio.Lock()
        self.joined = OrderedDict()

    def channel(self, channel_id):
        channel = self.client.get_channel(snowflake(channel_id))
        guild = getattr(channel, "guild", None)
        if guild is None or getattr(guild, "me", None) is None:
            raise ValueError("This server channel is unavailable to the selected account.")
        if int(channel.type) not in {0, 5, 10, 11, 12}:
            raise ValueError("Choose a text channel or an accessible thread.")
        permissions = channel.permissions_for(guild.me)
        if not permissions.view_channel:
            raise ValueError("This account cannot view that channel.")
        if int(channel.type) == 12 and not getattr(channel, "me", None) and not permissions.manage_threads:
            raise ValueError("This account has not joined that private thread.")
        return channel, permissions

    def channels(self, guild_id):
        guild = self.client.get_guild(snowflake(guild_id))
        if guild is None:
            raise ValueError("The selected account has not joined this server.")
        rows = []
        for channel in [*guild.channels, *getattr(guild, "threads", [])]:
            try:
                channel, permissions = self.channel(channel.id)
            except ValueError:
                continue
            thread = int(channel.type) in {10, 11, 12}
            parent = getattr(channel, "parent", None) if thread else None
            category = getattr(channel, "category", None) or getattr(parent, "category", None)
            pending = bool(getattr(guild.me, "pending", False))
            send = permissions.send_messages_in_threads if thread else permissions.send_messages
            reason = ("Complete server verification in Discord." if pending else
                      "This thread is archived or locked." if thread and (channel.archived or channel.locked) else
                      "This account cannot send messages here." if not send else "")
            rows.append({"id": str(channel.id), "name": str(channel.name), "thread": thread,
                         "parent": str(parent.name) if parent else "", "topic": str(getattr(channel, "topic", "") or ""),
                         "category": str(category.name) if category else "Text channels",
                         "category_position": getattr(category, "position", -1),
                         "position": getattr(parent or channel, "position", 0),
                         "can_read": bool(permissions.read_message_history), "can_send": not reason,
                         "reason": reason, "slowmode": getattr(channel, "slowmode_delay", 0),
                         **(self.activity.status(channel) if self.activity is not None else {})})
        rows.sort(key=lambda row: (row["category_position"], row["position"], row["thread"], row["name"]))
        return {"ok": True, "guild": guild_row(guild), "channels": rows,
                "activity": self.activity.policy(guild.id) if self.activity is not None else None}

    def watches(self, channel_id):
        now = self.clock()
        self.watching = OrderedDict((cid, until) for cid, until in self.watching.items() if until > now)
        return self.watching.get(int(channel_id), 0) > now

    async def handle(self, command, payload):
        try:
            if command == "servers_list":
                return {"ok": True, "servers": [guild_row(g) for g in self.client.guilds]}
            if command == "servers_channels":
                return self.channels(payload.get("guild_id"))
            if command == "servers_activity":
                guild = self.client.get_guild(snowflake(payload.get("guild_id")))
                if guild is None or self.activity is None:
                    raise ValueError("Channel activity is unavailable for this server.")
                self.activity.configure(guild.id, payload.get("selected_channel_ids"), payload.get("explore"))
                return self.channels(guild.id)
            if command == "servers_watch":
                channel, _ = self.channel(payload.get("channel_id"))
                self.watching[channel.id] = self.clock() + 90
                self.watching.move_to_end(channel.id)
                while len(self.watching) > 8:
                    self.watching.popitem(last=False)
                return {"ok": True}
            code = invite_code(payload.get("invite"))
            if command == "servers_join":
                async with self.join_lock:
                    expected = snowflake(payload.get("guild_id"))
                    if code in self.joined and self.clock()-self.joined[code][0] < 120:
                        previous = self.joined[code][1]
                        if previous["guild"]["id"] != str(expected):
                            raise ValueError("This invite changed. Preview it again before joining.")
                        return {**previous, "already_joined": True}
                    invite = await self.client.fetch_invite(code)
                    self._check_invite(invite, expected)
                    cached = self.client.get_guild(expected)
                    if cached:
                        return {"ok": True, "guild": guild_row(cached), "already_joined": True}
                    accepted = await self.client.accept_invite(invite)
                    result = {"ok": True, "guild": guild_row(accepted.guild), "already_joined": False}
                    self.joined[code] = (self.clock(), result)
                    while len(self.joined) > 32:
                        self.joined.popitem(last=False)
                    return result
            if command != "servers_invite":
                raise ValueError("Unknown server action.")
            invite = await self.client.fetch_invite(code)
            self._check_invite(invite)
            return {"ok": True, "invite": code, "guild": guild_row(invite.guild),
                    "already_joined": self.client.get_guild(invite.guild.id) is not None}
        except Exception as exc:  # noqa: BLE001 - return an IPC result for every owner action
            return error_result(exc)

    @staticmethod
    def _check_invite(invite, expected=None):
        if getattr(invite.type, "value", invite.type) != 0 or invite.guild is None:
            raise ValueError("Use a server invite; friend and group DM invites are not accepted here.")
        if expected is not None and invite.guild.id != expected:
            raise ValueError("This invite changed. Preview it again before joining.")
        if getattr(getattr(invite, "flags", None), "guest", False):
            raise ValueError("Use a regular server invite. Guest voice invites must be opened in Discord.")

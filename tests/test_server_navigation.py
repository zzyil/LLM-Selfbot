"""Invite joins and native channel navigation using fake Discord objects, never live messages."""
import ast
import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SANDBOX = tempfile.TemporaryDirectory(prefix="server-navigation-")
os.environ["LLMSELFBOT_DATA_DIR"] = SANDBOX.name
(Path(SANDBOX.name) / "config").mkdir()
(Path(SANDBOX.name) / "config/config.yaml").write_bytes((ROOT / "resources/config.yaml").read_bytes())

import discord
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils.discord_servers import ServerAccess, invite_code
from app.web import live
from app.web.routes import chat_routes

N = types.SimpleNamespace
GUILD = 1234567890123456789
CHANNEL = 987654321098765432


def fixtures():
    guild = N(id=GUILD, name="Test community", icon=None, member_count=42, me=N(pending=False), channels=[], threads=[])
    permissions = N(view_channel=True, read_message_history=True, send_messages=True,
                    send_messages_in_threads=True, manage_threads=False)
    channel = N(id=CHANNEL, name="general", type=discord.ChannelType.text, guild=guild, category=None,
                topic="Server chat", position=0, slowmode_delay=0, permissions_for=lambda me: permissions)
    guild.channels = [channel]
    invite = N(type=discord.InviteType.guild, guild=guild, flags=N(guest=False))
    client = N(guilds=[guild], get_guild=lambda gid: guild if gid == GUILD else None,
               get_channel=lambda cid: channel if cid == CHANNEL else None,
               fetch_invite=AsyncMock(return_value=invite), accept_invite=AsyncMock(return_value=invite))
    return client, guild, channel, permissions, invite


class InviteAndAccess(unittest.IsolatedAsyncioTestCase):
    def test_invite_parser_accepts_codes_and_discord_links_only(self):
        for value in ("abc_123", "https://discord.gg/abc_123", "https://discord.com/invite/abc_123?event=42"):
            self.assertEqual(invite_code(value), "abc_123")
        for value in (None, "https://evil.test/abc", "https://discord.gg.evil.test/abc", "https://user@discord.gg/abc",
                      "https://discord.gg:444/abc", "file:///abc", "../abc", "https://discord.com/channels/123"):
            with self.assertRaises(ValueError):
                invite_code(value)

    async def test_preview_does_not_join_and_rejects_group_or_friend_invites(self):
        client, _, _, _, invite = fixtures()
        access = ServerAccess(client)
        result = await access.handle("servers_invite", {"invite": "abc"})
        self.assertEqual(result["guild"]["id"], str(GUILD))
        client.accept_invite.assert_not_awaited()
        invite.type = discord.InviteType.group_dm
        self.assertFalse((await access.handle("servers_invite", {"invite": "abc"}))["ok"])
        client.accept_invite.assert_not_awaited()

    async def test_join_checks_previewed_guild_and_already_joined_membership(self):
        client, _, _, _, _ = fixtures()
        access = ServerAccess(client)
        wrong = await access.handle("servers_join", {"invite": "abc", "guild_id": "42"})
        self.assertFalse(wrong["ok"])
        result = await access.handle("servers_join", {"invite": "abc", "guild_id": str(GUILD)})
        self.assertTrue(result["already_joined"])
        client.accept_invite.assert_not_awaited()

    async def test_join_is_serialized_and_duplicate_clicks_do_not_join_twice(self):
        client, _, _, _, _ = fixtures()
        client.get_guild = lambda gid: None
        access = ServerAccess(client)
        payload = {"invite": "abc", "guild_id": str(GUILD)}
        first, second = await asyncio.gather(access.handle("servers_join", payload), access.handle("servers_join", payload))
        self.assertTrue(first["ok"] and second["ok"])
        client.accept_invite.assert_awaited_once()
        self.assertTrue(second["already_joined"])

    async def test_captcha_is_reported_for_manual_completion_without_leaking_challenge_data(self):
        client, _, _, _, _ = fixtures()
        client.get_guild = lambda gid: None
        class FakeCaptchaRequired(Exception):
            pass
        client.accept_invite.side_effect = FakeCaptchaRequired("secret challenge payload")
        data = await ServerAccess(client).handle("servers_join", {"invite": "abc", "guild_id": str(GUILD)})
        self.assertFalse(data["ok"])
        self.assertTrue(data["needs_discord"])
        self.assertNotIn("secret challenge", str(data))

    async def test_channels_respect_visibility_read_only_state_and_thread_membership(self):
        client, guild, channel, perms, _ = fixtures()
        access = ServerAccess(client)
        perms.send_messages = False
        row = access.channels(GUILD)["channels"][0]
        self.assertFalse(row["can_send"])
        self.assertTrue(row["can_read"])
        perms.view_channel = False
        self.assertEqual(access.channels(GUILD)["channels"], [])
        perms.view_channel = True
        guild.me.pending = True
        self.assertIn("verification", access.channels(GUILD)["channels"][0]["reason"])
        channel.type = discord.ChannelType.private_thread
        channel.me = None
        self.assertEqual(access.channels(GUILD)["channels"], [])

    async def test_watch_is_bounded_and_expires(self):
        client, _, _, _, _ = fixtures()
        now = [100]
        access = ServerAccess(client, clock=lambda: now[0])
        self.assertTrue((await access.handle("servers_watch", {"channel_id": str(CHANNEL)}))["ok"])
        self.assertTrue(access.watches(CHANNEL))
        now[0] += 91
        self.assertFalse(access.watches(CHANNEL))
        self.assertFalse((await access.handle("servers_watch", {"channel_id": "42"}))["ok"])


class OwnerRoutes(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(chat_routes.router)
        self.client = TestClient(app)
        targets = {"discord_1": {"platform": "discord", "target": 1}, "snapchat_1": {"platform": "snapchat", "target": "snap"}}
        self.patches = [patch.object(chat_routes.ipc, "discover_targets", return_value=targets),
                        patch.object(live, "running_accounts", return_value={"discord_1"}),
                        patch.object(chat_routes.ipc, "send_and_wait", AsyncMock(return_value={"ok": True, "servers": []}))]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.runner = self.mocks[-1]

    def test_explicit_account_and_string_ids_reach_the_runner(self):
        self.client.get(f"/api/servers/{GUILD}/channels?target=discord_1")
        self.runner.assert_awaited_once_with(1, "servers_channels", {"guild_id": str(GUILD)}, timeout=15)

    def test_join_requires_a_previewed_server_id_and_valid_invite(self):
        response = self.client.post("/api/servers/join", json={"target": "discord_1", "invite": "abc"})
        self.assertEqual(response.status_code, 400)
        self.runner.assert_not_awaited()
        response = self.client.post("/api/servers/join", json={"target": "discord_1", "invite": "abc", "guild_id": str(GUILD)})
        self.assertEqual(response.status_code, 200)
        self.runner.assert_awaited_once_with(1, "servers_join", {"invite": "abc", "guild_id": str(GUILD)}, timeout=40)

    def test_stopped_snapchat_and_unknown_accounts_cannot_join(self):
        for target in ("snapchat_1", "discord_99"):
            self.assertEqual(self.client.get(f"/api/servers?target={target}").status_code, 400)
        with patch.object(live, "running_accounts", return_value=set()):
            self.assertEqual(self.client.get("/api/servers?target=discord_1").status_code, 503)
        self.runner.assert_not_awaited()

    def test_server_history_window_skips_dm_storage_and_preserves_ids(self):
        self.runner.return_value = {"ok": True, "messages": [], "more": False}
        result = self.client.post("/api/chats/around", json={"target": "discord_1", "user_id": f"ch{CHANNEL}",
                                                          "mid": "1234567890123456790", "dir": "older"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.runner.await_args.args[2]["user_id"], f"ch{CHANNEL}")
        self.assertEqual(self.runner.await_args.args[1], "chat_window")


class ServerChatBridge(unittest.IsolatedAsyncioTestCase):
    def recovery_scope(self, *, own=False, ignored=False, budget=True):
        tree = ast.parse((ROOT / 'app/platforms/discord_runner.py').read_bytes())
        functions = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                     and n.name in {'_server_job', '_latest_server_reply_target'}]
        channel = N(id=CHANNEL)
        target = N(id=55, content='a current reaction', author=N(id=7 if own else 9),
                   channel=channel, guild=N(id=GUILD))
        reads = []

        async def history(**kwargs):
            reads.append(kwargs)
            yield target

        channel.history = history
        bot = N(selfbot_id=7, user=N(id=7), server_waiting={}, get_channel=lambda cid: channel,
                message_history={'server': []})
        results = []
        writer = AsyncMock(return_value='delivered')
        scope = {'bot': bot, '_server_chat': None,
                 '_get_server_access': lambda: N(channel=lambda cid: (channel, None),
                                                activity=N(can_send=lambda ch: True)),
                 '_manual_history_budget': N(allow=lambda: budget), 'should_ignore_message': lambda m: ignored,
                 'companion_memory': N(message_allowed=lambda m: True),
                 '_write_result': lambda cmd, data: results.append(data), '_answered_since': AsyncMock(return_value=False),
                 'seed_history': AsyncMock(), 'history_key': lambda m: 'server', 'as_turn': lambda m, text: text,
                 'set_reply_state': lambda *a, **k: None, 'clear_reply_state': lambda *a, **k: None,
                 'generate_response_and_reply': writer}
        exec(compile(ast.Module(functions, type_ignores=[]), 'server-recovery', 'exec'), scope)  # noqa: S102
        return scope, writer, results, reads

    async def test_owner_reply_recovers_the_latest_message_when_the_live_waiting_row_was_cleared(self):
        scope, writer, results, reads = self.recovery_scope()
        await scope['_server_job']('reply_user', 'request', CHANNEL, {})
        writer.assert_awaited_once()
        self.assertEqual(results[-1], {'success': True})
        self.assertEqual(reads, [{'limit': 1}])

    async def test_recovery_does_not_repeat_an_answer_or_answer_an_ignored_message(self):
        for options in ({'own': True}, {'ignored': True}):
            scope, writer, results, _ = self.recovery_scope(**options)
            await scope['_server_job']('reply_user', 'request', CHANNEL, {})
            writer.assert_not_awaited()
            self.assertFalse(results[-1]['success'])

    async def test_recovery_obeys_the_manual_history_budget(self):
        scope, writer, results, reads = self.recovery_scope(budget=False)
        await scope['_server_job']('reply_user', 'request', CHANNEL, {})
        writer.assert_not_awaited()
        self.assertEqual(reads, [])
        self.assertIn('limit', results[-1]['reason'])

    async def test_owner_send_supports_files_and_suppresses_mentions(self):
        """Execute the real runner branch without importing or logging into a Discord client."""
        tree = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())
        job = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_server_job")
        client, _guild, channel, perms, _ = fixtures()
        sent = N(id=123, created_at=N(timestamp=lambda: 50))
        channel.send = AsyncMock(return_value=sent)
        bot = N(selfbot_id=7, user=N(id=7), server_waiting={}, get_channel=client.get_channel,
                global_send_lock=asyncio.Lock(), message_history={})
        results = []
        scope = {"bot": bot, "discord": discord, "time": __import__("time"), "_server_chat": None,
                 "_get_server_access": lambda: ServerAccess(client), "_write_result": lambda cid, data: results.append(data),
                 "_decode_files": lambda raw: ([("sample.txt", b"sample")], ""), "_as_discord_files": lambda files: ["decoded-file"],
                 "emit_chat_event": lambda event: None, "_message_payload": lambda message: {"text": "", "attachments": []}}
        exec(compile(ast.Module(body=[job], type_ignores=[]), "server_job", "exec"), scope)  # noqa: S102 - execute our own AST
        await scope["_server_job"]("send_message", "request", CHANNEL, {"text": "", "files": [{"name": "sample.txt"}]})
        self.assertTrue(results[-1]["ok"])
        self.assertEqual(channel.send.await_args.kwargs["files"], ["decoded-file"])
        self.assertFalse(channel.send.await_args.kwargs["allowed_mentions"].everyone)
        perms.send_messages = False
        await scope["_server_job"]("send_message", "request", CHANNEL, {"text": "blocked"})
        self.assertFalse(results[-1]["ok"])
        self.assertEqual(channel.send.await_count, 1)


if __name__ == "__main__":
    unittest.main()

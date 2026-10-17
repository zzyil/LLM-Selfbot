"""Contact inboxes and channel conversations remain separate through sweeps and live events."""
import ast
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SANDBOX = tempfile.TemporaryDirectory(prefix="channel-categories-")
os.environ["LLMSELFBOT_DATA_DIR"] = SANDBOX.name
(Path(SANDBOX.name) / "config").mkdir()
(Path(SANDBOX.name) / "config/config.yaml").write_bytes((ROOT / "resources/config.yaml").read_bytes())

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils import chatlog, db
from app.utils.channel_conversations import separate
from app.web import live
from app.web.routes import chat_routes as routes


def channel(speaker="Member", author="42", mid="1", cid="555"):
    return {"id": f"ch{cid}", "kind": "server", "channel_id": cid, "guild_id": "999", "author_id": author,
            "name": speaker, "avatar": "member-avatar", "where": "#general · Community", "mid": mid, "snippet": "hello"}


class CategoryBoundaries(unittest.TestCase):
    def setUp(self):
        routes._CHATS_CACHE.clear()
        routes._CHAT_EVENTS.clear()
        routes._CHATS_LOCKS.clear()
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.patches = [patch.object(routes.ipc, "discover_targets", return_value={"discord_1": {"platform": "discord", "target": 1}}),
                        patch.object(live, "running_accounts", return_value={"discord_1"}),
                        patch.object(routes, "_profiles_for", return_value={})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_api_has_distinct_contact_and_channel_categories_even_for_an_old_runner(self):
        for result in ({"users": [{"id": "42", "name": "DM contact"}, channel()]},
                       {"users": [{"id": "42", "name": "DM contact"}], "channels": [channel()]}):
            routes._CHATS_CACHE.clear()
            runner = AsyncMock(return_value=result)
            with patch.object(routes.ipc, "send_and_wait", runner):
                contacts = self.client.get("/api/chats?target=discord_1").json()
                channels = self.client.get("/api/servers/pending?target=discord_1").json()
            self.assertEqual([row["id"] for row in contacts["users"]], ["42"])
            self.assertNotIn("channels", contacts)
            self.assertNotIn("users", channels)
            self.assertEqual(channels["channels"][0]["name"], "#general")
            runner.assert_awaited_once()  # one sweep shared by the two projections

    def test_changing_speakers_keeps_one_channel_and_does_not_create_contacts(self):
        body = routes.apply_event({"users": []}, {"t": "msg", "uid": "ch555", **channel()})
        body = routes.apply_event(body, {"t": "msg", "uid": "ch555", **channel("Another member", "43", "2")})
        self.assertEqual(body["users"], [])
        self.assertEqual(len(body["channels"]), 1)
        row = body["channels"][0]
        self.assertEqual((row["id"], row["name"], row["avatar"], row["count"]), ("ch555", "#general", "", 2))
        self.assertEqual(row["latest_author"]["name"], "Another member")

    def test_contact_and_two_channels_with_the_same_member_have_independent_states(self):
        body = {"users": [{"id": "42", "name": "Member", "state": {"state": "waiting"}}],
                "channels": [channel(cid="555"), channel(cid="556")]}
        body = routes.apply_event(body, {"t": "state", "uid": "ch555", "state": {"state": "writing"}})
        self.assertEqual(body["users"][0]["state"]["state"], "waiting")
        self.assertNotIn("state", body["channels"][1])
        body = routes.apply_event(body, {"t": "done", "uid": "ch555"})
        self.assertEqual([row["id"] for row in body["channels"]], ["ch556"])
        self.assertEqual(body["users"][0]["id"], "42")

    def test_legacy_rows_migrate_by_channel_id_and_deduplicate(self):
        people, channels = separate([{"id": "42"}, {"id": "ch555", "where": "#general · Community"}], [channel()])
        self.assertEqual(people, [{"id": "42"}])
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["channel_name"], "general")

    def test_server_only_members_do_not_appear_in_the_dm_archive(self):
        db.init_db()
        db.record_user_message(42, "Server only", account=1)
        db.record_user_message(43, "DM contact", account=1)
        chatlog.put(1, "43", "12345", False, 10, {"text": "a real DM"})
        chatlog.put(2, "44", "12346", False, 11, {"text": "another account's DM"})
        data = self.client.get("/api/chats/archive?target=discord_1").json()
        self.assertEqual([row["id"] for row in data["conversations"]], ["43"])

    def test_runner_progress_and_completion_are_scoped_to_the_channel(self):
        tree = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"set_reply_state", "clear_reply_state"}]
        events = []
        bot = types.SimpleNamespace(reply_state={42: {"state": "waiting"}}, waiting_msgs={42: object()},
                                    server_waiting={555: object(), 556: object()})
        scope = {"bot": bot, "time": __import__("time"), "emit_chat_event": events.append}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "reply_states", "exec"), scope)  # noqa: S102 - our own source
        scope["set_reply_state"](42, "writing", channel_id=555)
        scope["set_reply_state"](42, "sending", channel_id=556)
        scope["clear_reply_state"](42, channel_id=555)
        self.assertEqual(bot.reply_state[42]["state"], "waiting")
        self.assertEqual(set(bot.channel_reply_state), {556})
        self.assertEqual(bot.channel_reply_state[556]["state"], "sending")
        self.assertIn(42, bot.waiting_msgs)
        self.assertIn(556, bot.server_waiting)
        self.assertEqual([event["uid"] for event in events], ["ch555", "ch556", "ch555"])

    def test_first_scheduled_reply_in_a_channel_arrives_with_its_live_status(self):
        tree = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"set_reply_state", "_server_reply_scheduled"}]
        body = {"users": [], "channels": []}

        def emit(event):
            nonlocal body
            body = routes.apply_event(body, event)

        bot = types.SimpleNamespace(reply_state={}, server_waiting={})
        scope = {"bot": bot, "time": __import__("time"), "emit_chat_event": emit,
                 "server_row": lambda message: channel(cid=str(message.channel.id))}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "schedule", "exec"), scope)  # noqa: S102 - our own source
        message = types.SimpleNamespace(author=types.SimpleNamespace(id=42), channel=types.SimpleNamespace(id=555), guild=object())
        scope["_server_reply_scheduled"](message, 5)
        self.assertEqual(body["users"], [])
        self.assertEqual(body["channels"][0]["state"]["state"], "waiting")
        self.assertGreater(body["channels"][0]["state"]["eta"], scope["time"].time())

    def test_legacy_server_history_cannot_become_a_dm_contact(self):
        tree = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())
        loop = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_tg_ipc_loop")
        branch = next(node for node in ast.walk(loop) if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "cmd == 'reply_check'")
        history_pass = next(node for node in branch.body if isinstance(node, ast.For))
        dm_type = type("DMChannel", (), {})
        dm = dm_type()
        guild_channel = types.SimpleNamespace(guild=object())
        bot = types.SimpleNamespace(message_history={"42-100": [{"role": "user", "content": "server text"}],
                                                     "43-200": [{"role": "user", "content": "DM text"}]},
                                    get_channel=lambda cid: dm if cid == 200 else guild_channel,
                                    get_user=lambda uid: types.SimpleNamespace(name=f"Person {uid}"),
                                    user_message_batches={}, waiting_msgs={})
        scope = {"bot": bot, "discord": types.SimpleNamespace(DMChannel=dm_type), "_seen_users": {},
                 "_blocked_users": set(), "users_out": [], "seen_ids": set()}
        exec(compile(ast.Module(body=[history_pass], type_ignores=[]), "history_pass", "exec"), scope)  # noqa: S102 - our own source
        self.assertEqual([row["id"] for row in scope["users_out"]], [43])


if __name__ == "__main__":
    unittest.main()

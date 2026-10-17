"""Dashboard discovery and vocabulary management, using isolated memory and fake runners."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SANDBOX = tempfile.TemporaryDirectory(prefix="server-memory-dashboard-")
os.environ["LLMSELFBOT_DATA_DIR"] = SANDBOX.name
(Path(SANDBOX.name) / "config").mkdir()
(Path(SANDBOX.name) / "config/config.yaml").write_bytes((ROOT / "resources/config.yaml").read_bytes())

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils import configstore, db, lingo, personas, server_memory
from app.web import live
from app.web.routes import chat_routes


class ServerMemoryDashboard(unittest.TestCase):
    def setUp(self):
        with db._connect() as conn:
            lingo._schema(conn)
            conn.execute("DELETE FROM server_lingo")
            conn.execute("DELETE FROM server_lingo_evidence")
        app = FastAPI()
        app.include_router(chat_routes.router)
        self.client = TestClient(app)

    def test_summary_counts_scoped_terms_and_disputes_without_leaking_other_personas(self):
        lingo.set_definition("1234567890123456789", "floop", "a silly mistake")
        lingo.set_definition("1234567890123456789", "floop", "a late night snack", "88")
        lingo.set_definition("999", "secret", "a private joke", persona="another")
        with db._connect() as conn:
            conn.execute("INSERT INTO server_lingo VALUES(?,?,?,?,?,?,0,?)",
                         ("default", "1234567890123456789", "0", "floop", "other", "a different meaning", 5))
        summary = server_memory.remembered()
        self.assertEqual(list(summary), ["1234567890123456789"])
        row = summary["1234567890123456789"]
        self.assertEqual((row["terms"], row["private_terms"], row["disputed"]), (2, 1, 1))
        self.assertNotIn("secret", str(summary))

    def test_offline_saved_servers_are_discoverable(self):
        lingo.set_definition("1234567890123456789", "floop", "a silly mistake")
        response = self.client.get("/api/server-memories")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["live_accounts"], 0)
        self.assertEqual(data["servers"][0]["guild_id"], "1234567890123456789")
        self.assertEqual(data["servers"][0]["accounts"], [])

    def test_live_discovery_is_filtered_to_discord_accounts_of_this_persona(self):
        data = {"servers": [{"guild_id": "1234567890123456789", "name": "Community", "channels": []}]}
        runner = AsyncMock(return_value=data)
        with patch.object(live, "running_accounts", return_value={"discord_1", "discord_2", "snapchat_1"}), \
                patch.object(personas, "persona_of", side_effect=lambda account: "other" if account == "discord_2" else "default"), \
                patch.object(chat_routes.ipc, "send_and_wait", runner):
            body = self.client.get("/api/server-memories").json()
        runner.assert_awaited_once_with(1, "server_memory", timeout=3)
        self.assertEqual(body["servers"][0]["name"], "Community")
        self.assertEqual(body["live_accounts"], 1)

    def test_unavailable_runner_keeps_persistent_memory(self):
        lingo.set_definition("42", "floop", "a silly mistake")
        for result in (None, {"ok": False}):
            with patch.object(live, "running_accounts", return_value={"discord_1"}), \
                    patch.object(personas, "persona_of", return_value="default"), \
                    patch.object(chat_routes.ipc, "send_and_wait", AsyncMock(return_value=result)):
                data = self.client.get("/api/server-memories").json()
            self.assertEqual(data["unavailable_accounts"], 1)
            self.assertEqual(data["servers"][0]["terms"], 1)

    def test_settings_are_effective_for_the_selected_persona(self):
        config = {"bot": {"server_lingo": {"enabled": True, "share_with_mutuals": False},
                          "companion": {"server": {"enabled": True, "ambient_reply_probability": 0.35}}}}
        with patch.object(configstore, "effective", return_value=config) as effective:
            data = self.client.get("/api/server-memories?persona=default").json()
        effective.assert_called_once_with("default")
        self.assertEqual(data["settings"]["learning"], True)
        self.assertEqual(data["settings"]["share_with_mutuals"], False)
        self.assertEqual(data["settings"]["participation"], True)
        self.assertEqual(self.client.get("/api/server-memories?persona=missing").status_code, 404)

    def test_same_guild_across_accounts_does_not_duplicate_terms(self):
        lingo.set_definition("42", "floop", "a silly mistake")
        live_data = {"servers": [{"guild_id": "42", "name": "Community", "channels": []}]}
        result = server_memory.merge(server_memory.remembered(), [("discord_1", live_data), ("discord_2", live_data)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["terms"], 1)
        self.assertEqual(len(result[0]["accounts"]), 2)

    def test_correct_and_forget_preserve_private_scope_and_persona(self):
        for persona in ("default", "another"):
            lingo.set_definition("42", "floop", "a silly mistake", persona=persona)
            lingo.set_definition("42", "floop", "a late night snack", "88", persona=persona)
        response = self.client.put("/api/lingo/42?persona=default", json={
            "term": "floop", "meaning": "a private nickname", "scope_channel": "88"})
        self.assertEqual(response.status_code, 200)
        entries = self.client.get("/api/lingo?guild_id=42&persona=default").json()["entries"]
        scoped = {entry["scope_channel"]: entry["senses"][0]["meaning"] for entry in entries}
        self.assertEqual(scoped, {"0": "a silly mistake", "88": "a private nickname"})
        self.client.delete("/api/lingo/42/floop?channel_id=88&persona=default")
        self.assertEqual(len(lingo.glossary("42")), 1)
        self.assertEqual(len(lingo.glossary("42", persona="another")), 2)

    def test_snapshot_uses_cached_activity_and_never_includes_message_text(self):
        guild = types.SimpleNamespace(id=1234567890123456789, name="Community")
        message = types.SimpleNamespace(guild=guild, channel=types.SimpleNamespace(name="general"), content="private text")
        state = types.SimpleNamespace(first_message=message, observed=[(10, 1, 7), (35, 2, 8), (40, 3, 7)],
                                      current=None, pending=None, sending=False, messages=[message])
        scheduler = types.SimpleNamespace(clock=lambda: 50, get_config=lambda: {"ambient_window_seconds": 30},
                                          channels={987654321098765432: state})
        bot = types.SimpleNamespace(guilds=[guild], paused=True, allow_server=False)
        data = server_memory.snapshot(bot, scheduler)
        channel = data["servers"][0]["channels"][0]
        self.assertEqual((channel["messages"], channel["members"]), (2, 2))
        self.assertEqual(channel["channel_id"], "987654321098765432")
        self.assertEqual(channel["state"], "reading")
        self.assertNotIn("private text", str(data))
        state.current = message
        state.sending = True
        self.assertEqual(server_memory.snapshot(bot, scheduler)["servers"][0]["channels"][0]["state"], "sending")


if __name__ == "__main__":
    unittest.main()

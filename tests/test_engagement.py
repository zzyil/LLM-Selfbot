"""Active conversations retain availability without bypassing owner/spam controls."""
import ast
import os
import random
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SANDBOX = tempfile.TemporaryDirectory(prefix="agent-g-active-chat-")
os.environ["LLMSELFBOT_DATA_DIR"] = SANDBOX.name
(Path(SANDBOX.name) / "config").mkdir()
(Path(SANDBOX.name) / "config/config.yaml").write_bytes((ROOT / "resources/config.yaml").read_bytes())

from app.utils import conversation, db, engagement

db.init_db()


def message(uid=10, channel=100):
    return types.SimpleNamespace(author=types.SimpleNamespace(id=uid), channel=types.SimpleNamespace(id=channel))


class Availability(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = {"bot": {"active_chat": {"enabled": True, "minutes": 120,
                                               "max_batch_wait_seconds": 8}}}
        self.patch = patch.object(conversation, "_config", return_value=self.config)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.now = 100000
        self.active = engagement.ActiveChat("discord:1", clock=lambda: self.now)
        with db._connect() as conn:
            conn.execute("DELETE FROM conversation_activity")
        self.active.recent.clear()
        self.active.saved.clear()

    def test_two_hour_window_refreshes_during_conversation(self):
        m = message()
        self.assertFalse(self.active.active(m))
        self.active.touch(m)
        self.now += 7199
        self.assertTrue(self.active.active(m))
        self.active.touch(m)
        self.now += 7199
        self.assertTrue(self.active.active(m))
        self.now += 1
        self.assertFalse(self.active.active(m))

    def test_active_chat_caps_long_waits_and_preserves_short_ones(self):
        m = message()
        self.assertEqual(self.active.wait(1600, m), 1600)
        self.active.touch(m)
        self.assertEqual(self.active.wait(1600, m), 8)
        self.assertEqual(self.active.wait(3, m), 3)
        self.assertEqual(self.active.wait(90, message(uid=11)), 90)
        self.assertEqual(self.active.wait(90, message(channel=200)), 90)

    def test_window_survives_restart_without_leaking_between_accounts_or_personas(self):
        self.active.touch(message())
        self.now += 60
        same = engagement.ActiveChat("discord:1", clock=lambda: self.now)
        self.assertTrue(same.active(message()))
        self.assertFalse(engagement.ActiveChat("discord:2", clock=lambda: self.now).active())
        self.assertFalse(engagement.ActiveChat("discord:1", persona="other", clock=lambda: self.now).active())

    def test_continuous_traffic_still_refreshes_persistence(self):
        for _ in range(900):
            self.active.touch(message())
            self.now += 10
        restarted = engagement.ActiveChat("discord:1", clock=lambda: self.now)
        self.assertLess(self.now-max(restarted.recent.values()), 70)

    def test_disabled_mode_retains_existing_timing(self):
        self.active.touch(message())
        self.config["bot"]["active_chat"]["enabled"] = False
        self.assertFalse(self.active.active())
        self.assertEqual(self.active.wait(1600, message()), 1600)
        self.assertEqual(self.active.context_block(message()), "")

    def test_guidance_prevents_fabricated_breaks_and_respects_goodbyes(self):
        self.active.touch(message())
        block = self.active.context_block(message())
        self.assertIn("Do not invent being AFK", block)
        self.assertIn("says goodbye or asks for space", block)

    def test_runner_caps_real_batch_table_and_disables_random_skip_only_while_active(self):
        source = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())
        wait = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "get_batch_wait_time")
        bot = types.SimpleNamespace(active_chat=self.active)
        ns = {"bot": bot, "config": {"bot": {"batch_wait_times": [{"time": 1600}]}},
              "pick_wait": lambda rows: rows[0]["time"]}
        exec(compile(ast.Module(body=[wait], type_ignores=[]), "batch_wait", "exec"), ns)  # noqa: S102
        m = message()
        m.content = "what about that?"
        self.active.touch(m)
        self.assertEqual(ns["get_batch_wait_time"](message=m), 8)
        handler = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_message")
        skip = next(n for n in ast.walk(handler) if isinstance(n, ast.If)
                    and any(isinstance(c, ast.Name) and c.id == "IGNORE_CHANCE" for c in ast.walk(n.test)))
        condition = compile(ast.Expression(body=skip.test), "random_skip", "eval")
        ns.update({"random": types.SimpleNamespace(random=lambda: 0), "IGNORE_CHANCE": 1,
                   "message": m, "content_has_trigger": False, "mentioned": False,
                   "PREFIX": "!", "PRIORITY_PREFIX": "="})
        self.assertFalse(eval(condition, ns))
        self.now += 7201
        self.assertEqual(ns["get_batch_wait_time"](message=m), 1600)
        self.assertTrue(eval(condition, ns))

    async def test_presence_wakes_from_night_and_stays_online_while_active(self):
        source = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())
        node = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "random_status_loop")
        self.active.touch(message())
        calls = []
        async def sleep(_):
            calls.append(1)
            if len(calls) == 1:
                self.now += 7199
            else:
                self.now += 2
        fakebot = types.SimpleNamespace(wait_until_ready=AsyncMock(), paused=False, active_chat=self.active,
            shown_status="invisible", is_closed=lambda: len(calls) >= 3, change_presence=AsyncMock())
        ns = {"bot": fakebot, "config": {"bot": {"status": {"enabled": True}, "night_invisible": {}}},
              "discord": types.SimpleNamespace(Status=types.SimpleNamespace(online="online", idle="idle",
                                                       dnd="dnd", invisible="invisible")),
              "time": types.SimpleNamespace(monotonic=lambda: self.now), "random": random,
              "night_window_state": lambda cfg: (True, 8000), "asyncio": types.SimpleNamespace(sleep=sleep),
              "_pick_status": lambda *args: "idle", "log_system": lambda *args: None,
              "log_error": lambda *args: None}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "presence", "exec"), ns)  # noqa: S102
        await ns["random_status_loop"]()
        self.assertEqual([call.kwargs["status"] for call in fakebot.change_presence.await_args_list],
                         ["online", "invisible"])

    def test_retention_and_persona_deletion(self):
        for uid in range(270):
            self.now += 1
            self.active.touch(message(uid=uid))
        self.assertEqual(len(self.active.recent), 256)
        with db._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM conversation_activity").fetchone()[0], 256)
        db.delete_persona_rows(self.active.persona)
        self.assertFalse(engagement.ActiveChat("discord:1", clock=lambda: self.now).active())


if __name__ == "__main__":
    unittest.main()

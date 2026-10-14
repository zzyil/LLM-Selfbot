"""Companion behavior and routing checks: temporary DBs, fake models, no real accounts."""
import ast
import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SANDBOX = tempfile.TemporaryDirectory(prefix="agent-g-test-")
os.environ["LLMSELFBOT_DATA_DIR"] = SANDBOX.name
(Path(SANDBOX.name) / "config").mkdir()
(Path(SANDBOX.name) / "config/config.yaml").write_bytes((ROOT / "resources/config.yaml").read_bytes())

import yaml

from app.utils import ai, db, memory, providers
from app.utils import relationships as social
from scripts.setup_agent_g import build_config

BASE = yaml.safe_load((ROOT / "resources/config.yaml").read_text(encoding="utf-8"))
LOCAL = build_config(BASE, "chat-model", 123, [456])
db.init_db()


class Relationships(unittest.TestCase):
    def setUp(self):
        with db._connect() as conn:
            social._schema(conn)
            conn.execute("DELETE FROM companion_relationships")
            conn.execute("DELETE FROM companion_relationship_events")

    def test_friendship_persists_and_duplicate_events_do_not_accumulate(self):
        for message in range(8):
            self.assertTrue(social.record_interaction("1", "server:a", message, "friendly", now=100))
        self.assertFalse(social.record_interaction("1", "server:a", 0, "hostile", now=100))
        profile = social.get_relationship("1", "server:a", now=100)
        self.assertEqual(profile["relationship"], "friend")
        self.assertEqual(profile["interactions"], 8)
        self.assertEqual(profile["affinity"], 0.48)

    def test_relationships_are_isolated_by_server_and_persona(self):
        social.record_interaction("1", "server:a", "m", "friendly", persona="default", now=100)
        self.assertEqual(social.get_relationship("1", "server:b", now=100)["relationship"], "new")
        self.assertEqual(social.get_relationship("1", "server:a", persona="other", now=100)["relationship"], "new")
        self.assertEqual(len(social.list_relationships("1", now=100)), 1)

    def test_bounds_foes_and_decay_on_return(self):
        for message in range(30):
            social.record_interaction("1", "server:a", message, "hostile", now=100)
        self.assertEqual(social.get_relationship("1", "server:a", now=100)["affinity"], -1)
        self.assertEqual(social.get_relationship("1", "server:a", now=100)["relationship"], "foe")
        later = 100 + 60*86400
        self.assertEqual(social.get_relationship("1", "server:a", now=later)["affinity"], -0.5)
        social.record_interaction("1", "server:a", "back", "neutral", now=later)
        self.assertEqual(social.get_relationship("1", "server:a", now=later)["affinity"], -0.5)

    def test_close_friend_requires_familiarity(self):
        self.assertEqual(social.label(0.9, 10), "friend")
        self.assertEqual(social.label(0.9, 20), "close_friend")
        self.assertEqual(social.label(-0.9, 2), "acquaintance")

    def test_invalid_or_invented_evidence_is_neutral(self):
        content = "thanks for helping me with this"
        self.assertEqual(social.validate_analysis({"stance": "friendly", "quote": "thanks for helping"}, content),
                         ("friendly", "thanks for helping"))
        for raw in ([], None, {"stance": "enemy"}, {"stance": "friendly", "quote": "fabricated"},
                    {"stance": "hostile", "quote": True}):
            self.assertEqual(social.validate_analysis(raw, content), ("neutral", ""))
        with self.assertRaises(ValueError):
            social.record_interaction("1", "s", "m", "enemy")

    def test_clear_memory_clears_relationships(self):
        memory.set_memory(1, "interest", "games")
        social.record_interaction("1", "s", "m", "friendly", now=100)
        memory.clear_memory(1)
        self.assertEqual(memory.get_memory(1), {})
        self.assertEqual(social.list_relationships("1"), [])

    def test_cooldown_and_scope_check(self):
        social.record_interaction("1", "s", "m", "neutral", now=100)
        self.assertFalse(social.due("1", "s", "m", now=300))
        self.assertFalse(social.due("1", "s", "new", now=150))
        self.assertTrue(social.due("1", "s", "new", now=220))
        self.assertTrue(social.due("1", "other", "new", now=150))

    def test_allowlist_fails_closed_and_supports_snowflakes(self):
        self.assertTrue(social.channel_allowed(456, LOCAL))
        self.assertFalse(social.channel_allowed(789, LOCAL))
        config = copy.deepcopy(LOCAL)
        config["bot"]["companion"]["allowed_channel_ids"] = []
        self.assertFalse(social.channel_allowed(456, config))
        config["bot"]["companion"]["allowed_channel_ids"] = "456"
        self.assertFalse(social.channel_allowed(456, config))
        self.assertTrue(social.channel_allowed(456, BASE))

    def test_style_has_no_raw_message_text(self):
        style = social.chat_style([{"role": "user", "content": "lol 😅"}])
        self.assertEqual(style["laughter_messages"], 1)
        self.assertEqual(style["emoji_messages"], 1)
        self.assertNotIn("lol", json.dumps(style))
        self.assertEqual(social.chat_style([])["average_chars"], 0)

    def test_background_learning_is_deduplicated(self):
        calls = []

        async def fake(*args, **kwargs):
            calls.append(args)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(
                content='{"stance":"friendly","quote":"thanks for helping"}'))])

        with patch.object(social, "settings", return_value={"enabled": True}), \
                patch.object(ai, "_create_small_completion", side_effect=fake):
            asyncio.run(social.learn_interaction("1", "s", "m", "thanks for helping me"))
            asyncio.run(social.learn_interaction("1", "s", "m", "thanks for helping me"))
        profile = social.get_relationship("1", "s")
        self.assertEqual(profile["affinity"], 0.06)
        self.assertEqual(profile["interactions"], 1)
        self.assertEqual(len(calls), 1)

    def test_inflight_clear_does_not_resurrect_relationship(self):
        async def fake(*args, **kwargs):
            social.clear_relationships("1")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(
                content='{"stance":"friendly","quote":"thanks for helping"}'))])

        with patch.object(social, "settings", return_value={"enabled": True}), \
                patch.object(ai, "_create_small_completion", side_effect=fake):
            asyncio.run(social.learn_interaction("1", "s", "m", "thanks for helping me"))
        self.assertEqual(social.list_relationships("1"), [])

    def test_memory_endpoint_returns_scoped_relationships(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.web.routes import chat_routes
        social.record_interaction("1", "discord:guild:123", "m", "neutral", now=100)
        app = FastAPI()
        app.include_router(chat_routes.router)
        with TestClient(app) as client:
            response = client.get("/api/memory/1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["relationships"][0]["scope"], "discord:guild:123")


class LocalRouting(unittest.TestCase):
    def test_preset_stops_services_and_routes_utility_work_locally(self):
        self.assertFalse(LOCAL["services"]["discord"]["enabled"])
        self.assertFalse(LOCAL["services"]["snapchat"]["enabled"])
        self.assertTrue(LOCAL["services"]["webui"]["enabled"])
        self.assertEqual(providers.reply_chain(LOCAL), [{"provider": "local", "model": "chat-model"}])
        self.assertEqual(providers.job(LOCAL, "small")["provider"], "local")

    def test_explicit_cloud_chains_are_filtered_in_local_only_mode(self):
        config = copy.deepcopy(LOCAL)
        config["bot"]["models"]["replies"].append({"provider": "openai", "model": "cloud"})
        config["bot"]["models"]["small"] = [{"provider": "groq", "model": "cloud"}]
        self.assertTrue(all(e["provider"] == "local" for e in providers.reply_chain(config)))
        for name in providers.JOBS:
            self.assertTrue(all(e["provider"] == "local" for e in providers.job_chain(config, name)))

    def test_unconfigured_local_model_never_chooses_cloud(self):
        config = copy.deepcopy(LOCAL)
        config["bot"]["models"] = {}
        config["bot"]["local"]["model"] = ""
        self.assertEqual(providers.reply_chain(config), [])
        self.assertEqual(providers.job(config, "small"), {"provider": "local", "model": ""})

    def test_failed_local_job_does_not_call_groq_fallback(self):
        calls = []

        async def fail(client, model):
            raise RuntimeError("Local model offline")

        async def cloud(model):
            calls.append(model)
            return "cloud reply"

        with patch.object(ai, "load_config", return_value=LOCAL), \
                patch.object(ai, "_groq_clients", [{"client": object()}]), \
                patch.object(ai, "_openai_client", return_value=object()), \
                patch.object(ai, "_wont_load_now", return_value=None), \
                patch.object(ai, "_left_alone", return_value=None), \
                self.assertRaisesRegex(RuntimeError, "Local model offline"):
            asyncio.run(ai._job_call_inner("small", fail, cloud))
        self.assertEqual(calls, [])

    def test_preset_does_not_mutate_upstream_defaults(self):
        self.assertFalse(BASE["bot"]["local"]["enabled"])
        self.assertTrue(BASE["services"]["discord"]["enabled"])

    def test_setup_seeds_stopped_services_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="agent-g-setup-") as folder:
            command = [sys.executable, str(ROOT / "scripts/setup_agent_g.py"),
                       "--model", "local-test", "--channels", "456"]
            env = {**os.environ, "LLMSELFBOT_DATA_DIR": folder}
            created = subprocess.run(command, env=env, capture_output=True, text=True,
                                     timeout=10, check=False)
            self.assertEqual(created.returncode, 0, created.stderr)
            config_file = Path(folder) / "config/config.yaml"
            initial = config_file.read_text()
            config = yaml.safe_load(initial)
            self.assertFalse(config["services"]["discord"]["enabled"])
            self.assertEqual(config["bot"]["local"]["model"], "local-test")
            again = subprocess.run(command, env=env, capture_output=True, text=True,
                                   timeout=10, check=False)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(config_file.read_text(), initial)

    def test_discord_background_hook_runs_fact_and_relationship_learning(self):
        # Extract the real callback without importing a runner that opens account sessions.
        tree = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())
        callbacks = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"_spawn_memory_update", "_relationship_update_done"}]
        calls = []

        async def facts(author, prompt, response):
            calls.append(("facts", author.id, prompt))

        async def relation(user_id, scope, message_id, content):
            calls.append(("relationship", user_id, scope, message_id, content))

        companion = types.SimpleNamespace(settings=lambda: {"enabled": True},
                                          learn_interaction=relation, discord_scope=social.discord_scope)
        namespace = {"asyncio": asyncio, "companion_memory": companion, "_memory_tasks": set(),
                     "_update_memory_after_reply": facts, "log_error": lambda *args: None}
        exec(compile(ast.Module(body=callbacks, type_ignores=[]), "discord-callbacks", "exec"),  # noqa: S102
             namespace)
        message = types.SimpleNamespace(author=types.SimpleNamespace(id=7), id=8, content="hi",
                                        guild=types.SimpleNamespace(id=9), channel=None)

        async def run_hook():
            namespace["_spawn_memory_update"](message, "batch", "reply")
            await asyncio.gather(*namespace["_memory_tasks"])
            await asyncio.sleep(0)

        asyncio.run(run_hook())
        self.assertIn(("facts", 7, "batch"), calls)
        self.assertIn(("relationship", 7, "discord:guild:9", 8, "hi"), calls)
        self.assertEqual(namespace["_memory_tasks"], set())


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failures = len(result.failures) + len(result.errors)
    print(f"\n{result.testsRun-failures} passed, {failures} failed")
    SANDBOX.cleanup()
    sys.exit(0 if result.wasSuccessful() else 1)

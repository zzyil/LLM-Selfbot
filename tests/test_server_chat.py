"""Exercise shared guild conversations with fake members and transports; no Discord login."""
import ast
import asyncio
import copy
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SANDBOX = tempfile.TemporaryDirectory(prefix="agent-g-server-test-")
os.environ["LLMSELFBOT_DATA_DIR"] = SANDBOX.name
(Path(SANDBOX.name) / "config").mkdir()
(Path(SANDBOX.name) / "config/config.yaml").write_bytes((ROOT / "resources/config.yaml").read_bytes())

import yaml

from app.utils import conversation
from app.utils import relationships as social
from app.utils.server_chat import ServerChat
from scripts.setup_agent_g import build_config


def message(mid, uid=1, text="hey", channel=100, guild=10, name="Sam", parent=None):
    return types.SimpleNamespace(
        id=mid, author=types.SimpleNamespace(id=uid, display_name=name, name=name), content=text,
        channel=types.SimpleNamespace(id=channel, parent_id=parent),
        guild=types.SimpleNamespace(id=guild) if guild is not None else None,
        reference=None, mentions=[], created_at=datetime.fromtimestamp(1000, timezone.utc),
    )


class ServerConversations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = {"names": ["G"], "batch_wait_seconds": 0, "reply_cooldown_seconds": 0,
                       "learning_min_messages": 2, "learn_from_chat": True}
        self.sent, self.learned, self.errors = [], [], []
        self.time = 1000
        self.enabled = True

        async def respond(target, history, directed):
            self.sent.append((target, history, directed))
            return "reply"

        async def learn(author, content):
            self.learned.append((author.id, content))

        async def seed(first):
            return []

        self.chat = ServerChat(
            self_id=lambda: 999, get_config=lambda: self.config, respond=respond,
            learn=learn, seed=seed, can_reply=lambda target: self.enabled,
            report=self.errors.append, clock=lambda: self.time, wall_clock=lambda: 1000,
            rng=lambda: 0.5,
        )

    async def asyncTearDown(self):
        await self.chat.close()
        self.assertEqual(self.errors, [])

    async def settle(self):
        for _ in range(10):
            tasks = list(self.chat.reply_tasks)
            if self.chat.learning_task is not None:
                tasks.append(self.chat.learning_task)
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=False)
            await asyncio.sleep(0)
        self.fail("Background jobs did not drain")

    async def test_unaddressed_members_contribute_context_without_triggering_replies(self):
        self.chat.receive(message(1, text="I play co-op games"))
        self.chat.receive(message(2, uid=2, text="same lol"))
        self.chat.receive(message(3, text="G, any suggestions?"))
        await self.settle()
        self.assertEqual(len(self.sent), 1)
        transcript = "\n".join(t["content"] for t in self.sent[0][1])
        self.assertIn("Sam [user 1]: I play co-op games", transcript)
        self.assertIn("Sam [user 2]: same lol", transcript)
        self.assertEqual(self.sent[0][0].id, 3)

    async def test_active_conversation_continues_past_two_followups_and_short_timeout(self):
        self.chat.is_active = lambda m: m.author.id == 1
        self.chat.receive(message(1, text="G hey"))
        await self.settle()
        for mid in range(2, 7):
            self.time += 90
            self.chat.receive(message(mid, text="and what about that?"))
            await self.settle()
        self.assertEqual(len(self.sent), 6)
        elsewhere = message(7, text="what do you think?")
        elsewhere.reference = types.SimpleNamespace(resolved=message(80, uid=2))
        self.chat.receive(elsewhere)
        self.chat.receive(message(8, uid=2, text="talking to someone else"))
        await self.settle()
        self.assertEqual(len(self.sent), 6)

    async def test_larger_channel_window_survives_edits_and_deletions(self):
        self.config["history_messages"] = 200
        for mid in range(1, 121):
            self.chat.observe(message(mid, text=f"message {mid}"))
        self.assertEqual(len(self.chat.history(100)), 120)
        self.assertIn("message 1", self.chat.history(100)[0]["content"])
        self.chat.edit(100, 60, "edited middle")
        self.chat.delete(100, 61)
        history = self.chat.history(100)
        self.assertEqual(len(history), 119)
        self.assertIn("edited middle", "\n".join(t["content"] for t in history))
        self.assertEqual(self.chat.channels[100].messages.maxlen, 200)

    async def test_real_message_handler_dispatches_server_context_and_mentions(self):
        tree = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())
        callbacks = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name in {"on_message", "is_trigger_message"}]
        for callback in callbacks:
            callback.decorator_list = []
        text_channel = type("TextChannel", (types.SimpleNamespace,), {})
        discord = types.SimpleNamespace(
            DMChannel=type("DMChannel", (), {}), GroupChannel=type("GroupChannel", (), {}),
            TextChannel=text_channel, Thread=type("Thread", (), {}),
            ForumChannel=type("ForumChannel", (), {}), StageChannel=type("StageChannel", (), {}),
            VoiceChannel=type("VoiceChannel", (), {}),
        )
        bot = types.SimpleNamespace(
            selfbot_id=999, user=types.SimpleNamespace(id=999, mentioned_in=lambda m: bool(m.mentions)),
            paused=False, paused_users=set(), allow_dm=False, allow_gc=False, allow_server=True,
            active_conversations={}, hold_conversation=True,
        )
        seen, vocabulary_seen = [], []
        namespace = {
            "conversation": conversation,
            "discord": discord, "bot": bot, "companion_memory": types.SimpleNamespace(
                message_allowed=lambda m: True, settings=lambda: {"enabled": True}, server_enabled=lambda: True),
            "_log_dm": lambda m: None, "_cache_author": lambda a: None,
            "remember_user": lambda a: None, "_blocked_users": set(), "OWNER_ID": 0,
            "should_ignore_message": lambda m: False, "PREFIX": "/", "TRIGGER_RE": None,
            "_raw_reply_cache": {},
            "time": types.SimpleNamespace(time=lambda: 1000), "CONVERSATION_TIMEOUT": 60,
            "in_server": lambda ch: isinstance(ch, text_channel),
            "_server_can_reply": lambda m: True, "_get_server_chat": lambda: self.chat,
            "_server_channel_active": lambda m, **kw: True,
            "_get_server_access": lambda: types.SimpleNamespace(activity=types.SimpleNamespace(observe=lambda m: None)),
            "_observe_server_lingo": lambda m: vocabulary_seen.append(m.id),
            "record_user_message": lambda uid, name, account: seen.append(uid), "ACCOUNT_INDEX": 1,
        }
        exec(compile(ast.Module(body=callbacks, type_ignores=[]), "discord-handler", "exec"),  # noqa: S102
             namespace)
        quiet, direct = message(1, uid=2, text="the server topic"), message(2, text="<@999> thoughts?")
        direct.mentions = [types.SimpleNamespace(id=999)]
        for target in (quiet, direct):
            target.channel = text_channel(id=100, parent_id=None)
            target.stickers = []
            await namespace["on_message"](target)
        await self.settle()
        self.assertEqual(seen, [2, 1])
        self.assertEqual(vocabulary_seen, [1, 2])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("the server topic", json_text(self.sent[0][1]))
        self.assertTrue(self.sent[0][2])
        # The legacy trigger path also honors the active window, but retains its owner controls.
        bot.active_chat = types.SimpleNamespace(followup=lambda m, self_id: m.author.id == 1)
        namespace["companion_memory"].server_enabled = lambda: False
        followup = message(3, text="what about this though?")
        followup.channel = text_channel(id=100, parent_id=None)
        self.assertTrue((await namespace["is_trigger_message"](followup))[0])
        bot.hold_conversation = False
        self.assertFalse((await namespace["is_trigger_message"](followup))[0])
        bot.hold_conversation, bot.allow_server = True, False
        self.assertFalse((await namespace["is_trigger_message"](followup))[0])

    async def test_several_members_addressing_in_one_burst_get_one_reply(self):
        self.chat.receive(message(1, text="G, hey"))
        self.chat.receive(message(2, uid=2, text="what do you think?"), directed=True)
        self.chat.receive(message(3, uid=3, text="this is unrelated"))
        await self.settle()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0].author.id, 2)
        self.assertEqual(len(self.sent[0][1]), 3)

    async def test_same_author_burst_uses_latest_message(self):
        self.chat.receive(message(1, text="G, got a sec?"))
        self.chat.receive(message(2, text="which game do you recommend?"))
        await self.settle()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0].id, 2)

    async def test_background_facts_are_separated_by_author(self):
        self.chat.receive(message(1, text="I like chess"))
        self.chat.receive(message(2, uid=2, text="I like jazz"))
        self.chat.receive(message(3, text="I play on weekends"))
        self.chat.receive(message(4, uid=2, text="I play saxophone"))
        await self.settle()
        self.assertCountEqual(self.learned, [(1, "I like chess\nI play on weekends"),
                                             (2, "I like jazz\nI play saxophone")])
        self.assertEqual(self.sent, [])

    async def test_same_member_in_different_channels_keeps_context_separate(self):
        self.chat.receive(message(1, text="G, games?", channel=100))
        self.chat.receive(message(2, text="G, music?", channel=200))
        await self.settle()
        self.assertEqual(len(self.sent), 2)
        self.assertNotIn("music?", json_text(self.sent[0][1]))
        self.assertNotIn("games?", json_text(self.sent[1][1]))

    async def test_name_matching_uses_whole_words_and_direct_mentions_work(self):
        self.chat.receive(message(1, text="great GPU"))
        await self.settle()
        self.assertEqual(self.sent, [])
        self.chat.receive(message(2, text="hello <@999>"), directed=True)
        await self.settle()
        self.assertEqual(len(self.sent), 1)

    async def test_short_followups_are_limited_to_the_last_member(self):
        self.chat.receive(message(1, text="G, hello"))
        await self.settle()
        self.chat.receive(message(2, uid=2, text="I'm chatting with someone else"))
        self.chat.receive(message(3, text="what about multiplayer?"))
        await self.settle()
        self.assertEqual([m.id for m, _, _ in self.sent], [1, 3])
        self.time += 46
        self.chat.receive(message(4, text="the conversation moved on"))
        await self.settle()
        self.assertEqual(len(self.sent), 2)

    async def test_reply_to_somebody_else_is_not_a_followup(self):
        self.chat.receive(message(1, text="G, hello"))
        await self.settle()
        other = message(2, text="thanks for that")
        other.reference = types.SimpleNamespace(resolved=message(50, uid=2))
        self.chat.receive(other)
        await self.settle()
        self.assertEqual(len(self.sent), 1)

    async def test_ambient_probability_is_optional_and_is_not_a_relationship_interaction(self):
        self.chat.receive(message(1, text="general conversation"))
        await self.settle()
        self.assertEqual(self.sent, [])
        self.config["ambient_reply_probability"] = 1
        self.config.update({"ambient_min_messages": 1, "ambient_min_members": 1, "ambient_read_seconds": 0})
        self.chat.receive(message(2, text="another topic"))
        await self.settle()
        self.assertFalse(self.sent[0][2])

    async def test_duplicate_events_do_not_duplicate_context_or_learning(self):
        target = message(1, text="G, hello")
        self.chat.receive(target)
        self.assertFalse(self.chat.receive(target))
        await self.settle()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.sent[0][1]), 1)
        self.assertEqual(len(self.learned), 1)

    async def test_edit_and_delete_update_context_and_buffered_learning(self):
        self.chat.receive(message(1, text="old fact"))
        self.chat.edit(100, 1, "corrected fact")
        self.chat.receive(message(2, uid=2, text="delete this"))
        self.chat.delete(100, 2)
        self.chat.receive(message(3, text="G, hello"))
        await self.settle()
        self.assertIn("corrected fact", json_text(self.sent[0][1]))
        self.assertNotIn("delete this", json_text(self.sent[0][1]))
        self.assertEqual(self.learned, [(1, "corrected fact\nG, hello")])

    async def test_manual_self_messages_are_assistant_turns(self):
        self.chat.observe(message(1, uid=999, text="here's what I think"))
        self.chat.receive(message(2, text="G, why?"))
        await self.settle()
        self.assertEqual(self.sent[0][1][0]["role"], "assistant")
        self.assertEqual(len(self.learned), 1)

    async def test_stale_and_paused_opportunities_do_not_send(self):
        old = message(1, text="G, old question")
        old.created_at = datetime.fromtimestamp(0, timezone.utc)
        self.chat.receive(old)
        await self.settle()
        self.enabled = False
        self.chat.receive(message(2, text="G, while paused"))
        await self.settle()
        self.assertEqual(self.sent, [])

    async def test_cancel_can_be_followed_immediately_by_a_new_request(self):
        self.chat.receive(message(1, text="G, first"))
        old_task = self.chat.channels[100].task
        self.assertTrue(self.chat.cancel(100))
        self.chat.receive(message(2, uid=2, text="G, second"))
        await asyncio.gather(old_task, return_exceptions=True)
        await self.settle()
        self.assertEqual([m.id for m, _, _ in self.sent], [2])

    async def test_cancel_stops_inflight_generation(self):
        started = asyncio.Event()
        finished = []

        async def slow(target, history, directed):
            started.set()
            await asyncio.Event().wait()
            finished.append(target.id)

        self.chat.respond = slow
        self.chat.receive(message(1, text="G, first"))
        await asyncio.wait_for(started.wait(), 1)
        task = self.chat.channels[100].task
        self.assertTrue(self.chat.cancel(100))
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(finished, [])

    async def test_messages_arriving_during_generation_are_not_lost(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def respond(target, history, directed):
            self.sent.append((target, history, directed))
            if len(self.sent) == 1:
                started.set()
                await release.wait()
            return "reply"

        self.chat.respond = respond
        self.chat.receive(message(1, text="G, hello"))
        await asyncio.wait_for(started.wait(), 1)
        self.chat.receive(message(2, text="and another thing"))
        release.set()
        await self.settle()
        self.assertEqual([m.id for m, _, _ in self.sent], [1, 2])

    async def test_followup_limit_applies_to_messages_queued_during_inference(self):
        self.chat.channels[100] = self.chat._channel(message(1))
        state = self.chat.channels[100]
        state.last_author, state.last_reply, state.followups = 1, self.time, 1
        started, release = asyncio.Event(), asyncio.Event()

        async def respond(target, history, directed):
            self.sent.append((target, history, directed))
            started.set()
            await release.wait()
            return "reply"

        self.chat.respond = respond
        self.chat.receive(message(2, text="second follow-up"))
        await asyncio.wait_for(started.wait(), 1)
        self.chat.receive(message(3, text="third follow-up"))
        release.set()
        await self.settle()
        self.assertEqual([m.id for m, _, _ in self.sent], [2])

    async def test_an_outgoing_send_is_distinguished_from_operator_typing(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def respond(target, history, directed):
            self.assertFalse(self.chat.is_sending(target.channel.id))
            self.chat.mark_sending(target.channel.id)
            started.set()
            await release.wait()
            return "reply"

        self.chat.respond = respond
        self.chat.receive(message(1, text="G, hello"))
        await asyncio.wait_for(started.wait(), 1)
        self.assertTrue(self.chat.is_sending(100))
        release.set()
        await self.settle()
        self.assertFalse(self.chat.is_sending(100))

    async def test_history_is_seeded_once_and_bounded(self):
        seeds = []

        async def seed(first):
            seeds.append(first.id)
            return [{"role": "user", "content": "older discussion"}]

        self.chat.seed = seed
        self.chat.receive(message(1, text="G, hello"))
        await self.settle()
        for mid in range(2, 70):
            self.chat.receive(message(mid, uid=2, text="ordinary chatter"))
        self.chat.receive(message(70, text="G, another question"))
        await self.settle()
        self.assertEqual(seeds, [1])
        self.assertIn("older discussion", json_text(self.sent[0][1]))
        self.assertLessEqual(len(self.sent[-1][1]), 40)


def json_text(history):
    return "\n".join(turn["content"] for turn in history)


class ServerAccess(unittest.TestCase):
    def setUp(self):
        base = yaml.safe_load((ROOT / "resources/config.yaml").read_text(encoding="utf-8"))
        self.config = build_config(base, "local", channels=[100], servers=[20])

    def test_server_allowlist_applies_to_its_channels_only(self):
        self.assertTrue(social.message_allowed(message(1, channel=555, guild=20), self.config))
        self.assertFalse(social.message_allowed(message(1, channel=555, guild=21), self.config))
        self.assertFalse(social.message_allowed(message(1, channel=555, guild=None), self.config))

    def test_character_preset_follows_servers_joined_later_without_a_new_profile(self):
        base = yaml.safe_load((ROOT / "resources/config.yaml").read_text(encoding="utf-8"))
        config = build_config(base, "local")
        self.assertTrue(config["bot"]["companion"]["follow_joined_servers"])
        self.assertTrue(social.message_allowed(message(1, channel=555, guild=999), config))
        self.assertTrue(social.message_allowed(message(2, channel=666, guild=1000), config))
        self.assertFalse(social.message_allowed(message(3, channel=777, guild=None), config))
        self.assertFalse(self.config["bot"]["companion"]["follow_joined_servers"])

    def test_threads_inherit_only_an_allowed_parent(self):
        self.assertTrue(social.message_allowed(message(1, channel=101, parent=100), self.config))
        self.assertFalse(social.message_allowed(message(1, channel=101, parent=200), self.config))
        config = copy.deepcopy(self.config)
        config["bot"]["companion"]["include_threads"] = False
        self.assertFalse(social.message_allowed(message(1, channel=101, parent=100), config))

    def test_empty_and_malformed_access_lists_fail_closed(self):
        config = copy.deepcopy(self.config)
        config["bot"]["companion"].update(allowed_channel_ids=[], allowed_guild_ids="20")
        self.assertFalse(social.message_allowed(message(1, guild=20), config))

    def test_runner_sends_raw_subject_content_to_fact_learning(self):
        tree = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())
        callback = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == "_learn_in_server")
        calls = []

        async def update(author, content, reply):
            calls.append((author.id, content, reply))

        namespace = {"_update_memory_after_reply": update}
        exec(compile(ast.Module(body=[callback], type_ignores=[]), "server-callback", "exec"),  # noqa: S102
             namespace)
        asyncio.run(namespace["_learn_in_server"](types.SimpleNamespace(id=123), "my own fact"))
        self.assertEqual(calls, [(123, "my own fact", "")])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failures = len(result.failures) + len(result.errors)
    print(f"\n{result.testsRun-failures} passed, {failures} failed")
    SANDBOX.cleanup()
    sys.exit(0 if result.wasSuccessful() else 1)

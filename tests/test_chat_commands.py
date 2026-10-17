"""No Discord commands, replies, reactions or learning from slash messages."""
import ast
import asyncio
import sys
import types
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.utils import conversation, lingo
from app.utils.server_chat import ServerChat

CONFIG = {"bot": {"prefix": "/", "chat_commands": {"enabled": False, "ignore_slash": True}}}
TREE = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())


def callback(name, namespace):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), "discord-command-guard", "exec"), namespace)  # noqa: S102
    return namespace[name]


def message(text, uid=10):
    return types.SimpleNamespace(content=text, author=types.SimpleNamespace(id=uid),
                                 type=types.SimpleNamespace(name="default"))


class DisabledCommands(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = patch.object(conversation, "_config", return_value=CONFIG)
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_whitespace_unicode_and_native_slash_invocations_are_ignored(self):
        for text in ("/help", "/", "/persona act like a boomer", "  /help", "\n\t/help", "\u200b/help", "／help"):
            self.assertTrue(conversation.ignored_chat_message(message(text)), text)
        native = message("")
        native.type.name = "chat_input_command"
        self.assertTrue(conversation.ignored_chat_message(native))
        for text in ("hello", "https://example.com/help", "1/2", "what does /help mean?", '"/help"'):
            self.assertFalse(conversation.ignored_chat_message(message(text)), text)

    async def test_real_gateway_returns_before_owner_self_and_member_command_paths(self):
        log = Mock()
        ns = {"conversation": conversation, "companion_memory": types.SimpleNamespace(message_allowed=lambda m: True),
              "_log_dm": log, "bot": types.SimpleNamespace(selfbot_id=99)}
        on_message = callback("on_message", ns)
        for uid in (99, 10, 11):
            await on_message(message(" /help", uid))
        self.assertEqual(log.call_count, 3)
        # There are no command/reply/learning functions in this namespace: reaching one would fail.

    async def test_command_extensions_are_not_loaded_and_prefix_resolves_to_nothing(self):
        ns = {"conversation": conversation}
        await callback("load_extensions", ns)()
        prefix = callback("discord_command_prefix", ns)
        self.assertEqual(prefix(None, message("/help")), [])
        self.assertEqual(prefix(None, message("!help")), [])

    async def test_manual_reply_cannot_force_a_response_to_a_slash_message(self):
        states = Mock()
        ns = {"conversation": conversation, "companion_memory": types.SimpleNamespace(message_allowed=lambda m: True),
              "set_reply_state": states}
        reply = callback("generate_response_and_reply", ns)
        self.assertIsNone(await reply(message("/help"), "/help", [], bypass_cooldown=True))
        self.assertIn("slash commands are disabled", states.call_args.kwargs["reason"])

    async def test_queued_slash_messages_are_discarded_without_starting_inference(self):
        key = "10-100"
        bot = types.SimpleNamespace(processing_locks={key: asyncio.Lock()},
                                    message_queues={key: deque([message("/help"), message(" /status")])})
        ns = {"conversation": conversation, "bot": bot}
        await callback("process_message_queue", ns)(key)
        self.assertEqual(bot.message_queues, {})
        self.assertEqual(bot.processing_locks, {})

    def test_history_drops_commands_and_their_old_answers_but_preserves_normal_chat(self):
        history = [{"role": "user", "content": "/help\nact like a boomer"},
                   {"role": "assistant", "content": "Available commands: ..."},
                   {"role": "user", "content": "hey"}, {"role": "assistant", "content": "hi"}]
        self.assertEqual(conversation.model_history(history, CONFIG), history[2:])
        self.assertEqual(history[0]["content"], "/help\nact like a boomer")
        for label in ("Member:", "Member [user 10]:"):
            self.assertEqual(conversation.without_slash_history([{"role": "user", "content": label+" /help"}]), [])
        normal = [{"role": "user", "content": "  hello\nhttps://example.com/help\n1/2"}]
        self.assertEqual(conversation.without_slash_history(normal), normal)

    def test_server_observation_and_vocabulary_learning_drop_slash_messages(self):
        chat = ServerChat(self_id=lambda: 99, get_config=dict, respond=None, learn=None, seed=None,
                          can_reply=lambda m: True)
        self.assertIsNone(chat.observe(message("/hello")))
        self.assertEqual(chat.channels, {})
        learner = lingo.Learner(can_learn=lambda m: True)
        learner.observe(message("/define snouted means something"))
        self.assertEqual(learner.channels, {})

    async def test_background_memory_hook_drops_slash_without_reading_or_writing_memory(self):
        learn = callback("_update_memory_after_reply", {"conversation": conversation})
        await learn(types.SimpleNamespace(id=10), "/remember my age is 80", "reply")

    def test_owner_can_reenable_commands_deliberately_without_treating_urls_as_commands(self):
        cfg = {"bot": {"prefix": "!", "chat_commands": {"enabled": True, "ignore_slash": False}}}
        with patch.object(conversation, "_config", return_value=cfg):
            self.assertTrue(conversation.commands_enabled())
            self.assertFalse(conversation.ignored_chat_message(message("/hello")))
            prefix = callback("discord_command_prefix", {"conversation": conversation, "load_config": lambda: cfg})
            self.assertEqual(prefix(None, message("!help")), "!")


if __name__ == "__main__":
    unittest.main()

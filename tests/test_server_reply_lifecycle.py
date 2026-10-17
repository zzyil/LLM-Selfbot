"""Expiry and cancellation cannot leave channel replies stuck at typing/sending."""
import ast
import asyncio
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SANDBOX = tempfile.TemporaryDirectory(prefix="reply-lifecycle-")
os.environ["LLMSELFBOT_DATA_DIR"] = SANDBOX.name
(Path(SANDBOX.name) / "config").mkdir()
(Path(SANDBOX.name) / "config/config.yaml").write_bytes((ROOT / "resources/config.yaml").read_bytes())

from app.utils.server_chat import ServerChat

N = types.SimpleNamespace
TREE = ast.parse((ROOT / "app/platforms/discord_runner.py").read_text())


def compile_functions(names, scope):
    nodes = [node for node in ast.walk(TREE) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "reply-lifecycle", "exec"), scope)  # noqa: S102 - actual repository source


class ChannelReplyLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.events, self.errors = [], []
        self.bot = N(server_waiting={}, channel_reply_state={}, reply_state={7: {"state": "waiting"}}, waiting_msgs={7: object()})
        scope = {"bot": self.bot, "time": N(time=lambda: self.now), "emit_chat_event": self.events.append,
                 "log_system": lambda text: None}
        compile_functions({"set_reply_state", "clear_reply_state", "_finish_server_reply"}, scope)
        self.finish = scope["_finish_server_reply"]
        self.config = {"names": ["G"], "batch_wait_seconds": 0, "reply_cooldown_seconds": 0, "learn_from_chat": False,
                       "ambient_reply_probability": 1, "ambient_min_messages": 1, "ambient_min_members": 1,
                       "ambient_read_seconds": 0, "ambient_cooldown_seconds": 0, "ambient_global_cooldown_seconds": 0,
                       "max_direct_reply_age_seconds": 120}

        def scheduled(message, delay):
            self.bot.server_waiting[message.channel.id] = message
            self.bot.channel_reply_state[message.channel.id] = {"state": "sending"}

        self.chat = ServerChat(self_id=lambda: 999, get_config=lambda: self.config, respond=AsyncMock(return_value="sent"),
                               learn=AsyncMock(), seed=AsyncMock(return_value=[]), can_reply=lambda m: True,
                               scheduled=scheduled, finished=self.finish, report=self.errors.append,
                               clock=lambda: self.now, wall_clock=lambda: self.now, rng=lambda: 0)
        self.scope = scope

    async def asyncTearDown(self):
        await self.chat.close()

    def message(self, mid=1, text="G hello"):
        return N(id=mid, content=text, guild=N(id=10), author=N(id=7, name="Member"),
                 channel=N(id=100, send=AsyncMock()), created_at=datetime.fromtimestamp(1000, timezone.utc),
                 reference=None, mentions=[])

    async def settle(self):
        await asyncio.gather(*self.chat.reply_tasks, return_exceptions=True)

    def assert_finished(self):
        self.assertEqual(self.bot.server_waiting, {})
        self.assertEqual(self.bot.channel_reply_state, {})
        self.assertEqual(self.bot.reply_state[7]["state"], "waiting")
        self.assertIn(7, self.bot.waiting_msgs)

    async def test_cancelled_send_clears_typing_without_changing_the_members_dm(self):
        async def expired(message, history, directed):
            self.now += 130
            raise asyncio.CancelledError

        self.chat.respond = expired
        self.chat.receive(self.message())
        await self.settle()
        self.assert_finished()
        self.assertEqual(self.events[-1]["t"], "done")

    async def test_expiry_before_generation_also_clears_the_scheduled_status(self):
        self.now = 1201
        self.chat.receive(self.message())
        await self.settle()
        self.chat.respond.assert_not_awaited()
        self.assert_finished()

    async def test_empty_response_is_failed_instead_of_stuck_sending(self):
        self.chat.respond.return_value = None
        self.chat.receive(self.message())
        await self.settle()
        self.assertEqual(self.bot.channel_reply_state[100]["state"], "failed")
        self.assertIn(100, self.bot.server_waiting)

    async def test_actual_timeout_and_privacy_reasons_survive_scheduler_cleanup(self):
        for status, reason in [('failed', 'reply check timed out'), ('skipped', 'output privacy check withheld the reply')]:
            async def withheld(message, history, directed, status=status, reason=reason):
                self.bot.channel_reply_state[100] = {'state': status, 'reason': reason}
            self.chat.respond = withheld
            message = self.message(mid=1 if status == "failed" else 2)
            self.chat.receive(message)
            await self.settle()
            self.assertEqual(self.bot.channel_reply_state[100], {'state': status, 'reason': reason})
            self.assertIs(self.bot.server_waiting[100], message)

    async def test_unexpected_error_finishes_one_attempt_and_services_the_next(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def respond(message, history, directed):
            if message.id == 1:
                started.set()
                await release.wait()
                raise RuntimeError("failed send")
            return "sent"

        self.chat.respond = respond
        self.chat.receive(self.message())
        await started.wait()
        self.chat.receive(self.message(2))
        release.set()
        await self.settle()
        self.assertEqual(len(self.errors), 1)
        self.assert_finished()

    def test_finishing_an_old_attempt_cannot_clear_a_newer_channel_message(self):
        old, new = self.message(), self.message(2)
        self.bot.server_waiting[100] = new
        self.bot.channel_reply_state[100] = {"state": "waiting"}
        self.finish(old, "cancelled", "expired")
        self.assertIs(self.bot.server_waiting[100], new)
        self.assertEqual(self.bot.channel_reply_state[100]["state"], "waiting")

    async def test_cancel_while_waiting_for_the_batch_timer_clears_the_queue(self):
        self.config["batch_wait_seconds"] = 30
        self.chat.receive(self.message())
        await asyncio.sleep(0)
        self.chat.cancel(100)
        await self.settle()
        self.assert_finished()

    async def test_slow_generation_reserves_time_for_an_ambient_send(self):
        """Replay the actual deadline/typing/send path with a deterministic clock and fake Discord."""
        message = self.message(text="pizza is great")

        class Typing:
            async def __aenter__(self):
                pass

            async def __aexit__(self, *args):
                pass

        async def sleep(seconds):
            self.now += seconds

        message.channel.typing = Typing
        scope = {"time": N(monotonic=lambda: self.now), "asyncio": N(sleep=sleep, wait_for=asyncio.wait_for),
                 "typing_time": lambda text, fast: 45, "typing_bursts": lambda total, fast: [(6, 3)]*10,
                 "log_system": lambda text: None}
        compile_functions({"_simulate_typing"}, scope)
        sends = {"message": message, "chunk": "pizza takes", "server_initiated": True, "update_memory": False,
                 "_server_channel_active": lambda m, **kw: True, "_server_can_reply": lambda m: True,
                 "_server_chat": self.chat, "asyncio": asyncio, "set_reply_state": self.scope["set_reply_state"],
                 "discord": N(AllowedMentions=N(none=lambda: "none"))}
        from app.utils.output_guard import Delivery
        sends.update(delivery=Delivery((sends["chunk"],)), i=0)
        compile_functions({"_send_chunk"}, sends)

        async def respond(message, history, directed):
            self.now += 77  # the observed reply spent most of its age budget in local inference
            await scope["_simulate_typing"](message.channel, "reply", max_seconds=self.chat.ambient_seconds_left(message)-3)
            self.assertEqual(self.now, 1117)
            await sends["_send_chunk"]()
            return "sent"

        self.chat.respond = respond
        self.chat.receive(message)
        await self.settle()
        self.assertEqual(self.errors, [])
        message.channel.send.assert_awaited_once_with("pizza takes", allowed_mentions="none")
        self.assert_finished()


class TypingBoundaries(unittest.IsolatedAsyncioTestCase):
    async def test_a_stuck_typing_indicator_times_out_without_blocking_the_send_path(self):
        async def timeout(coroutine, timeout):
            coroutine.close()
            self.assertEqual(timeout, 7)
            raise TimeoutError

        logs = []
        scope = {"typing_time": lambda text, fast: 45, "time": N(monotonic=lambda: 0),
                 "asyncio": N(wait_for=timeout), "log_system": logs.append}
        compile_functions({"_simulate_typing"}, scope)
        await scope["_simulate_typing"](N(), "hello", max_seconds=5)
        self.assertEqual(len(logs), 1)

    async def test_no_time_left_means_no_typing_request(self):
        scope = {"typing_time": lambda text, fast: 45}
        compile_functions({"_simulate_typing"}, scope)
        await scope["_simulate_typing"](N(), "hello", max_seconds=0)


if __name__ == "__main__":
    unittest.main()

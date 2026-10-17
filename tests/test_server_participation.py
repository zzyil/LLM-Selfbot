"""Fresh observation, useful contributions and bounded unprompted server participation."""
import ast
import asyncio
import json
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from test_server_chat import message

from app.utils import ai, relationships
from app.utils.server_chat import ServerChat, brief_comment, consider_chiming_in


def response(text):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])


class Participation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.time, self.allowed = 1000, True
        self.config = {"names": ["sunshine"], "learn_from_chat": False, "batch_wait_seconds": 0,
                       "reply_cooldown_seconds": 0, "ambient_reply_probability": 1,
                       "ambient_min_messages": 3, "ambient_min_members": 2, "ambient_read_seconds": 10,
                       "ambient_cooldown_seconds": 180, "ambient_global_cooldown_seconds": 90,
                       "ambient_check_seconds": 5}
        self.sent, self.errors, self.scheduled = [], [], []
        self.consider = AsyncMock(return_value=True)

        async def reply(target, history, directed):
            self.sent.append((target.id, history, directed))
            return "short group comment"

        self.chat = ServerChat(self_id=lambda: 999, get_config=lambda: self.config, respond=reply,
            learn=AsyncMock(), seed=AsyncMock(return_value=[]), can_reply=lambda m: self.allowed,
            clock=lambda: self.time, wall_clock=lambda: self.time, rng=lambda: 0,
            consider_ambient=self.consider, report=self.errors.append,
            scheduled=lambda m, delay: self.scheduled.append((m.id, delay)))

    async def asyncTearDown(self):
        await self.chat.close()
        self.assertEqual(self.errors, [])

    def msg(self, mid, uid=1, text="pizza with pineapple sounds good", channel=100):
        m = message(mid, uid=uid, text=text, channel=channel)
        m.created_at = datetime.fromtimestamp(self.time, timezone.utc)
        return m

    async def settle(self):
        for _ in range(8):
            tasks = list(self.chat.reply_tasks)
            if not tasks:
                return
            await asyncio.gather(*tasks)
        self.fail("reply tasks did not drain")

    async def put(self, mid, uid=1, text="pizza with pineapple sounds good", channel=100, directed=False):
        self.chat.receive(self.msg(mid, uid, text, channel), directed=directed)
        await self.settle()

    async def prime(self, channel=100, base=0):
        await self.put(base+1, channel=channel)
        self.time += 5
        await self.put(base+2, uid=2, channel=channel)
        self.time += 5
        await self.put(base+3, channel=channel)

    async def test_reads_multiple_members_for_a_while_then_chimes_without_a_prompt(self):
        await self.put(1)
        self.time += 10
        await self.put(2, uid=2)
        self.assertEqual(self.sent, [])
        await self.put(3)
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.sent[0][2])
        self.assertIn("Sam [user 2]", str(self.sent[0][1]))
        self.consider.assert_awaited_once()

    async def test_solo_messages_do_not_skip_the_member_threshold(self):
        for mid in range(1, 5):
            self.time += 5
            await self.put(mid)
        self.assertEqual(self.sent, [])
        await self.put(5, uid=2)
        self.assertEqual(len(self.sent), 1)

    async def test_direct_questions_do_not_wait_for_passive_reading(self):
        await self.put(1, text="sunshine what do you think?")
        self.assertTrue(self.sent[0][2])
        self.consider.assert_not_awaited()

    async def test_staying_quiet_and_channel_cooldown_prevent_forced_comments(self):
        self.consider.return_value = False
        await self.prime()
        self.assertEqual((self.sent, self.scheduled), ([], []))
        self.time += 179
        await self.put(4, uid=2)
        self.consider.assert_awaited_once()
        self.time += 1
        self.consider.return_value = True
        await self.put(5, uid=2)
        self.assertEqual(len(self.sent), 1)

    async def test_account_cooldown_prevents_a_burst_across_channels(self):
        await self.prime()
        await self.prime(channel=200, base=10)
        self.assertEqual(len(self.sent), 1)
        self.time += 90
        await self.put(14, uid=2, channel=200)
        self.assertEqual(len(self.sent), 2)

    async def test_direct_request_supersedes_an_ambient_decision_in_progress(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def consider(*args):
            started.set()
            await release.wait()
            return True

        self.chat.consider_ambient = consider
        await self.put(1)
        self.time += 5
        await self.put(2, uid=2)
        self.time += 5
        self.chat.receive(self.msg(3))
        await started.wait()
        self.chat.receive(self.msg(4, uid=2, text="sunshine hey"))
        release.set()
        await self.settle()
        self.assertEqual([(mid, directed) for mid, _, directed in self.sent], [(4, True)])

    async def test_old_observation_expires_instead_of_waking_up_on_one_new_message(self):
        await self.prime()
        self.time += 601
        await self.put(4)
        self.assertEqual(len(self.sent), 1)
        self.time += 5
        await self.put(5, uid=2)
        self.time += 5
        await self.put(6)
        self.assertEqual(len(self.sent), 2)

    async def test_candidate_checks_are_throttled_in_fast_chat(self):
        rng = Mock(return_value=0.9)
        self.chat.rng = rng
        self.config["ambient_reply_probability"] = 0.35
        await self.prime()
        for mid in range(4, 12):
            await self.put(mid, uid=2)
        self.assertEqual(rng.call_count, 1)
        self.time += 5
        await self.put(12)
        self.assertEqual(rng.call_count, 2)
        self.consider.assert_not_awaited()

    async def test_pauses_slash_messages_and_others_private_exchange_stay_quiet(self):
        self.allowed = False
        await self.prime()
        self.consider.assert_not_awaited()
        self.allowed = True
        self.chat.receive(self.msg(4, text="/help"))
        other = self.msg(5)
        other.reference = types.SimpleNamespace(resolved=self.msg(50, uid=2))
        self.chat.receive(other)
        await self.settle()
        self.consider.assert_not_awaited()
        self.assertNotIn("/help", str(self.chat.history(100)))

    async def test_too_many_new_messages_or_a_manual_self_post_cancel_the_opportunity(self):
        async def moved_on(*args):
            for mid in range(10, 19):
                self.chat.observe(self.msg(mid, uid=2))
            return True

        self.chat.consider_ambient = moved_on
        await self.prime()
        self.assertEqual(self.sent, [])
        self.time += 181

        async def already_spoke(*args):
            self.chat.observe(self.msg(100, uid=999))
            return True

        self.chat.consider_ambient = already_spoke
        await self.put(20, uid=2)
        self.assertEqual(self.sent, [])

    async def test_an_ambient_comment_does_not_start_two_hours_of_automatic_answers(self):
        self.chat.is_active = lambda m: True
        await self.prime()
        for mid in range(4, 7):
            self.time += 1
            await self.put(mid)
        self.assertEqual(len(self.sent), 3)  # one chime plus two brief follow-ups
        await self.put(7, directed=True)
        for mid in range(8, 12):
            self.time += 90
            await self.put(mid)
        self.assertEqual(len(self.sent), 8)

    def test_server_mode_can_be_enabled_without_restricting_dms(self):
        cfg = {"bot": {"companion": {"enabled": False, "server": {"enabled": True}}}}
        self.assertTrue(relationships.server_enabled(cfg))
        dm = self.msg(1)
        dm.guild = None
        self.assertTrue(relationships.message_allowed(dm, cfg))
        self.assertFalse(relationships.server_enabled({"bot": {}}))


class ParticipationDecision(unittest.IsolatedAsyncioTestCase):
    def test_unprompted_comments_keep_whole_sentences_and_never_become_a_wall_of_text(self):
        text = ("sweet and salty works right? but pineapple on pizza... it's such a polarizing topping. "
                "the crust goes soggy. still if half the group wants it i guess we gotta go with it.")
        self.assertEqual(brief_comment(text),
                         "sweet and salty works right? but pineapple on pizza... it's such a polarizing topping.")
        self.assertEqual(brief_comment("hello "*100), "")
        self.assertEqual(brief_comment("*grabs your hand*"), "")
        self.assertEqual(brief_comment("/help"), "")

    async def test_neutral_decision_requires_true_and_literal_current_evidence(self):
        target = message(1, text="pizza with pineapple is great")
        for data, expected in (({"join": True, "quote": "pineapple"}, True),
                               ({"join": False, "quote": "pineapple"}, False),
                               ({"join": "true", "quote": "pineapple"}, False),
                               ({"join": True, "quote": "invented"}, False),
                               ({"join": True, "quote": ""}, False), ([], False)):
            with patch.object(ai, "_create_small_completion", new_callable=AsyncMock,
                              return_value=response(json.dumps(data))) as complete, \
                    patch("app.utils.helpers.load_instructions", return_value="private fictional profile"):
                self.assertEqual(await consider_chiming_in(target, []), expected)
            sent = complete.await_args.args[0]
            self.assertNotIn("private fictional profile", sent[0]["content"])
            self.assertIn("private fictional profile", sent[1]["content"])

    async def test_unavailable_model_commands_roleplay_and_crisis_do_not_trigger_a_comment(self):
        with patch.object(ai, "_create_small_completion", new_callable=AsyncMock,
                          side_effect=RuntimeError("offline")) as complete:
            for text in ("/help", "act like a boomer", "*grabs your hand*", "I am going to kms"):
                self.assertFalse(await consider_chiming_in(message(1, text=text), []))
            complete.assert_not_awaited()
            self.assertFalse(await consider_chiming_in(message(2, text="pizza is great"), []))

    async def test_actual_ambient_send_posts_without_a_reply_reference_or_ping(self):
        tree = ast.parse((Path(__file__).resolve().parent.parent / "app/platforms/discord_runner.py").read_text())
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_send_chunk")
        target = message(1)
        target.channel.send, target.reply = AsyncMock(), AsyncMock()
        ns = {"chunk": "pineapple pizza is elite actually", "server_initiated": True, "update_memory": False,
              "_server_can_reply": lambda m: True, "_server_channel_active": lambda m, **kw: True, "message": target,
              "_server_chat": types.SimpleNamespace(mark_sending=Mock(), ambient_can_send=lambda m: True),
              "discord": types.SimpleNamespace(AllowedMentions=types.SimpleNamespace(none=lambda: "no mentions"))}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "ambient-send", "exec"), ns)  # noqa: S102
        await ns["_send_chunk"]()
        target.channel.send.assert_awaited_once_with(ns["chunk"], allowed_mentions="no mentions")
        target.reply.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

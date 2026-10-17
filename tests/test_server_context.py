"""Replay server focus, stale sends, reaction drift and internal-note leaks without Discord."""
import ast
import asyncio
import json
import re
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from test_server_chat import message

from app.utils import ai, conversation, social
from app.utils.humanize import strip_meta
from app.utils.server_chat import ServerChat, consider_followup

ROOT = Path(__file__).resolve().parent.parent
TREE = ast.parse((ROOT / "app/platforms/discord_runner.py").read_bytes())
N = types.SimpleNamespace
CONFIG = {"bot": {"character_chat": {"social_awareness": True, "react_to_prompt_bait": True}}}


def compile_functions(names, scope):
    nodes = [n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "server-context", "exec"), scope)  # noqa: S102


def response(text):
    return N(choices=[N(message=N(content=text))])


class ReplyContext(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        privacy = patch("app.utils.output_guard._verify", AsyncMock(return_value=True))
        privacy.start()
        self.addCleanup(privacy.stop)

    async def test_server_seeding_never_reads_the_same_members_dm_and_keeps_speakers_separate(self):
        class DM:
            pass
        class Text(N):
            pass
        channel = Text(id=100)
        rows = [message(1, text="server topic", name="Sam"), message(2, uid=2, text="other member", name="Other")]
        for row in rows:
            row.channel, row.type = channel, 0
        async def history(**kwargs):
            for row in reversed(rows):
                yield row
        channel.history = history
        target = message(3, text="G question")
        target.channel = channel
        dm_seed = Mock(return_value=[{"role": "user", "content": "PRIVATE_DM_TOPIC"}])
        bot = N(message_history={"1-500": [{"role": "user", "content": "PRIVATE_DM_TOPIC"}]}, selfbot_id=999)
        ns = {"bot": bot, "discord": N(DMChannel=DM, MessageType=N(default=0, reply=1)),
              "SERVER_CHANNELS": (Text,), "conversation": conversation, "ACCOUNT_INDEX": 1,
              "history_scan_allowed": lambda: True, "_chatlog": N(seed_turns=dm_seed),
              "re": re, "log_system": Mock()}
        compile_functions({"seed_history", "_dm_partner", "history_key", "in_server", "as_turn", "speaker_label", "_turn_text"}, ns)
        key = ns["history_key"](target)
        await ns["seed_history"](key, target)
        self.assertEqual(key, "ch-100")
        dm_seed.assert_not_called()
        self.assertEqual(len(bot.message_history[key]), 2)
        self.assertIn("Sam [user 1]", bot.message_history[key][0]["content"])
        self.assertNotIn("PRIVATE_DM_TOPIC", str(bot.message_history[key]))
        self.assertIn("PRIVATE_DM_TOPIC", str(bot.message_history["1-500"]))

    def test_threads_have_server_boundaries_and_shared_facts_are_not_live_dialogue(self):
        target = message(1, uid=20, channel=300, parent=100)
        block = conversation.discord_context(target)
        data = conversation.context_values(block, "current Discord conversation")[0]
        self.assertEqual((data["kind"], data["channel_id"], data["speaker_id"]), ("server", "300", "20"))
        messages = conversation.model_messages("profile"+block, [{"role": "user", "content": "hi"}], CONFIG)
        self.assertIn("DMs and other server channels are separate", messages[0]["content"])
        self.assertIn("Do not quote private exchanges", messages[0]["content"])

    async def test_selected_server_speaker_is_last_even_when_snapshot_ends_with_another_speaker(self):
        target = message(3, uid=20, text="what games do you know", name="techcet")
        target.attachments, target.flags = [], N(value=0)
        history = [{"role": "user", "content": "techcet [user 20]: what games do you know"},
                   {"role": "user", "content": "zzy [user 10]: unrelated question"}]
        generate = AsyncMock(return_value=None)
        ns = {"conversation": conversation, "as_turn": lambda m, text: f"techcet [user 20]: {text}",
              "history_key": lambda m: "ch-100", "bot": N(message_history={}), "set_reply_state": Mock(),
              "generate_response_and_reply": generate, "_extract_image_url_from_message": lambda m: None,
              "asyncio": asyncio, "companion_memory": N(learn_interaction=AsyncMock(), discord_scope=lambda m: "character"),
              "_memory_tasks": set(), "_relationship_update_done": lambda t: None}
        compile_functions({"_reply_in_server"}, ns)
        await ns["_reply_in_server"](target, history, True)
        await asyncio.gather(*ns["_memory_tasks"])
        selected = generate.await_args.args[2]
        self.assertEqual(selected[-1]["content"], "techcet [user 20]: what games do you know")
        self.assertIn("zzy [user 10]", selected[-2]["content"])
        self.assertEqual(len(history), 2)

    async def test_ai_does_not_continue_an_assistant_tail_instead_of_the_current_member(self):
        history = [{"role": "user", "content": "old question"}, {"role": "assistant", "content": "old answer"}]
        with patch.object(ai, "_initialised", True), patch.object(conversation, "_config", return_value={"bot": {}}), \
                patch.object(ai, "_create_completion", new_callable=AsyncMock, return_value=response("current answer")) as model:
            await ai.generate_response("new question", "profile", history)
        self.assertEqual(model.await_args.args[0][-1], {"role": "user", "content": "new question"})
        self.assertEqual(len(history), 2)

    def test_a_substring_or_another_members_text_is_not_the_selected_turn(self):
        history = [{"role": "user", "content": "Sam [user 1]: this is different"}]
        self.assertEqual(conversation.focused_history(history, "hi")[-1]["content"], "hi")
        history = [{"role": "user", "content": "Other [user 2]: hi"}]
        self.assertEqual(conversation.focused_history(history, "hi", "Sam [user 1]: hi")[-1]["content"], "Sam [user 1]: hi")

    def test_meta_only_and_unclosed_thoughts_are_withheld_while_real_chat_is_preserved(self):
        for text in ("(Self-correction: I should respond to the agreement.)", "<think>unfinished analysis",
                     "[Note to self: use a different voice]", "(translation: hi)"):
            self.assertEqual(strip_meta(text), "")
        self.assertEqual(strip_meta("yeah\n(Self-correction: internal note)"), "yeah")
        self.assertEqual(strip_meta("yeah (Self-correction: internal note)"), "yeah")
        self.assertEqual(strip_meta("that self-correction was weird"), "that self-correction was weird")
        history = [{"role": "assistant", "content": "(Self-correction: I should answer.)"},
                   {"role": "user", "content": "why did you post Self-correction?"}]
        clean = conversation.model_history(history, CONFIG)
        self.assertEqual(len(clean), 1)
        self.assertIn("Self-correction", clean[0]["content"])

    async def test_meta_only_normal_completion_never_becomes_a_chat_message(self):
        with patch.object(ai, "_initialised", True), patch.object(conversation, "_config", return_value={"bot": {}}), \
                patch.object(ai, "_create_completion", new_callable=AsyncMock,
                             return_value=response("(Self-correction: I should answer now.)")):
            self.assertEqual(await ai.generate_response("Exactly", "profile"), "")

    def test_unrelated_insults_cannot_supply_an_age_biography(self):
        raw = {"kind": "insult", "target": "character", "category": "age", "direction": "older",
               "quote": "clown", "prior_quote": ""}
        assessment = social.parse_assessment(json.dumps(raw), "you clown")
        self.assertEqual(assessment["category"], "other")
        self.assertFalse(social.valid_reply("i'm 23 not some grandpa", assessment, []))
        self.assertTrue(social.valid_reply("what's your problem", assessment, []))

    def test_repetition_evidence_is_scoped_to_the_current_server_speaker(self):
        raw = {"kind": "false_claim", "target": "character", "category": "age", "direction": "older",
               "quote": "grandpa", "prior_quote": "stone tablet"}
        history = [{"role": "user", "content": "Other [user 1]: stone tablet"},
                   {"role": "user", "content": "Current [user 2]: grandpa"}]
        self.assertFalse(social.parse_assessment(json.dumps(raw), "grandpa", history)["repeated"])
        history[0]["content"] = "Current [user 2]: stone tablet"
        self.assertTrue(social.parse_assessment(json.dumps(raw), "grandpa", history)["repeated"])

    async def test_a_topic_question_misclassified_as_an_insult_gets_an_independent_target_check(self):
        prompt = "what lolcows do you know"
        wrong = {"kind": "insult", "target": "character", "category": "other", "direction": "other",
                 "quote": "lolcows", "prior_quote": ""}
        with patch.object(conversation, "_config", return_value=CONFIG), \
                patch.object(ai, "_create_small_completion", new_callable=AsyncMock, side_effect=[
                    response(json.dumps(wrong)), response('{"personal_insult":false,"quote":""}')]) as check, \
                patch.object(ai, "_create_completion", new_callable=AsyncMock) as actor:
            self.assertIsNone(await ai._social_reaction(prompt, "character age 23"))
        self.assertEqual(check.await_count, 2)
        self.assertNotIn("age 23", str(check.await_args.args[0]))
        actor.assert_not_awaited()


class CurrentServerReplies(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000
        self.sent, self.finished, self.errors = [], [], []
        self.config = {"names": ["G"], "batch_wait_seconds": 0, "reply_cooldown_seconds": 0, "learn_from_chat": False}
        async def respond(target, history, directed):
            self.sent.append((target.id, directed))
            return "sent"
        self.chat = ServerChat(self_id=lambda: 999, get_config=lambda: self.config, respond=respond,
            learn=AsyncMock(), seed=AsyncMock(return_value=[]), can_reply=lambda m: True,
            clock=lambda: self.now, wall_clock=lambda: self.now, report=self.errors.append,
            finished=lambda m, outcome, detail: self.finished.append((m.id, outcome, detail)))

    async def asyncTearDown(self):
        await self.chat.close()
        self.assertEqual(self.errors, [])

    async def settle(self):
        await asyncio.gather(*list(self.chat.reply_tasks), return_exceptions=True)

    def send_scope(self, target):
        target.reply = AsyncMock()
        ns = {"message": target, "chunk": f"answer {target.id}", "server_initiated": False, "update_memory": False,
              "_server_chat": self.chat, "_server_can_reply": lambda m: True, "_server_channel_active": lambda m, **kw: True,
              "set_reply_state": Mock(), "asyncio": asyncio, "discord": N(DMChannel=type("DM", (), {})),
              "_no_reply_channels": set(), "config": {"bot": {"reply_ping": False}}}
        from app.utils.output_guard import Delivery
        ns.update(delivery=Delivery((ns["chunk"],)), i=0)
        compile_functions({"_send_chunk"}, ns)
        return ns

    async def test_newer_messages_wait_for_the_accepted_reply_instead_of_cancelling_it(self):
        started, release = asyncio.Event(), asyncio.Event()
        first, latest = message(1, text="G old question"), message(2, text="G current question")
        scopes = {m.id: self.send_scope(m) for m in (first, latest)}
        async def respond(target, history, directed):
            if target.id == 1:
                started.set()
                await release.wait()  # inference or simulated typing
            await scopes[target.id]["_send_chunk"]()
            return "sent"
        self.chat.respond = respond
        self.chat.receive(first)
        await started.wait()
        self.chat.receive(latest)
        release.set()
        await self.settle()
        first.reply.assert_awaited_once()
        latest.reply.assert_awaited_once()
        self.assertEqual([mid for mid, outcome, _ in self.finished if outcome == "sent"], [1, 2])
        self.assertFalse(self.chat.has_pending(100))

    async def test_busy_chat_cannot_starve_a_reply_by_evicting_its_source_from_short_history(self):
        target = message(1, text="G question")
        scope = self.send_scope(target)
        async def respond(*args):
            for mid in range(2, 102):
                self.chat.receive(message(mid, uid=2, text=f"unrelated chat {mid}"))
            self.assertNotIn(target, self.chat.channels[100].messages)
            self.assertFalse(self.chat.ambient_can_send(target))
            await scope["_send_chunk"]()
            return "sent"
        self.chat.respond = respond
        self.chat.receive(target)
        await self.settle()
        target.reply.assert_awaited_once()
        self.assertEqual(self.finished[-1][1], "sent")

    async def test_send_rechecks_age_after_inference(self):
        target = message(1, text="G question")
        scope = self.send_scope(target)
        async def respond(*args):
            self.now += 301
            await scope["_send_chunk"]()
        self.chat.respond = respond
        self.chat.receive(target)
        await self.settle()
        target.reply.assert_not_awaited()
        self.assertEqual(self.finished[-1][1], "cancelled")

    async def test_an_edit_updates_a_waiting_request_even_after_busy_chat_evicts_it(self):
        first = message(1, text="G first")
        waiting = message(2, uid=2, text="G old question")
        contents = []
        async def respond(target, *args):
            contents.append(target.content)
            if target.id == 1:
                self.chat.receive(waiting)
                for mid in range(3, 103):
                    self.chat.receive(message(mid, uid=3, text=f"unrelated chat {mid}"))
                self.chat.edit(100, waiting.id, "G edited question")
            return "sent"
        self.chat.respond = respond
        self.chat.receive(first)
        await self.settle()
        self.assertEqual(contents, ["G first", "G edited question"])

    async def test_accepted_reply_has_more_time_than_an_unsolicited_comment(self):
        target = message(1, text="G question")
        scope = self.send_scope(target)
        async def respond(*args):
            self.now += 121
            self.assertFalse(self.chat.ambient_can_send(target))
            await scope["_send_chunk"]()
            return "sent"
        self.chat.respond = respond
        self.chat.receive(target)
        await self.settle()
        target.reply.assert_awaited_once()

    async def test_retraction_only_invalidates_the_same_members_accepted_turn(self):
        for retracting_uid, expected_sends in ((2, 1), (1, 0)):
            target = message(10*retracting_uid, text="G question", channel=100*retracting_uid)
            scope = self.send_scope(target)
            async def respond(*args, target=target, retracting_uid=retracting_uid, scope=scope):
                self.chat.receive(message(target.id+1, uid=retracting_uid, text="never mind", channel=target.channel.id))
                await scope["_send_chunk"]()
                return "sent"
            self.chat.respond = respond
            self.chat.receive(target)
            await self.settle()
            self.assertEqual(target.reply.await_count, expected_sends)

    async def test_edit_or_delete_invalidates_only_the_source_and_preserves_the_next_queued_request(self):
        for action, channel in (("edit", 100), ("delete", 200)):
            first, latest = message(1, text="G old question", channel=channel), message(2, text="G new question", channel=channel)
            scopes = {m.id: self.send_scope(m) for m in (first, latest)}
            async def respond(target, *args, latest=latest, action=action, channel=channel, first=first, scopes=scopes):
                if target.id == 1:
                    self.chat.receive(latest)
                    if action == "edit":
                        self.chat.edit(channel, first.id, "different question")
                    else:
                        self.chat.delete(channel, first.id)
                await scopes[target.id]["_send_chunk"]()
                return "sent"
            self.chat.respond = respond
            self.chat.receive(first)
            await self.settle()
            first.reply.assert_not_awaited()
            latest.reply.assert_awaited_once()

    async def test_automatic_own_reply_does_not_cancel_the_next_question_but_manual_own_post_does(self):
        for automatic, channel in ((True, 100), (False, 200)):
            first = message(1, text="G first", channel=channel)
            next_question = message(2, text="G second", channel=channel)
            scope = self.send_scope(next_question)
            async def respond(target, *args, next_question=next_question, channel=channel, automatic=automatic, scope=scope):
                if target.id == 1:
                    self.chat.receive(next_question)
                    own = message(3, uid=999, text="first answer", channel=channel)
                    self.chat.observe(own)
                    if automatic:
                        self.chat.record_sent(channel, own.id)
                else:
                    await scope["_send_chunk"]()
                return "sent"
            self.chat.respond = respond
            self.chat.receive(first)
            await self.settle()
            self.assertEqual(next_question.reply.await_count, 1 if automatic else 0)

    async def test_delayed_automatic_gateway_echo_does_not_cancel_the_next_queued_question(self):
        waiting = message(2, text="G next question")
        self.chat.observe(waiting)
        self.chat.record_sent(100, 3)
        own = message(3, uid=999, text="previous answer")
        bot = N(selfbot_id=999, server_waiting={100: waiting}, channel_reply_state={100: {"state": "waiting"}},
                user_message_batches={}, event=lambda f: f)
        ns = {"bot": bot, "_server_chat": self.chat, "_get_server_chat": lambda: self.chat,
              "_server_can_reply": lambda m: True, "in_server": lambda c: True,
              "_server_channel_active": lambda m: True, "_log_dm": Mock(), "PREFIX": "!",
              "companion_memory": N(message_allowed=lambda m: True), "conversation": conversation,
              "discord": N(DMChannel=type("DM", (), {})), "emit_chat_event": Mock()}
        compile_functions({"on_message"}, ns)
        await ns["on_message"](own)
        self.assertIs(bot.server_waiting[100], waiting)
        self.assertEqual(bot.channel_reply_state[100]["state"], "waiting")
        ns["emit_chat_event"].assert_not_called()

    async def test_another_member_can_join_the_exchange_after_a_semantic_check(self):
        self.chat.consider_followup = AsyncMock(return_value=True)
        self.chat.receive(message(1, text="G what do you think?"))
        await self.settle()
        self.chat.receive(message(2, uid=2, text="what games do you know?"))
        await self.settle()
        self.assertEqual(self.sent, [(1, True), (2, True)])
        self.chat.consider_followup.assert_awaited_once()

    async def test_acknowledgements_and_people_talking_elsewhere_do_not_provoke_filler(self):
        self.chat.consider_followup = AsyncMock(return_value=False)
        self.chat.receive(message(1, text="G hi"))
        await self.settle()
        for mid, text in enumerate(("Exactly", "Lmao", "yeah", "Hey rau2"), 2):
            self.chat.receive(message(mid, uid=2, text=text))
            await self.settle()
        self.assertEqual(self.sent, [(1, True)])
        self.chat.consider_followup.assert_awaited_once()

    async def test_the_original_member_can_talk_to_somebody_else_too(self):
        self.chat.consider_followup = AsyncMock(return_value=False)
        self.chat.receive(message(1, text="G hi"))
        await self.settle()
        self.chat.receive(message(2, text="Hey rau2"))
        await self.settle()
        self.chat.consider_followup.assert_awaited_once()
        self.assertEqual(self.sent, [(1, True)])

    async def test_optional_group_routing_requires_literal_current_evidence(self):
        target = message(1, text="what games do you know?")
        for raw, expected in (({"reply": True, "quote": "what games"}, True),
                              ({"reply": True, "quote": "invented"}, False),
                              ({"reply": False, "quote": "what games"}, False)):
            with patch.object(ai, "_create_small_completion", new_callable=AsyncMock, return_value=response(json.dumps(raw))):
                self.assertEqual(await consider_followup(target, []), expected)


if __name__ == "__main__":
    unittest.main()

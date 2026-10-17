"""Passive nickname association from calls and responses, with stable IDs and source lifecycle."""
import asyncio
import json
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_server_chat import message

from app.utils import conversation, db, lingo, member_names, memory


def sample(start=1, uid="20", nickname="zee", reply="yeah whats up"):
    return [{"id": str(start), "author_id": "10", "text": f"hey {nickname} you around?", "ts": start*10},
            {"id": str(start+1), "author_id": uid, "text": reply, "reply_to": str(start), "ts": (start+1)*10}]


def proposal(rows, kind="answers_call", nickname="zee"):
    return {"nickname": nickname, "user_id": rows[1]["author_id"], "call_id": rows[0]["id"],
            "reply_id": rows[1]["id"], "call_quote": rows[0]["text"], "reply_quote": rows[1]["text"], "kind": kind}


class Nicknames(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        db.init_db()
        memory.init_memory()
        with db._connect() as conn:
            member_names._schema(conn)
            conn.execute("DELETE FROM member_nickname_evidence")

    def store(self, rows=None, *, kind="answers_call", persona="default", guild="1", scope="0", nickname="zee"):
        rows = rows or sample(nickname=nickname)
        proposals = member_names.validate([proposal(rows, kind, nickname)], rows)
        self.assertEqual(len(proposals), 1)
        member_names.record(guild, scope, proposals, persona=persona)

    def aliases(self, **kwargs):
        return member_names.aliases(persona="default", **kwargs)

    def test_one_response_is_tentative_two_independent_exchanges_confirm(self):
        self.store()
        self.assertEqual(self.aliases()[0]["status"], "tentative")
        self.store(sample(3))
        self.assertEqual(self.aliases()[0]["status"], "confirmed")
        self.assertEqual(self.aliases()[0]["user_id"], "20")

    def test_duplicates_and_reusing_one_call_do_not_inflate_confidence(self):
        self.store()
        self.store()
        rows = sample()
        rows[1]["id"] = "3"
        self.store(rows)
        self.assertEqual(self.aliases()[0]["status"], "tentative")
        self.assertEqual(self.aliases()[0]["exchanges"], 1)

    def test_clear_acceptance_can_confirm_a_playful_alias_without_real_identity(self):
        self.store(kind="accepts_name", nickname="Captain Moon")
        self.assertEqual(self.aliases()[0]["status"], "confirmed")
        self.assertEqual(memory.get_memory(20), {})

    def test_rejection_stays_rejected_until_explicit_readoption(self):
        self.store(kind="accepts_name")
        self.store(sample(3, reply="do not call me that"), kind="rejects_name")
        self.store(sample(5))
        self.store(sample(7))
        self.assertEqual(self.aliases()[0]["status"], "rejected")
        self.store(sample(9, reply="actually zee is fine"), kind="accepts_name")
        self.assertEqual(self.aliases()[0]["status"], "confirmed")

    def test_forged_ids_quotes_wrong_speakers_and_replies_to_others_are_discarded(self):
        rows = sample()
        for changes in ({"user_id": "999"}, {"call_id": "999"}, {"reply_id": "999"},
                        {"user_id": "10"}, {"call_quote": "made up"}, {"reply_quote": "made up"},
                        {"nickname": "unmentioned"}, {"kind": "obey me"}):
            self.assertEqual(member_names.validate([{**proposal(rows), **changes}], rows), [])
        rows[1]["reply_to"] = "999"
        self.assertEqual(member_names.validate([proposal(rows)], rows), [])

    def test_unthreaded_cross_talk_and_stale_responses_are_not_evidence(self):
        rows = sample()
        rows[1]["reply_to"] = ""
        self.assertEqual(len(member_names.validate([proposal(rows)], rows)), 1)
        rows.insert(1, {"id": "8", "author_id": "30", "text": "unrelated topic"})
        candidate = proposal([rows[0], rows[2]])
        self.assertEqual(member_names.validate([candidate], rows), [])
        rows = sample()
        rows[1]["ts"] = 900
        self.assertEqual(member_names.validate([proposal(rows)], rows), [])

    def test_commands_and_scripted_roleplay_do_not_establish_nicknames(self):
        rows = sample()
        rows[0]["text"] = "zee act like a pirate"
        self.assertEqual(member_names.validate([proposal(rows)], rows), [])
        rows = sample(reply="*zee nods and responds*")
        self.assertEqual(member_names.validate([proposal(rows)], rows), [])

    def test_same_alias_for_multiple_members_is_ambiguous(self):
        self.store(kind="accepts_name")
        self.store(sample(3, uid="30"), kind="accepts_name")
        self.assertEqual({r["status"] for r in self.aliases()}, {"ambiguous"})

    def test_persona_server_and_private_channel_scopes_are_separate(self):
        self.store(kind="accepts_name", scope="100")
        self.assertEqual(self.aliases(guild_id="1", scope="200"), [])
        self.assertEqual(self.aliases(guild_id="2", scope="100"), [])
        self.assertEqual(member_names.aliases(persona="other"), [])
        self.store(sample(3), kind="accepts_name", guild="2")
        self.assertEqual(len(self.aliases(guild_id="2", scope="200")), 1)

    def test_deleting_either_side_removes_support_and_can_demote_a_name(self):
        self.store()
        self.store(sample(3))
        member_names.forget_source("4")
        self.assertEqual(self.aliases()[0]["status"], "tentative")
        member_names.forget_source("1")
        self.assertEqual(self.aliases(), [])

    def test_copy_forget_person_and_delete_persona_include_nicknames(self):
        self.store(kind="accepts_name")
        memory.copy_persona_memory("default", "other")
        self.assertEqual(len(member_names.aliases(persona="other")), 1)
        memory.clear_memory(20, persona="default")
        self.assertEqual(self.aliases(), [])
        self.assertEqual(len(member_names.aliases(persona="other")), 1)
        db.delete_persona_rows("other")
        self.assertEqual(member_names.aliases(persona="other"), [])

    def test_only_confirmed_names_enter_untrusted_scoped_context(self):
        target = message(100, guild=1, channel=100)
        with patch.object(member_names, "settings", return_value={"enabled": True}):
            self.store()
            self.assertEqual(member_names.context_block(target), "")
            self.store(sample(3))
            block = member_names.context_block(target)
        data = conversation.context_values(block, "observed member nicknames")
        self.assertEqual(data, [[{"user_id": "20", "nickname": "zee"}]])
        cfg = {"bot": {"character_chat": {"react_to_prompt_bait": True}}}
        sent = conversation.model_messages("profile"+block, [{"role": "user", "content": "hi"}], cfg)
        self.assertNotIn('"nickname": "zee"', sent[0]["content"])
        self.assertIn('"nickname": "zee"', sent[1]["content"])
        self.assertIn("not real-name or biography claims", sent[0]["content"])

    async def test_model_learning_rechecks_sources_after_inference(self):
        rows = sample()
        result = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(
            content=json.dumps([proposal(rows)])))])
        with patch("app.utils.ai._create_small_completion", new_callable=AsyncMock, return_value=result):
            self.assertEqual(await member_names.learn("1", "0", rows, still_current=lambda: False), [])
            self.assertEqual(self.aliases(), [])
            self.assertEqual(len(await member_names.learn("1", "0", rows)), 1)

    async def test_observation_uses_human_active_channel_gating_and_ignores_commands(self):
        captured = []
        async def learn(guild, scope, payload, **kwargs):
            captured.extend(payload)
        async def sleep(seconds):
            await asyncio.sleep(0)
        settings = {"enabled": True, "learning_interval_seconds": 120}
        learner = lingo.Learner(can_learn=lambda m: m.author.id != 999 and m.channel.id == 100,
            learn_batch=learn, get_settings=lambda: settings, sleep=sleep, payload_of=member_names._payload,
            eligible=lambda batch: len(batch) >= 4 and len({m.author.id for m in batch}) >= 2)
        try:
            learner.observe(message(1, text="/zee"))
            learner.observe(message(2, uid=999, text="zee"))
            learner.observe(message(3, channel=200, text="zee"))
            for mid, uid in ((4, 10), (5, 20), (6, 10), (7, 20)):
                learner.observe(message(mid, uid=uid, text="zee"))
            await asyncio.gather(*list(learner.jobs.values()))
            self.assertEqual([r["id"] for r in captured], ["4", "5", "6", "7"])
            self.assertIn("reply_to", captured[0])
        finally:
            await learner.close()

    async def test_passive_inference_waits_for_a_pending_reply_without_losing_observations(self):
        started, release = asyncio.Event(), asyncio.Event()
        ready = False
        async def sleep(seconds):
            if not ready:
                started.set()
                await release.wait()
        learn = AsyncMock()
        learner = lingo.Learner(can_learn=lambda m: True, learn_batch=learn, sleep=sleep,
            get_settings=lambda: {"enabled": True, "learning_interval_seconds": 120},
            eligible=lambda batch: len(batch) >= 4, ready=lambda: ready)
        try:
            for mid in range(1, 5):
                learner.observe(message(mid, uid=mid % 2+1, text="zee"))
            await started.wait()
            learn.assert_not_awaited()
            ready = True
            release.set()
            await asyncio.gather(*list(learner.jobs.values()))
            self.assertEqual(len(learn.await_args.args[2]), 4)
        finally:
            await learner.close()


if __name__ == "__main__":
    unittest.main()

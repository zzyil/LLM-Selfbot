"""Unified member memories, server attitudes and evidence-grounded replies, without Discord."""
import types
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_server_chat import message

from app.utils import (
    ai,
    configstore,
    conversation,
    db,
    member_context,
    relationships,
    reply_grounding,
    social,
)
from app.utils.memory import clear_memory, copy_persona_memory, get_memory, set_memory
from app.utils.server_chat import ServerChat
from app.web.routes import chat_routes

N = types.SimpleNamespace
SERVER_ONLY = {"enabled": False, "server": {"enabled": True}}


def source(mid, uid=1, *, guild=10, channel=100, public=True, text="thanks for helping me"):
    m = message(mid, uid=uid, text=text, guild=guild, channel=channel, name=f"Member {uid}")
    m.channel.name = "general"
    if m.guild is not None:
        m.guild.name = "Community"
        m.guild.default_role = N()
        m.channel.permissions_for = lambda role: N(view_channel=public)
    return m


def completion(text):
    return N(choices=[N(message=N(content=text))])


class MemberMemories(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        db.init_db()
        with db._connect() as conn:
            member_context._schema(conn)
            relationships._schema(conn)
            for table in ("member_contexts", "member_context_messages", "companion_relationships", "companion_relationship_events", "user_memory"):
                conn.execute(f"DELETE FROM {table}")
        app = FastAPI()
        app.include_router(chat_routes.router)
        self.client = TestClient(app)

    def test_one_user_has_both_dm_and_server_contexts_without_duplicate_events(self):
        m = source(1)
        self.assertTrue(member_context.observe(m))
        self.assertFalse(member_context.observe(m))
        member_context.observe(source(2, guild=None, channel=200))
        item = member_context.users()["1"]
        self.assertEqual(item["messages"], 2)
        self.assertEqual({c["kind"] for c in item["contexts"]}, {"dm", "server"})
        self.assertEqual(get_memory(1), {})

    def test_server_only_people_appear_without_requiring_a_biography_fact(self):
        member_context.observe(source(1))
        data = self.client.get("/api/memory?persona=default").json()
        self.assertEqual(data["users"][0]["user_id"], "1")
        self.assertEqual(data["users"][0]["facts"], {})
        self.assertEqual(data["users"][0]["contexts"][0]["guild_id"], "10")

    async def test_server_relationship_learning_works_when_dm_companion_mode_is_off(self):
        with patch.object(relationships, "settings", return_value=SERVER_ONLY), \
                patch.object(ai, "_create_small_completion", AsyncMock(return_value=completion(
                    '{"stance":"friendly","quote":"thanks for helping"}'))):
            await relationships.learn_interaction(1, "character", 1, "thanks for helping me", message=source(1))
        relation = relationships.get_relationship(1)
        self.assertEqual(relation["interactions"], 1)
        self.assertEqual(relationships.get_relationship(1, now=1000)["affinity"], 0.06)
        event = member_context.interactions(1)[0]
        self.assertEqual((event["guild_id"], event["channel_id"], event["kind"]), ("10", "100", "server"))

    def test_public_prompt_cannot_recall_private_dm_or_other_server_events(self):
        for mid, m in enumerate((source(1), source(2, guild=None, channel=200),
                                 source(3, public=False, channel=300), source(4, guild=20)), 1):
            member_context.observe(m)
            relationships.record_interaction(1, "character", mid, "friendly", f"source {mid}")
        events = member_context.interactions(1, message=source(5))
        self.assertEqual([e["message_id"] for e in events], ["1"])
        self.assertEqual(len(member_context.interactions(1)), 4)  # owner can inspect all sources
        private = member_context.interactions(1, message=source(6, public=False, channel=300))
        self.assertEqual({e["message_id"] for e in private}, {"1", "3"})

    def test_persona_isolation_copy_and_whole_user_forgetting(self):
        member_context.observe(source(1))
        self.assertEqual(member_context.users("other"), {})
        set_memory(1, "hobby", "painting")
        copy_persona_memory("default", "other")
        self.assertIn("1", member_context.users("other"))
        clear_memory(1)
        self.assertEqual(member_context.users(), {})
        self.assertIn("1", member_context.users("other"))
        self.assertEqual(get_memory(1, "other")["hobby"], "painting")

    def test_making_a_channel_public_does_not_promote_its_old_private_events(self):
        member_context.observe(source(1, public=False, channel=300))
        relationships.record_interaction(1, "character", 1, "friendly", "old private exchange")
        member_context.observe(source(2, public=True, channel=300))
        self.assertEqual(member_context.interactions(1, message=source(3)), [])

    def test_deleting_a_persona_clears_contexts_and_relationships(self):
        member_context.observe(source(1), "other")
        relationships.record_interaction(1, "character", 1, "friendly", "thanks for helping", persona="other")
        db.delete_persona_rows("other")
        self.assertEqual(member_context.users("other"), {})
        self.assertEqual(relationships.list_relationships(1, "other"), [])

    def test_relationship_dashboard_includes_sources_and_server_filter_metadata(self):
        member_context.observe(source(1))
        relationships.record_interaction(1, "character", 1, "hostile", "you are a clown")
        with patch.object(configstore, "effective", return_value={"bot": {"companion": SERVER_ONLY}}):
            data = self.client.get("/api/relationships?persona=default").json()
        self.assertTrue(data["learning_enabled"])
        self.assertEqual(data["servers"], [{"id": "10", "name": "Community"}])
        self.assertEqual(data["people"][0]["interactions"][0]["quote"], "you are a clown")
        self.assertEqual(data["people"][0]["relationship"]["interactions"], 1)

    def test_relationship_context_works_in_server_mode_and_marks_evidence_as_data(self):
        m = source(1)
        member_context.observe(m)
        relationships.record_interaction(1, "character", 1, "friendly", "thanks for helping")
        with patch.object(relationships, "settings", return_value=SERVER_ONLY):
            block = relationships.context_block(1, "character", [], message=m)
        data = conversation.context_values(block, "relationship with current member")[0]
        self.assertEqual(data["recent_interactions"][0]["message_id"], "1")

    def test_invalidating_a_source_removes_its_social_memory_and_attitude_change(self):
        member_context.observe(source(1))
        relationships.record_interaction(1, "character", 1, "hostile", "you are a clown")
        relationships.forget_source(1)
        self.assertEqual(member_context.interactions(1), [])
        relation = relationships.get_relationship(1)
        self.assertEqual((relation["interactions"], relation["affinity"]), (0, 0))


class QuietLearning(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000
        self.config = {"learn_from_chat": True, "learning_min_messages": 4, "learning_interval_seconds": 120}
        self.learn = AsyncMock()
        self.chat = ServerChat(self_id=lambda: 999, get_config=lambda: self.config, respond=AsyncMock(),
            learn=self.learn, seed=AsyncMock(return_value=[]), can_reply=lambda m: True,
            clock=lambda: self.now, wall_clock=lambda: self.now)

    async def asyncTearDown(self):
        await self.chat.close()

    async def drain(self):
        if self.chat.learning_task:
            await self.chat.learning_task

    async def test_one_passive_disclosure_is_flushed_without_a_fourth_message(self):
        self.chat.receive(source(1, text="I work in a bakery"))
        self.learn.assert_not_awaited()
        self.now += 16
        self.chat._flush_due_learning()
        await self.drain()
        self.assertEqual(self.learn.await_args.args[1], "I work in a bakery")

    async def test_cooldown_tail_is_flushed_without_another_member_message(self):
        self.config["learning_min_messages"] = 1
        self.chat.receive(source(1, text="I work in a bakery"))
        await self.drain()
        self.now += 10
        self.chat.receive(source(2, text="I also paint"))
        self.now += 111
        self.chat._flush_due_learning()
        await self.drain()
        self.assertEqual(self.learn.await_count, 2)
        self.assertIn("I also paint", self.learn.await_args.args[1])

    async def test_queued_batches_merge_instead_of_losing_the_previous_fact(self):
        self.config["learning_min_messages"] = 1
        self.chat.receive(source(1, text="I work in a bakery"))
        self.now += 121
        self.chat.receive(source(2, text="I also paint"))
        await self.drain()
        self.assertIn("I work in a bakery", self.learn.await_args.args[1])
        self.assertIn("I also paint", self.learn.await_args.args[1])

    async def test_disabling_server_learning_discards_a_waiting_tail(self):
        self.chat.receive(source(1, text="I work in a bakery"))
        self.config["learn_from_chat"] = False
        self.now += 16
        self.chat._flush_due_learning()
        await self.drain()
        self.learn.assert_not_awaited()


class Grounding(unittest.IsolatedAsyncioTestCase):
    def test_social_reaction_receives_the_actual_attributed_channel_context(self):
        history = [{"role": "user", "content": "Tech [user 1]: insult"},
                   {"role": "user", "content": "Zzy [user 2]: someone else called you that"}]
        assessment = {"kind": "correction", "category": "other", "direction": "other", "cue": "someone else", "repeated": False}
        messages = social.reply_messages("profile", assessment, history, "someone else called you that")
        self.assertIn("Tech [user 1]", str(messages))
        self.assertIn("Zzy [user 2]", str(messages))
        self.assertIn("Admit an unsupported accusation", messages[0]["content"])

    def test_grounding_requires_an_exact_error_in_the_draft(self):
        draft = "you wrote in all caps"
        self.assertEqual(social.grounding_issue('{"grounded":false,"issue":"invented_event","quote":"all caps"}', draft), "invented_event")
        self.assertIsNone(social.grounding_issue('{"grounded":false,"issue":"invented_event","quote":"made up"}', draft))

    async def test_false_attribution_gets_one_revision_and_a_second_source_check(self):
        instructions = "profile"+conversation.discord_context(source(1))
        checks = [completion('{"grounded":false,"issue":"wrong_speaker","quote":"you said it"}'),
                  completion('{"grounded":true,"issue":"none","quote":""}')]
        with patch.object(social, "enabled", return_value=True), \
                patch.object(ai, "_create_small_completion", AsyncMock(side_effect=checks)) as judge, \
                patch.object(ai, "_create_completion", AsyncMock(return_value=completion("yeah, i mixed you two up"))) as actor:
            result = await reply_grounding.check("that was someone else", instructions, [{"role": "user", "content": "that was someone else"}], "you said it")
        self.assertEqual(result, "yeah, i mixed you two up")
        self.assertEqual(judge.await_count, 2)
        actor.assert_awaited_once()

    async def test_a_dm_does_not_pay_for_the_server_source_check(self):
        with patch.object(social, "enabled", return_value=True), patch.object(ai, "_create_small_completion", AsyncMock()) as judge:
            self.assertEqual(await reply_grounding.check("hello", "profile"+conversation.discord_context(source(1, guild=None)), [], "hey"), "hey")
        judge.assert_not_awaited()

    async def test_another_unsupported_revision_is_not_published(self):
        instructions = "profile"+conversation.discord_context(source(1))
        raw = '{"grounded":false,"issue":"invented_event","quote":"all caps"}'
        with patch.object(social, "enabled", return_value=True), \
                patch.object(ai, "_create_small_completion", AsyncMock(return_value=completion(raw))), \
                patch.object(ai, "_create_completion", AsyncMock(return_value=completion("you were still using all caps"))):
            self.assertEqual(await reply_grounding.check("that wasn't caps", instructions, [], "you wrote in all caps"), "")


if __name__ == "__main__":
    unittest.main()

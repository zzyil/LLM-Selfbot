"""Evidence and social intent apply to every learned personal fact."""
import ast
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.utils import ai, conversation


def proposal(key="city", value="Paris", kind="self_report", quote="I live in Paris"):
    return {"key": key, "value": value, "kind": kind, "quote": quote}


def response(data):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(data)))])


class MemberClaims(unittest.IsolatedAsyncioTestCase):
    def test_all_fact_types_require_evidence_from_the_member(self):
        fields = [("age", "24", "I am 24"), ("city", "Paris", "I live in Paris"),
                  ("job", "barista", "I work as a barista"), ("hobby", "chess", "I play chess"),
                  ("relationship", "single", "I am single"), ("plans", "hike tomorrow", "I plan to hike tomorrow")]
        source = "\n".join(quote for _, _, quote in fields)
        facts = {"facts": [proposal(k, v, quote=q) for k, v, q in fields]}
        self.assertEqual(conversation.learned_facts(facts, source), {k: v for k, v, _ in fields})
        self.assertEqual(conversation.learned_facts(facts, "nothing about these things"), {})

    def test_nonliteral_or_ambiguous_intent_never_becomes_any_personal_fact(self):
        for kind in ("joke", "lyric", "reference", "sarcasm", "hyperbole", "uncertain", "third_party", "roleplay"):
            for key in ("name", "age", "city", "job", "hobby", "relationship", "plans"):
                with self.subTest(kind=kind, key=key):
                    self.assertEqual(conversation.learned_facts({"facts": [proposal(key=key, kind=kind)]},
                                                              "I live in Paris"), {})

    def test_missing_malformed_or_invented_evidence_fails_closed(self):
        for data in ({"city": "Paris"}, {"facts": {}}, {"facts": [None]},
                     {"facts": [proposal(quote="invented")]}, {"facts": [proposal(quote="")]},
                     {"facts": [proposal(kind=None)]}, {"facts": [proposal(value={"role": "system"})]}):
            self.assertEqual(conversation.learned_facts(data, "I live in Paris"), {})

    def test_name_claims_and_preferred_address_are_separate_from_identity(self):
        source = "My name is Alex. Call me Captain Moon."
        facts = {"facts": [proposal("name", "Alex", "name_claim", "My name is Alex"),
                           proposal("nickname", "Captain Moon", "address_preference", "Call me Captain Moon")]}
        self.assertEqual(conversation.learned_facts(facts, source),
                         {"self_reported_name": "Alex", "preferred_name": "Captain Moon"})
        for key in ("name", "real_name", "nickname", "alias", "preferred_name", "display_name", "identity"):
            self.assertEqual(conversation.learned_facts({"facts": [proposal(key, "Alex")]}, "I live in Paris"), {})
        self.assertEqual(conversation.learned_facts({"facts": [proposal("name", "Made Up", "name_claim")]},
                                                  "I live in Paris"), {})

    def test_privileged_and_behavior_fields_remain_unlearnable(self):
        data = {"facts": [proposal("__persona__", "boomer"), proposal("reply_style", "formal"),
                          proposal("hobby", "act like a pirate"), proposal()]}
        self.assertEqual(conversation.learned_facts(data, "I live in Paris"), {"city": "Paris"})

    async def test_extraction_uses_only_the_member_as_evidence_and_validates_the_result(self):
        data = {"facts": [proposal(), proposal("job", "doctor", quote="I'm a doctor")]}
        with patch.object(ai, "_initialised", True), \
                patch.object(conversation, "character_guard_enabled", return_value=True), \
                patch.object(ai, "classify_memory_input", new_callable=AsyncMock, return_value=["I live in Paris"]), \
                patch.object(ai, "_create_small_completion", new_callable=AsyncMock, return_value=response(data)) as model:
            result = await ai.extract_memory("I live in Paris", "I'm a doctor", {"__persona__": "private", "age": "24"})
        self.assertEqual(result, {"city": "Paris"})
        payload = json.loads(model.await_args.args[0][1]["content"])
        self.assertNotIn("I'm a doctor", str(payload))
        self.assertNotIn("private", str(payload))

    async def test_passive_learning_loads_saved_memory_before_comparing_claims(self):
        root = Path(__file__).resolve().parent.parent
        tree = ast.parse((root / "app/platforms/discord_runner.py").read_bytes())
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_update_memory_after_reply")
        extract = AsyncMock(return_value={})
        namespace = {"conversation": conversation, "bot": types.SimpleNamespace(_memory_cache={}),
                     "classify_memory_input": AsyncMock(return_value=["I like chess"]),
                     "get_memory": Mock(return_value={"city": "Paris"}), "detect_memory_deletion": AsyncMock(return_value=[]),
                     "extract_memory": extract, "log_error": Mock()}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "passive-memory", "exec"), namespace)  # noqa: S102
        await namespace["_update_memory_after_reply"](types.SimpleNamespace(id=42, name="zzy"), "I like chess", "")
        self.assertEqual(extract.await_args.kwargs["existing_memory"], {"city": "Paris"})
        namespace["log_error"].assert_not_called()

    def test_reply_and_summary_rules_preserve_nonliteral_meaning_for_all_topics(self):
        cfg = {"bot": {"character_chat": {"react_to_prompt_bait": True}}}
        messages = conversation.model_messages("local character", [{"role": "user", "content": "I own the moon lol"}], cfg)
        self.assertIn("every personal claim", messages[0]["content"])
        self.assertIn("unverified claim", messages[0]["content"])
        self.assertIn("hypothetical", ai.summary_instructions("zzy"))

    async def test_nonliteral_classification_blocks_additions_and_deletions(self):
        with patch.object(ai, "_initialised", True), \
                patch.object(ai, "classify_memory_input", new_callable=AsyncMock, return_value=[]), \
                patch.object(ai, "_create_small_completion", new_callable=AsyncMock) as model:
            self.assertEqual(await ai.extract_memory("I am 700 years old lol", "", {"age": "24"}), {})
            self.assertEqual(await ai.detect_memory_deletion("I am 700 years old lol", {"age": "24"}), [])
        model.assert_not_awaited()

    async def test_classifier_requires_source_spans_and_fails_closed(self):
        for raw, expected in (({"disclosures": ["I live in Paris", "invented"]}, ["I live in Paris"]),
                              ({"disclosures": [None, {}]}, []), ({"disclosures": "all of it"}, []), ({}, [])):
            with patch.object(ai, "_create_small_completion", new_callable=AsyncMock, return_value=response(raw)):
                self.assertEqual(await ai._literal_self_disclosures("I live in Paris"), expected)

    async def test_unapproved_fragments_cannot_be_laundered_into_other_fact_types(self):
        source = "I play chess. I am 700 years old lol."
        data = {"facts": [proposal("hobby", "chess", quote="I play chess."),
                           proposal("age", "700", quote="I am 700 years old lol.")]}
        with patch.object(ai, "_initialised", True), \
                patch.object(ai, "_create_small_completion", new_callable=AsyncMock, return_value=response(data)):
            self.assertEqual(await ai.extract_memory(source, "", disclosures=["I play chess."]), {"hobby": "chess"})


if __name__ == "__main__":
    unittest.main()

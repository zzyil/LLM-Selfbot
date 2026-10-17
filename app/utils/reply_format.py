"""A visible-text envelope for local models, never a reasoning or planning channel."""
import json
from contextlib import contextmanager
from contextvars import ContextVar

from app.utils.output_guard import OutputBlocked

_ACTIVE = ContextVar("visible_reply_fragments", default=False)
MAX_PARTS, MAX_CHARS = 10, 120
MAX_REPLY_CHARS = MAX_PARTS * MAX_CHARS + MAX_PARTS - 1

INSTRUCTIONS = (
    "\n\n[VISIBLE MESSAGE FORMAT: Return the JSON object required by the transport schema. "
    "The messages array contains ONLY visible Discord text, never analysis, plans, labels or "
    "instructions. Each item is one short ordinary thought in your own texting voice, not a "
    "polished sentence you have to finish. One item can be enough for a small reply. If you yap, "
    "several items can circle the same concrete point. Keep the whole reply on this actual topic. "
    "Use only concrete details established in the conversation; don't invent another member's "
    "history, actions or requests to fill a fragment. "
    "Don't turn each item into a new argument, metaphor or rhetorical question. No headings or "
    "essay transitions. Don't add a tidy conclusion. Native reaction tags, if any, go at the end "
    "of the last item. The app groups these within its existing reply/message limits. "
    "Keep all privacy and character boundaries.]"
)

SCHEMA = {"type": "json_schema", "json_schema": {
    "name": "discord_reply", "strict": True, "schema": {
        "type": "object", "properties": {"messages": {
            "type": "array", "items": {"type": "string", "minLength": 1, "maxLength": MAX_CHARS},
            "minItems": 1, "maxItems": MAX_PARTS}},
        "required": ["messages"], "additionalProperties": False}}}


def enabled(config):
    from app.utils import providers
    style = ((config.get("bot") or {}).get("message_style") or {})
    return bool(style.get("compose_in_bursts", False) and providers.local_only(config))


@contextmanager
def scope(active):
    token = _ACTIVE.set(active)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def active():
    return _ACTIVE.get()


def long_reaction(assessment):
    return bool(active() and assessment.get("repeated") is True
                and assessment.get("kind") in {"insult", "true_trait_jab", "false_claim"})


def request_options():
    return {"response_format": SCHEMA} if active() else {}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def decode(content):
    """Reject malformed/extra fields; only visible strings enter the normal output gate."""
    try:
        value = json.loads(content, object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or set(value) != {"messages"}:
            raise ValueError("invalid fields")
        parts = value["messages"]
        if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS:
            raise ValueError("invalid count")
        if any(not isinstance(part, str) or not part.strip() or len(part) > MAX_CHARS for part in parts):
            raise ValueError("invalid text")
    except (TypeError, ValueError):
        raise OutputBlocked("invalid_reply_envelope") from None
    return "\n".join(parts)

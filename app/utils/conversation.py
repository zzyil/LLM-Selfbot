"""Character reactions and bounded, configurable conversation history."""
import random
import re


def _config(config):
    if config is None:
        from app.utils.helpers import load_config
        return load_config()
    return config


def _integer(value, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def history_settings(config=None):
    raw = ((_config(config).get("bot") or {}).get("history") or {})
    maximum = _integer(raw.get("max_messages"), 30, 8, 1000)
    threshold = _integer(raw.get("summarize_after"), min(20, maximum), 8, maximum)
    return {
        "max_messages": maximum,
        "summarize_after": threshold,
        "keep_recent": _integer(raw.get("keep_recent"), 6, 2, threshold - 2),
        "max_bytes": _integer(raw.get("max_bytes"), 32768, 4096, 1048576),
    }


def _size(turn):
    # UTF-8 bytes give a conservative bound without assuming an English token/character ratio.
    return len(str(turn.get("content") or "").encode("utf-8")) + 64


def bound_history(history, instructions="", config=None, *, max_messages=None):
    settings = history_settings(config)
    maximum = settings["max_messages"] if max_messages is None else max_messages
    budget = max(64, settings["max_bytes"] - len(instructions.encode("utf-8")))
    kept = []
    for turn in reversed(history[-maximum:]):
        size = _size(turn)
        if size > budget:
            if not kept:
                last = dict(turn)
                content = str(turn.get("content") or "").encode("utf-8")
                last["content"] = content[-(budget - 64):].decode("utf-8", errors="ignore") if budget > 64 else ""
                kept.append(last)
            break
        kept.append(dict(turn))
        budget -= size
    return list(reversed(kept))


async def prepare_history(history, instructions, summarize, config=None):
    settings = history_settings(config)
    bounded = bound_history(history, instructions, config)
    if len(history) > settings["summarize_after"] or bounded != history:
        try:
            compressed = await summarize(bounded, instructions)
            if compressed:
                bounded = compressed
        except Exception:  # noqa: BLE001 - optional compression must not prevent a reply
            return bound_history(history, instructions, config)
    return bound_history(bounded, instructions, config)


_OVERRIDE = re.compile(
    r"\b(?:ignore|forget|disregard|override)\s+"
    r"(?:(?:all|any|the)\s+)?(?:your|previous|prior|original|earlier|system)\s+"
    r"(?:(?:system|character|safety|previous|prior)\s+){0,2}"
    r"(?:prompts?|instructions?|rules?|persona|programming)\b", re.IGNORECASE)
_BREAK_CHARACTER = re.compile(
    r"\b(?:break|drop|abandon)\s+(?:your\s+)?character\b|"
    r"\b(?:answer|reply|respond|speak|talk|act|write|give me|tell me|teach me)\b"
    r"[^.!?\n]{0,240}\bout[ -]+of[ -]+character\b", re.IGNORECASE)
_QUOTED = re.compile(r"```.*?```|`[^`\n]*`|\"[^\"\n]*\"|“[^”\n]*”|(?m:^>[^\n]*)", re.DOTALL)
_REPORT = re.compile(r"^(?:my friend|someone|a user|they|he|she)\s+"
                     r"(?:said|says|told|wrote|posted|asked)\b", re.IGNORECASE)


def bait_reply(prompt, config=None):
    """React to explicit persona overrides, only for characters that opt in.

    This is a narrow conversational rule, not a general prompt-injection detector.
    Quoted examples and reported speech should still be available for discussion.
    """
    settings = ((_config(config).get("bot") or {}).get("character_chat") or {})
    if not settings.get("react_to_prompt_bait", False):
        return None
    text = _QUOTED.sub("", prompt or "").strip()
    if _REPORT.match(text) or not (_OVERRIDE.search(text) or _BREAK_CHARACTER.search(text)):
        return None
    replies = settings.get("bait_replies")
    if not isinstance(replies, list):
        replies = []
    replies = [s.strip()[:160] for s in replies if isinstance(s, str) and s.strip()]
    return random.choice(replies or ["wtf???"])

"""Check concrete conversational claims without judging a character's tone."""
import asyncio

from app.utils import conversation, social


async def check(prompt, instructions, history, text):
    if not text or not social.enabled():
        return text
    contexts = conversation.context_values(instructions, "current Discord conversation")
    if not any(isinstance(c, dict) and c.get("kind") == "server" for c in contexts):
        return text
    from app.utils.ai import (
        _answer_of,
        _create_completion,
        _create_small_completion,
        _reply_token_cap,
    )
    from app.utils.humanize import strip_meta
    try:
        for attempt in range(2):
            checked = await asyncio.wait_for(_create_small_completion(
                social.grounding_messages(prompt, text, history, instructions), max_tokens=500, temperature=0), timeout=20)
            issue = social.grounding_issue(_answer_of(checked), text)
            if not issue:
                return text
            if attempt:
                return ""
            feedback = ("\n[REVISE THIS REPLY: A separate source check found " + issue + ". "
                        "Use only the attributed channel messages. Don't transfer another person's words "
                        "to the current speaker, invent requests or formatting, or justify an earlier "
                        "mistake. If corrected, briefly accept the actual correction. Keep your voice "
                        "and answer the current message in one or two sentences.]\n")
            generated = await _create_completion(conversation.model_messages(
                instructions + feedback, history), max_tokens=_reply_token_cap())
            text = strip_meta(_answer_of(generated))
            if not text:
                return ""
    except Exception:  # noqa: BLE001 - optional verification must not strand a usable reply
        return text
    return text

"""Bounded shared conversation history, reply scheduling and per-author learning for guilds."""
import asyncio
import math
import random
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from app.utils import conversation


def _number(config, key, default, minimum, maximum):
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value)) if math.isfinite(value) else default


def _text(message):
    return (message.content or "").strip()[:2000]


def _turn(message, self_id):
    if message.author.id == self_id:
        return {"role": "assistant", "content": _text(message) or "[attachment]"}
    # Stable IDs distinguish members with identical nicknames. Names can't forge a new turn.
    name = str(getattr(message.author, "display_name", None) or message.author.name)
    name = re.sub(r"[\r\n:]", " ", name)[:60]
    return {"role": "user", "content": f"{name} [user {message.author.id}]: "
            + (_text(message) or "[attachment]")}


@dataclass
class Channel:
    messages: deque = field(default_factory=lambda: deque(maxlen=40))
    ids: deque = field(default_factory=lambda: deque(maxlen=200))
    earlier: list = field(default_factory=list)
    seeded: bool = False
    pending: tuple | None = None
    task: asyncio.Task | None = None
    current: object = None
    sending: bool = False
    first_message: object = None
    last_attempt: float = float("-inf")
    last_reply: float = float("-inf")
    last_author: int | None = None
    followups: int = 0
    learning: OrderedDict = field(default_factory=OrderedDict)


class ServerChat:
    def __init__(self, *, self_id, get_config, respond, learn, seed, can_reply,
                 scheduled=lambda message, delay: None, report=lambda exc: None,
                 clock=time.monotonic, wall_clock=time.time, rng=random.random):
        self.self_id, self.get_config = self_id, get_config
        self.respond, self.learn, self.seed = respond, learn, seed
        self.can_reply, self.report, self.clock, self.rng = can_reply, report, clock, rng
        self.scheduled, self.wall_clock = scheduled, wall_clock
        self.channels = OrderedDict()
        self.reply_tasks = set()
        self.reply_slot = asyncio.Semaphore(1)
        self.learning_queue = OrderedDict()
        self.learned_at = OrderedDict()
        self.learning_task = None
        self.closed = False

    def _history_limit(self):
        return int(_number(self.get_config() or {}, "history_messages", 40, 8, 1000))

    def _channel(self, message):
        channel_id = message.channel.id
        limit = self._history_limit()
        state = self.channels.get(channel_id)
        if state is None:
            # Keep only a bounded number of channels, never evicting an active reply worker.
            if len(self.channels) >= 128:
                idle = next((key for key, value in self.channels.items()
                             if value.task is None or value.task.done()), None)
                if idle is None:
                    return None
                self.channels.pop(idle)
            state = self.channels[channel_id] = Channel(first_message=message)
        if state.messages.maxlen != limit:
            state.messages = deque(state.messages, maxlen=limit)
            state.ids = deque(state.ids, maxlen=max(200, limit * 2))
        self.channels.move_to_end(channel_id)
        return state

    def observe(self, message):
        if self.closed:
            return None
        state = self._channel(message)
        if state is None or message.id in state.ids:
            return None
        state.ids.append(message.id)
        state.messages.append(message)
        return state

    def _reason(self, message, state, directed, config):
        if directed:
            return "direct"
        aliases = config.get("names", ["G"])
        if isinstance(aliases, list) and config.get("reply_to_name", True):
            for alias in aliases[:10]:
                if isinstance(alias, str) and alias.strip() and re.search(
                    r"(?<!\w)" + re.escape(alias.strip()) + r"(?!\w)", _text(message), re.IGNORECASE
                ):
                    return "direct"
        reference = getattr(message, "reference", None)
        other_author = getattr(getattr(reference, "resolved", None), "author", None)
        # A reply to somebody else's message, including an unresolved reply, isn't our follow-up.
        talking_elsewhere = reference is not None and (
            other_author is None or other_author.id != self.self_id())
        mentions = getattr(message, "mentions", [])
        talking_elsewhere = talking_elsewhere or any(m.id != self.self_id() for m in mentions)
        if not talking_elsewhere and state.pending is not None \
                and state.pending[0].author.id == message.author.id \
                and state.pending[1] != "ambient":
            return state.pending[1]
        if not talking_elsewhere and state.current is not None \
                and state.current.author.id == message.author.id and state.followups < 2:
            return "followup"
        followup_seconds = _number(config, "followup_seconds", 45, 0, 300)
        if not talking_elsewhere and message.author.id == state.last_author \
                and self.clock()-state.last_reply < followup_seconds and state.followups < 2:
            return "followup"
        chance = _number(config, "ambient_reply_probability", 0, 0, 1)
        if not talking_elsewhere and self.rng() < chance:
            return "ambient"
        return None

    def receive(self, message, *, directed=False):
        """Observe all accepted members, but schedule only relevant response opportunities."""
        state = self.observe(message)
        if state is None:
            return False
        if message.author.id == self.self_id():
            return True
        config = self.get_config()
        reason = self._reason(message, state, directed, config)
        if config.get("learn_from_chat", True) and _text(message):
            self._queue_learning(message, state, force=reason in {"direct", "followup"})
        if reason is None or not self.can_reply(message):
            return True
        # One pending response for the whole channel, even when several members address it.
        # Ambient chatter never replaces a direct request waiting on inference.
        if state.pending is None or reason != "ambient" or state.pending[1] == "ambient":
            state.pending = (message, reason)
            wait = _number(config, "batch_wait_seconds", 3, 0, 30)
            cooldown = _number(config, "reply_cooldown_seconds", 20, 0, 300)
            self.scheduled(message, max(wait, state.last_attempt+cooldown-self.clock()))
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(self._reply_loop(state), name="server-conversation")
            self.reply_tasks.add(state.task)
            state.task.add_done_callback(self.reply_tasks.discard)
        return True

    async def _reply_loop(self, state):
        try:
            while state.pending is not None:
                config = self.get_config()
                delay = _number(config, "batch_wait_seconds", 3, 0, 30)
                cooldown = _number(config, "reply_cooldown_seconds", 20, 0, 300)
                delay = max(delay, state.last_attempt+cooldown-self.clock())
                await asyncio.sleep(delay)
                async with self.reply_slot:
                    pending, state.pending = state.pending, None
                    if pending is None:
                        continue
                    message, reason = pending
                    if not self.can_reply(message):
                        continue
                    if reason == "followup" and state.followups >= 2:
                        continue
                    created_at = getattr(message, "created_at", None)
                    max_age = _number(config, "max_reply_age_seconds", 120, 10, 600)
                    if created_at is not None and self.wall_clock()-created_at.timestamp() > max_age:
                        continue
                    state.last_attempt = self.clock()
                    state.current = message
                    if not state.seeded:
                        state.seeded = True
                        try:
                            state.earlier = (await self.seed(state.first_message))[-self._history_limit():]
                        except Exception as exc:  # noqa: BLE001 - isolate and report transport callbacks
                            self.report(exc)
                    history = self.history(message.channel.id)
                    reply = await self.respond(message, history, reason != "ambient")
                    if reply:
                        state.last_reply = self.clock()
                        state.last_author = message.author.id
                        state.followups = state.followups+1 if reason == "followup" else 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one failed channel must not stop the worker
            self.report(exc)
        finally:
            if state.task is asyncio.current_task():
                state.task = None
                state.current = None
                state.sending = False

    def history(self, channel_id):
        state = self.channels.get(channel_id)
        if state is None:
            return []
        return conversation.bound_history(
            state.earlier + [_turn(m, self.self_id()) for m in state.messages],
            max_messages=self._history_limit())

    def cancel(self, channel_id=None, user_id=None):
        stopped = False
        for key, state in self.channels.items():
            if channel_id is not None and key != channel_id:
                continue
            targets = [state.current, state.pending[0] if state.pending else None]
            if user_id is not None and not any(m and m.author.id == user_id for m in targets):
                continue
            if state.pending is not None or (state.task is not None and not state.task.done()):
                stopped = True
                state.pending = None
                if state.task is not None:
                    state.task.cancel()
                    state.task = None
                state.current = None
                state.sending = False
        return stopped

    def mark_sending(self, channel_id):
        state = self.channels.get(channel_id)
        if state is not None and state.current is not None:
            state.sending = True

    def is_sending(self, channel_id):
        state = self.channels.get(channel_id)
        return state is not None and state.sending

    def has_pending(self, channel_id):
        state = self.channels.get(channel_id)
        return state is not None and state.pending is not None

    def _queue_learning(self, message, state, *, force):
        config = self.get_config()
        user_id = message.author.id
        batch = state.learning.setdefault(user_id, deque(maxlen=12))
        batch.append(message)
        state.learning.move_to_end(user_id)
        if len(state.learning) > 64:
            state.learning.popitem(last=False)
        key = (message.guild.id, user_id)
        minimum = int(_number(config, "learning_min_messages", 4, 1, 12))
        cooldown = _number(config, "learning_interval_seconds", 120, 10, 86400)
        if (not force and len(batch) < minimum) \
                or self.clock()-self.learned_at.get(key, float("-inf")) < cooldown:
            return
        if len(self.learning_queue) >= 32:
            return
        self.learning_queue[key] = list(batch)
        state.learning.pop(user_id, None)
        self.learned_at[key] = self.clock()
        self.learned_at.move_to_end(key)
        if len(self.learned_at) > 4096:
            self.learned_at.popitem(last=False)
        if self.learning_task is None or self.learning_task.done():
            self.learning_task = asyncio.create_task(self._learn_loop(), name="server-memory")

    async def _learn_loop(self):
        try:
            while self.learning_queue:
                _, messages = self.learning_queue.popitem(last=False)
                if self.can_reply(messages[-1]):
                    try:
                        # Every entry belongs to this exact author; other members never enter the extractor.
                        await self.learn(messages[-1].author, "\n".join(_text(m) for m in messages))
                    except Exception as exc:  # noqa: BLE001 - isolate and report model failures
                        self.report(exc)
        finally:
            self.learning_task = None

    def edit(self, channel_id, message_id, content):
        state = self.channels.get(channel_id)
        if state:
            for message in state.messages:
                if message.id == message_id:
                    message.content = content[:2000]

    def delete(self, channel_id, message_id):
        state = self.channels.get(channel_id)
        if state:
            if state.current is not None and state.current.id == message_id:
                self.cancel(channel_id)
            state.messages = deque((m for m in state.messages if m.id != message_id), maxlen=state.messages.maxlen)
            if state.pending and state.pending[0].id == message_id:
                state.pending = None
            for batch in state.learning.values():
                for message in list(batch):
                    if message.id == message_id:
                        batch.remove(message)
            for key, messages in list(self.learning_queue.items()):
                self.learning_queue[key] = [m for m in messages if m.id != message_id]
                if not self.learning_queue[key]:
                    self.learning_queue.pop(key)

    async def close(self):
        self.closed = True
        tasks = list(self.reply_tasks)
        if self.learning_task is not None:
            tasks.append(self.learning_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.learning_queue.clear()
        self.channels.clear()

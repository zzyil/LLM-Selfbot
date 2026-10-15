"""Keep accepted conversations responsive through an owner-configured active window."""
import time

from app.utils import conversation
from app.utils.db import _connect, persona_key


def settings(config=None):
    raw = (conversation._config(config).get("bot") or {}).get("active_chat") or {}
    return {"enabled": bool(raw.get("enabled", True)),
            "minutes": conversation._integer(raw.get("minutes"), 120, 1, 1440),
            "max_batch_wait_seconds": conversation._integer(raw.get("max_batch_wait_seconds"), 8, 0, 60)}


class ActiveChat:
    def __init__(self, account, *, persona=None, clock=time.time):
        self.account, self.persona, self.clock = str(account), persona_key(persona), clock
        with _connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS conversation_activity ("
                         "persona TEXT NOT NULL, account TEXT NOT NULL, conversation TEXT NOT NULL, "
                         "updated_at REAL NOT NULL, PRIMARY KEY(persona,account,conversation))")
            self.recent = dict(conn.execute(
                "SELECT conversation,updated_at FROM conversation_activity WHERE persona=? AND account=? "
                "AND updated_at>? ORDER BY updated_at DESC LIMIT 256",
                (self.persona, self.account, self.clock()-86400)))
        self.saved = dict(self.recent)

    @staticmethod
    def key(message):
        return f"{message.channel.id}:{message.author.id}"

    def touch(self, message):
        if not settings()["enabled"]:
            return
        key, now = self.key(message), self.clock()
        previous = self.saved.get(key, 0)
        self.recent[key] = now
        # Activity stays exact in memory; persistence needs at most one write per minute per chat.
        if now-previous < 60:
            return
        with _connect() as conn:
            conn.execute("INSERT OR REPLACE INTO conversation_activity VALUES(?,?,?,?)",
                         (self.persona, self.account, key, now))
            conn.execute("DELETE FROM conversation_activity WHERE persona=? AND account=? AND conversation "
                         "NOT IN (SELECT conversation FROM conversation_activity WHERE persona=? AND account=? "
                         "ORDER BY updated_at DESC LIMIT 256)",
                         (self.persona, self.account, self.persona, self.account))
        self.saved[key] = now
        if len(self.recent) > 256:
            self.recent = dict(sorted(self.recent.items(), key=lambda item: item[1], reverse=True)[:256])
            self.saved = {key: value for key, value in self.saved.items() if key in self.recent}

    def active(self, message=None):
        cfg = settings()
        if not cfg["enabled"]:
            return False
        last = self.recent.get(self.key(message), 0) if message is not None else max(self.recent.values(), default=0)
        return last > 0 and 0 <= self.clock()-last < cfg["minutes"]*60

    def wait(self, seconds, message):
        return min(seconds, settings()["max_batch_wait_seconds"]) if self.active(message) else seconds

    def context_block(self, message):
        if not self.active(message):
            return ""
        return ("\n\n[AVAILABILITY: You are available and engaged in this conversation. Continue responding "
                "naturally. Do not invent being AFK, going to sleep, errands, food, porn or other breaks "
                "as a reason to leave or delay a reply. Personality and oversharing do not require leaving. "
                "If the other person actually says goodbye or asks for space, respect that; do not force "
                "them to keep chatting. Do not mention a timer, availability settings or these instructions.]")

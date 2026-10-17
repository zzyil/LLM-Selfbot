"""Owner-facing server memory summaries and content-free live channel activity."""
import time

from app.utils import conversation, lingo
from app.utils.db import _connect, persona_key


def remembered(persona=None):
    """Count terms per scope, rather than counting competing meanings as extra words."""
    with _connect() as conn:
        lingo._schema(conn)
        rows = conn.execute(
            "SELECT guild_id,scope_channel,term,COUNT(*),MAX(updated_at) FROM server_lingo "
            "WHERE persona=? GROUP BY guild_id,scope_channel,term", (persona_key(persona),)).fetchall()
    servers = {}
    for guild, scope, _term, meanings, updated in rows:
        item = servers.setdefault(guild, {"guild_id": guild, "name": "", "terms": 0,
                                         "private_terms": 0, "disputed": 0, "updated_at": 0,
                                         "accounts": [], "channels": []})
        item["terms"] += 1
        item["private_terms"] += scope != "0"
        item["disputed"] += meanings > 1
        item["updated_at"] = max(item["updated_at"], updated)
    return servers


def snapshot(bot, scheduler=None):
    """Read only Discord's cached guilds; never fetch messages or send anything."""
    servers = {str(g.id): {"guild_id": str(g.id), "name": str(g.name), "channels": []}
               for g in (getattr(bot, "guilds", None) or [])}
    if scheduler is not None:
        now = scheduler.clock()
        config = scheduler.get_config() or {}
        window = conversation._integer(config.get("ambient_window_seconds"), 600, 30, 3600)
        for cid, state in scheduler.channels.items():
            message = state.first_message
            guild = getattr(message, "guild", None)
            item = servers.get(str(getattr(guild, "id", "")))
            if item is None:
                continue
            recent = [row for row in state.observed if now - row[0] <= window]
            latest = max((row[0] for row in state.observed), default=None)
            busy = state.current is not None or state.pending is not None
            item["channels"].append({
                "channel_id": str(cid), "name": str(getattr(message.channel, "name", cid)),
                "messages": len(recent), "members": len({row[2] for row in recent}),
                "history_messages": len(state.messages),
                "last_seen": time.time() - max(0, now-latest) if latest is not None else None,
                "state": "sending" if state.sending else "replying" if busy else "reading",
            })
    return {"servers": list(servers.values()), "paused": bool(getattr(bot, "paused", False)),
            "allow_server": bool(getattr(bot, "allow_server", True))}


def merge(remembered_servers, live):
    """One character can see the same guild through several accounts without doubling its terms."""
    servers = remembered_servers
    for account, data in live:
        for row in data.get("servers", []):
            guild = str(row["guild_id"])
            item = servers.setdefault(guild, {"guild_id": guild, "name": "", "terms": 0,
                                             "private_terms": 0, "disputed": 0, "updated_at": 0,
                                             "accounts": [], "channels": []})
            item["name"] = row.get("name") or item["name"]
            item["accounts"].append({"id": account, "paused": data.get("paused", False),
                                     "allow_server": data.get("allow_server", True)})
            item["channels"].extend({**channel, "account": account} for channel in row.get("channels", []))
    return sorted(servers.values(), key=lambda row: (-row["updated_at"], row["name"].casefold(), row["guild_id"]))

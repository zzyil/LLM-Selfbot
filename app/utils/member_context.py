"""Where a character has met a member, without treating channel traffic as DMs."""
import time

from app.utils.db import _connect, persona_key


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS member_contexts (
        persona TEXT NOT NULL, user_id TEXT NOT NULL, guild_id TEXT NOT NULL,
        channel_id TEXT NOT NULL, username TEXT NOT NULL, guild_name TEXT NOT NULL,
        channel_name TEXT NOT NULL, public INTEGER NOT NULL, messages INTEGER NOT NULL,
        first_seen REAL NOT NULL, last_seen REAL NOT NULL,
        PRIMARY KEY(persona,user_id,guild_id,channel_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS member_context_messages (
        persona TEXT NOT NULL, message_id TEXT NOT NULL, user_id TEXT NOT NULL,
        guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, at REAL NOT NULL, public INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(persona,message_id))""")
    if "public" not in {row[1] for row in conn.execute("PRAGMA table_info(member_context_messages)")}:
        conn.execute("ALTER TABLE member_context_messages ADD COLUMN public INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS member_context_time ON member_context_messages(persona,at)")


def observe(message, persona=None):
    """Record an accepted real source once; no biography is inferred from it."""
    from app.utils import conversation, lingo
    if conversation.ignored_chat_message(message):
        return False
    guild = getattr(message, "guild", None)
    author = message.author
    pid, uid = persona_key(persona), str(author.id)
    gid, cid = str(guild.id) if guild is not None else "", str(message.channel.id)
    created = getattr(message, "created_at", None)
    at = created.timestamp() if created is not None else time.time()
    public = guild is not None and lingo.source_scope(message) == "0"
    with _connect() as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        added = conn.execute("INSERT OR IGNORE INTO member_context_messages VALUES(?,?,?,?,?,?,?)",
                             (pid, str(message.id), uid, gid, cid, at, int(public)))
        if not added.rowcount:
            return False
        conn.execute("""INSERT INTO member_contexts VALUES(?,?,?,?,?,?,?,?,1,?,?)
            ON CONFLICT(persona,user_id,guild_id,channel_id) DO UPDATE SET
            messages=member_contexts.messages+1, first_seen=MIN(first_seen,excluded.first_seen),
            last_seen=MAX(last_seen,excluded.last_seen), username=excluded.username,
            guild_name=excluded.guild_name,channel_name=excluded.channel_name,public=excluded.public""",
            (pid, uid, gid, cid, str(getattr(author, "display_name", None) or author.name)[:100],
             str(getattr(guild, "name", ""))[:100], str(getattr(message.channel, "name", ""))[:100],
             int(public), at, at))
        if added.lastrowid % 512 == 0:
            # Keep source links used by social memories, and bound ordinary deduplication metadata.
            keep = (" AND message_id NOT IN (SELECT message_id FROM companion_relationship_events WHERE persona=?)"
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='companion_relationship_events'").fetchone() else "")
            params = (pid, pid, pid) if keep else (pid, pid)
            conn.execute("DELETE FROM member_context_messages WHERE persona=? AND message_id IN "
                         "(SELECT message_id FROM member_context_messages WHERE persona=? ORDER BY at DESC LIMIT -1 OFFSET 50000)"
                         + keep, params)
    return True


def users(persona=None):
    with _connect() as conn:
        _schema(conn)
        rows = conn.execute("SELECT user_id,guild_id,channel_id,username,guild_name,channel_name,public,"
                            "messages,first_seen,last_seen FROM member_contexts WHERE persona=? "
                            "ORDER BY last_seen DESC", (persona_key(persona),)).fetchall()
    out = {}
    for uid, gid, cid, name, guild, channel, public, count, first, last in rows:
        item = out.setdefault(uid, {"user_id": uid, "username": name, "messages": 0,
                                   "first_seen": first, "last_seen": last, "contexts": []})
        item["messages"] += count
        item["first_seen"] = min(item["first_seen"], first)
        item["contexts"].append({"kind": "server" if gid else "dm", "guild_id": gid,
            "channel_id": cid, "guild_name": guild, "channel_name": channel, "public": bool(public),
            "messages": count, "last_seen": last})
    return out


def interactions(user_id, persona=None, *, message=None, limit=12):
    """Source-backed social memories; a public reply cannot recall private exchanges."""
    from app.utils import relationships
    pid, uid = persona_key(persona), str(user_id)
    params = [pid, uid]
    where = ""
    if message is not None:
        guild = getattr(message, "guild", None)
        if guild is not None:
            # Restricted-channel events stay in their source channel.
            where = " AND m.guild_id=? AND (m.public=1 OR m.channel_id=?)"
            params.extend([str(guild.id), str(message.channel.id)])
        else:
            where = " AND m.guild_id='' AND m.channel_id=?"
            params.append(str(message.channel.id))
    with _connect() as conn:
        _schema(conn)
        relationships._schema(conn)
        rows = conn.execute("""SELECT e.message_id,e.stance,e.quote,e.created_at,
            COALESCE(m.guild_id,''),COALESCE(m.channel_id,''),COALESCE(c.guild_name,''),
            COALESCE(c.channel_name,''),m.message_id
            FROM companion_relationship_events e
            LEFT JOIN member_context_messages m ON m.persona=e.persona AND m.message_id=e.message_id AND m.user_id=e.user_id
            LEFT JOIN member_contexts c ON c.persona=m.persona AND c.user_id=m.user_id
                AND c.guild_id=m.guild_id AND c.channel_id=m.channel_id
            WHERE e.persona=? AND e.user_id=? AND e.stance!='neutral'""" + where +
            " ORDER BY e.created_at DESC LIMIT ?", (*params, max(1, min(50, limit)))).fetchall()
    return [{"message_id": mid, "stance": stance, "quote": quote, "at": at,
             "guild_id": gid, "channel_id": cid, "guild_name": guild, "channel_name": channel,
             "kind": ("server" if gid else "dm") if source else "legacy"}
            for mid, stance, quote, at, gid, cid, guild, channel, source in rows]


def clear(user_id, persona=None):
    with _connect() as conn:
        _schema(conn)
        for table in ("member_contexts", "member_context_messages"):
            conn.execute(f"DELETE FROM {table} WHERE persona=? AND user_id=?", (persona_key(persona), str(user_id)))


def merge_people(people, persona=None):
    from app.utils import relationships
    known, profiles = users(persona), relationships.profiles(persona)
    for uid in known.keys() | profiles.keys():
        seen = known.get(uid, {})
        entry = people.setdefault(uid, {"user_id": uid, "username": seen.get("username", ""),
            "facts": {}, "persona": "", "learned_at": 0, "learned": ""})
        entry["contexts"] = seen.get("contexts", [])
        entry["relationship"] = profiles.get(uid, {"relationship": "new", "affinity": 0, "interactions": 0, "updated_at": 0})
        entry["observed"] = {k: seen.get(k, 0) for k in ("messages", "first_seen", "last_seen")}
    return people


def copy_persona(src, dst):
    with _connect() as conn:
        _schema(conn)
        for table in ("member_contexts", "member_context_messages"):
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})") if row[1] != "persona"]
            cols = ",".join(columns)
            conn.execute(f"INSERT OR REPLACE INTO {table}(persona,{cols}) SELECT ?,{cols} FROM {table} WHERE persona=?", (dst, src))

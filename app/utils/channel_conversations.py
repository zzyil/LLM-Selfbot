"""Keep channel conversations distinct from people, including old runner/cache rows."""
import re


def is_channel(row):
    return row.get("kind") == "server" or bool(re.fullmatch(r"ch[0-9]+", str(row.get("id") or row.get("uid") or "")))


def channel_row(row):
    raw_id = str(row.get("id") or row.get("uid") or "")
    channel_id = str(row.get("channel_id") or raw_id.removeprefix("ch"))
    where = str(row.get("where") or "").split(" · ", 1)
    name = str(row.get("channel_name") or where[0].lstrip("#") or channel_id)
    guild = str(row.get("guild_name") or (where[1] if len(where) > 1 else ""))
    author = row.get("latest_author") or row.get("author")
    if not author and row.get("author_id"):
        author = {"id": str(row["author_id"]), "name": row.get("name", ""), "avatar": row.get("avatar", "")}
    return {**row, "id": f"ch{channel_id}", "channel_id": channel_id, "kind": "server",
            "name": f"#{name}", "channel_name": name, "guild_name": guild,
            "guild_id": str(row.get("guild_id") or ""), "where": f"#{name}" + (f" · {guild}" if guild else ""),
            "avatar": "", "username": "", "latest_author": author}


def apply_active_event(rows, event):
    """Track activity without turning ambient channel messages into pending replies."""
    if event.get("t") == "active_channels":
        return [channel_row(row) for row in event.get("channels", []) if is_channel(row)]
    uid = str(event.get("uid", ""))
    out = []
    for row in rows:
        if row.get("id") != uid:
            out.append(row)
            continue
        if event.get("t") == "channel_msg":
            fields = ("text", "mid", "attachments", "stickers", "embeds", "reactions", "author")
            fresh = {key: event.get(key, [] if key in fields[2:6] else "") for key in fields}
            fresh.update(ts=event.get("ts", 0), snippet=event.get("text", ""),
                         latest_author=event.get("author"), mine=bool(event.get("mine")))
            out.append({**row, **fresh})
        elif event.get("t") == "reactions" and row.get("mid") == event.get("mid"):
            out.append({**row, "reactions": event.get("reactions", [])})
        elif event.get("t") == "edited" and row.get("mid") == event.get("mid"):
            out.append({**row, "text": event.get("text", ""), "snippet": event.get("text", "")})
        elif event.get("t") == "deleted" and row.get("mid") == event.get("mid"):
            out.append({**row, "text": "", "snippet": "Message deleted", "attachments": [], "embeds": [], "stickers": [], "reactions": []})
        else:
            out.append(row)
    return out


def separate(people, channels=()):
    contacts, by_channel = [], {}
    for row in [*people, *channels]:
        if is_channel(row):
            item = channel_row(row)
            by_channel[item["id"]] = item
        else:
            contacts.append(row)
    return contacts, list(by_channel.values())

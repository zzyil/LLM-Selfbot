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


def separate(people, channels=()):
    contacts, by_channel = [], {}
    for row in [*people, *channels]:
        if is_channel(row):
            item = channel_row(row)
            by_channel[item["id"]] = item
        else:
            contacts.append(row)
    return contacts, list(by_channel.values())

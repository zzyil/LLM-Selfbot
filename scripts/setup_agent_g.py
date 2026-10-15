"""Seed a local-only Discord companion profile without launching or connecting any account."""
import argparse
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from app.utils.paths import DATA_DIR, DEFAULTS_DIR


def build_config(base, model, owner_id=0, channels=(), servers=()):
    config = copy.deepcopy(base)
    bot = config["bot"]
    bot.update({"owner_id": owner_id, "trigger": "G", "allow_dm": False, "allow_gc": False,
                "allow_server": True, "disable_mentions": True, "reply_ping": False,
                "read_bios": False, "ignore_chance": 0, "typo_chance": 0,
                "edit_after_send_chance": 0, "timezone": "America/Detroit"})
    bot["local"].update({"enabled": True, "only": True, "model": model,
                          "base_url": "http://127.0.0.1:1234/v1", "autostart": False})
    bot["models"] = {"replies": [{"provider": "local", "model": model}],
                     "small": {"provider": "local", "model": model}}
    bot["companion"] = {
        "enabled": True, "allowed_channel_ids": [str(c) for c in channels],
        "allowed_guild_ids": [str(s) for s in servers], "include_threads": True,
        "follow_joined_servers": not bool(channels or servers),
        "server": {"names": ["G"], "reply_to_name": True, "followup_seconds": 45,
                   "ambient_reply_probability": 0, "batch_wait_seconds": 3,
                   "reply_cooldown_seconds": 20, "max_reply_age_seconds": 120,
                   "learn_from_chat": True, "learning_min_messages": 4,
                   "learning_interval_seconds": 120},
    }
    bot["server_batch_wait_times"] = [{"time": 3, "weight": 100}]
    bot["message_style"]["mode"] = "short"
    for key in ("pictures", "tts", "friend_requests", "nudge", "night_invisible", "status",
                "late_reply"):
        bot[key]["enabled"] = False
    # Allow truthful model replies; don't substitute a reaction for an AI identity disclosure.
    bot.setdefault("refusal", {})["enabled"] = False
    config["snapchat"]["enabled"] = False
    for key in ("telegram_error_notifications", "telegram_crash_alerts", "ratelimit_notifications"):
        config.setdefault("notifications", {})[key] = False
    services = config.setdefault("services", {})
    for key in ("discord", "snapchat", "telegram"):
        services.setdefault(key, {})["enabled"] = False
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Exact chat-model ID from LM Studio /v1/models")
    parser.add_argument("--owner-id", type=int, default=0)
    parser.add_argument("--channels", default="", help="Comma-separated allowed Discord channel IDs")
    parser.add_argument("--servers", default="", help="Comma-separated guild IDs; allows their channels")
    args = parser.parse_args()
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    servers = [s.strip() for s in args.servers.split(",") if s.strip()]
    if any(not value.isdigit() for value in channels+servers) \
            or args.owner_id < 0 or not args.model.strip():
        parser.error("Use a nonempty model ID and numeric channel/server/owner IDs")
    target = DATA_DIR / "config"
    if any((target / name).exists() for name in ("config.yaml", "instructions.txt", ".env")):
        parser.error("Runtime config already exists. Edit it in the app; this script never overwrites it.")
    base = yaml.safe_load((DEFAULTS_DIR / "config.yaml").read_text(encoding="utf-8"))
    config = build_config(base, args.model.strip(), args.owner_id, channels, servers)
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (target / "instructions.txt").write_text(
        (DEFAULTS_DIR / "agent-g-instructions.txt").read_text(encoding="utf-8"), encoding="utf-8")
    (target / ".env").write_text("# Add your account in the app when ready.\nDISCORD_TOKEN_1=\n", encoding="utf-8")
    print(f"Created companion config in {target}. No account was started.")
    print("Load your model and start LM Studio's server. Choose channels/servers and add your account.")


if __name__ == "__main__":
    main()

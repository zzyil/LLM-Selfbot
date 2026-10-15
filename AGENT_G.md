# Agent G fork

This fork develops one persistent character that lives on Discord. Its personality,
memories and relationships belong to the character and carry into servers it joins
later. It keeps LLM-Selfbot's dashboard, model routing and conversation handling.
The upstream GPL-3.0 license remains in place.

## One character, wherever it goes

The account's selected persona is its character. One saved set of instructions
defines its personality, and one persistent memory stores what it knows about
each person. Discord user IDs identify people across nicknames, DMs and servers.
Friendships and rivalries follow those people; joining a different server or
restarting the app doesn't create a new version of the character.

For example, G can get to know Alex in one place and recognize Alex when they
meet in a new server months later. The relationship and remembered interests are
still there, subject to normal relationship decay. The new channel gets its own
short conversation history so unrelated messages don't get mixed together.

The preset follows servers the account belongs to, including future memberships.
Join a server normally with that Discord account when ready; the app doesn't
accept invitations or join servers on its own. Keep the same persona assigned
to the account to keep the same character. Chat vibe changes how it expresses
itself, while its established personality and preferences remain consistent.

## Setup

Use Python 3.12 in an isolated environment. `discord.py` and `discord.py-self`
share the `discord` import, so install only the latter here.

```sh
uv pip install --python .venv/bin/python -r requirements.txt
npm --prefix webui ci
npm --prefix webui run build
```

Load a chat/instruct model in LM Studio and start its local API server. Bionic is
a separate app, not a model ID. Read the server's model IDs:

```sh
curl http://127.0.0.1:1234/v1/models
```

Before the app's first launch, seed the character using your exact model ID and
your Discord user ID. Enable Discord Developer Mode to copy IDs.

```sh
.venv/bin/python scripts/setup_agent_g.py --model YOUR_MODEL_ID --owner-id YOUR_DISCORD_ID
.venv/bin/python main.py
```

No server ID is needed for the character to follow the account into future servers.
If you want to restrict its participation, pass `--servers SERVER_ID` or
`--channels CHANNEL_ID`; supplying either option disables automatic participation
in other joined servers. Both accept comma-separated IDs.

The setup script refuses to overwrite existing runtime configuration. For an
existing installation, edit the equivalent settings in `config/config.yaml`:

```yaml
bot:
  local:
    enabled: true
    only: true
    base_url: http://127.0.0.1:1234/v1
    model: YOUR_MODEL_ID
  models:
    replies: [{provider: local, model: YOUR_MODEL_ID}]
    small: {provider: local, model: YOUR_MODEL_ID}
  companion:
    enabled: true
    follow_joined_servers: true
    allowed_channel_ids: []
    allowed_guild_ids: []
    include_threads: true
```

Copy `resources/agent-g-instructions.txt` into your persona's instructions, or
edit its personality in the existing Persona page. Credentials stay in the app's
ignored `config/.env`. The preset leaves account services stopped; add an account
and start it explicitly from the dashboard. With `follow_joined_servers: false`,
empty channel and server lists observe and answer nothing. Threads inherit an allowed parent channel by default;
set `include_threads: false` to require their own channel IDs. DMs and group chats
need their own channel IDs and are disabled by the preset; enable them if needed.

`bot.local.only` filters all model chains to local providers and disables the
implicit Groq fallback for background work, images and speech. Unsupported jobs
fail locally; they aren't sent to another provider. An unset chat model stays
unconfigured. The app still connects to Discord and retains upstream networking
features such as updates; this option controls inference providers.

## Server conversations

Companion mode works in server text channels, threads and forum posts the account
can access. Choose individual channels or whole servers. It keeps a shared recent
transcript per channel, including members it doesn't answer. Nicknames carry user
IDs so members with the same display name remain distinct. Threads have separate
conversation history while the character's relationships remain shared everywhere.

It answers direct mentions, replies to its messages, and its configured name.
After a delivered reply, the same member can continue without another mention
for 45 seconds, up to two follow-ups. A reply or mention aimed at someone else
doesn't count as a follow-up. One queue per channel combines bursts from multiple
members into a single response, with a three-second collection window and a
20-second cooldown between inference attempts. Stale requests expire after two
minutes. Dashboard Reply and Cancel work with this queue.

Customize these under `bot.companion.server` in `config/config.yaml`:

```yaml
names: ["G", "Agent G"]
reply_to_name: true
followup_seconds: 45
ambient_reply_probability: 0.0
batch_wait_seconds: 3
reply_cooldown_seconds: 20
max_reply_age_seconds: 120
learn_from_chat: true
learning_min_messages: 4
learning_interval_seconds: 120
```

Ambient replies are optional: for example, `0.02` gives an otherwise unaddressed
message a 2% chance to create a reply opportunity, subject to the same queue and
cooldown. The default is zero. Change `names` to match your persona.

Passive learning batches each member's own statements separately, after four
messages or a direct interaction, at most once every two minutes per member and
server. This batching is an inference limit; learned facts still belong to the
same character. It can learn while staying silent. Other members' words never enter that
person's fact extractor. Passive chatter and ambient replies don't alter friendship
scores; those require conversation directed to the companion. Live history tracks
edits and deletions. No model weights are trained.

The account needs permission to view a channel and send messages there, including
thread-specific permissions. If history access is unavailable, it uses messages
observed during the session. A server permission failure doesn't mark a member as
having blocked the account.

## Memory, relationships and vibe

Learning uses upstream's fact extractor, including passive server batches in
companion mode. Facts remain editable in the Memory page. Upstream facts are
scoped to the character and person, and follow them across conversations.
Relationships use the same character/person identity, independent of servers or
DM channels. Another persona is another character with its own memory.

Earlier server-specific relationship records are carried into a single character
relationship, with their existing interaction counts and decayed attitudes. Their
source rows are preserved. New updates use the character's shared record and don't
count the old snapshots again.

The companion classifies clearly directed kindness or hostility using the
background model, with a literal source quote. Ambiguous evidence stays neutral.
Each message can move affinity at most 0.06; updates are limited to one per person
and character every two minutes, even if they switch servers. Five analyzed interactions are needed before a
friend/wary/foe label is possible. Close friends need twenty. Attitudes decay
toward neutral with a 60-day half-life. Foe means polite distance in the prompt.
The Memory page shows the relationship states; its Forget action clears them
along with that person's facts. Forgetting does not permanently opt them out of
upstream learning, so pause/ignore the person too if you want future learning stopped.

Recent conversation supplies vocabulary and topics. Added style context measures
message length, casing, laughter and emoji; the persona adapts its delivery while
keeping a consistent identity. Local models can still misread tone or facts, so
inspect the Memory page rather than treating its notes as ground truth.

Pairwise relationships between other members, fine-tuning, and semantic vector
search are future extensions.

## Development

```sh
.venv/bin/python tests/test_agent_g.py
.venv/bin/python tests/test_server_chat.py
.venv/bin/python tests/test_engine.py
.venv/bin/python tests/test_localai.py
.venv/bin/python tests/test_memory_brain.py
npm --prefix webui run build
```

No token or real Discord account is needed for these checks. `main.py` starts the
app; tests use temporary data directories and fake inference clients.

Discord prohibits automating user accounts and may terminate them. See its
[self-bot policy](https://support.discord.com/hc/en-us/articles/115002192352-Automated-User-Accounts-Self-Bots).
LM Studio's [local server documentation](https://lmstudio.ai/docs/developer/core/server)
and [OpenAI-compatible API](https://lmstudio.ai/docs/developer/openai-compat) explain
the endpoint configuration.

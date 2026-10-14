# Agent G fork

This fork keeps LLM-Selfbot's dashboard, Discord integration, model routing,
message batching, conversation history and editable per-person memory. Its added
companion mode supplies persistent attitudes and chat-style context. The upstream
GPL-3.0 license remains in place.

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

Before the app's first launch, seed the profile using your exact model ID and the
channel IDs it may read and answer. Enable Discord Developer Mode to copy IDs.

```sh
.venv/bin/python scripts/setup_agent_g.py --model YOUR_MODEL_ID --owner-id YOUR_DISCORD_ID --channels CHANNEL_ID
.venv/bin/python main.py
```

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
    allowed_channel_ids: ["CHANNEL_ID"]
```

Copy `resources/agent-g-instructions.txt` into your persona's instructions, or
edit its personality in the existing Persona page. Credentials stay in the app's
ignored `config/.env`. The preset leaves account services stopped; add an account
and start it explicitly from the dashboard. An empty companion channel list
observes and answers nothing. Include each thread or DM's own channel ID to
allow it. DMs and group chats are disabled by the preset; enable them if needed.

`bot.local.only` filters all model chains to local providers and disables the
implicit Groq fallback for background work, images and speech. Unsupported jobs
fail locally; they aren't sent to another provider. An unset chat model stays
unconfigured. The app still connects to Discord and retains upstream networking
features such as updates; this option controls inference providers.

## Memory, relationships and vibe

Learning uses upstream's fact extraction after conversations; it does not train
model weights. Facts remain editable in the Memory page. Upstream facts are
scoped to a persona and person, and may be reused across that persona's channels.
Relationship state is separately scoped to the persona, Discord server or private
chat. A person can be a friend in one server and an acquaintance in another.

The companion classifies clearly directed kindness or hostility using the
background model, with a literal source quote. Ambiguous evidence stays neutral.
Each message can move affinity at most 0.06; updates are limited to one per person
and scope every two minutes. Five analyzed interactions are needed before a
friend/wary/foe label is possible. Close friends need twenty. Attitudes decay
toward neutral with a 60-day half-life. Foe means polite distance in the prompt.
The Memory page shows the relationship states; its Forget action clears them
along with that person's facts. Forgetting does not permanently opt them out of
upstream learning, so pause/ignore the person too if you want future learning stopped.

Recent conversation supplies vocabulary and topics. Added style context measures
message length, casing, laughter and emoji; the persona adapts its delivery while
keeping a consistent identity. Local models can still misread tone or facts, so
inspect the Memory page rather than treating its notes as ground truth.

This base learns from conversations it answers. Passive learning from unrelated
channel messages, pairwise relationships between other members, fine-tuning, and
semantic vector search are future extensions.

## Development

```sh
.venv/bin/python tests/test_agent_g.py
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

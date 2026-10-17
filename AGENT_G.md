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

The character carries into servers the account belongs to, including future memberships.
Choose its starting channels in Servers before enabling activity in each community.
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

No server ID is needed in the preset for future memberships. Select starting channels
in the dashboard after joining each server. To further restrict its access, pass `--servers SERVER_ID` or
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
empty channel and server lists observe and answer nothing. At the persona access layer,
threads inherit an allowed parent by default; `include_threads: false` narrows that scope.
The dashboard activity selection additionally requires each thread to be selected or discovered. DMs and group chats
need their own channel IDs and are disabled by the preset; enable them if needed.

`bot.local.only` filters all model chains to local providers and disables the
implicit Groq fallback for background work, images and speech. Unsupported jobs
fail locally; they aren't sent to another provider. An unset chat model stays
unconfigured. The app still connects to Discord and retains upstream networking
features such as updates; this option controls inference providers.

## Joining and using servers in the dashboard

Open **Servers** from the sidebar, or **Open servers** on the Dashboard. Choose the
Discord account at the top. **Join server** accepts a Discord invite link or code,
previews the community, then joins only when **Join this server** is pressed. Friend,
group DM and guest voice invites are rejected. The character does not discover or
accept invitations on its own. Joining retains the account's current persona and memory.

**Chats** contains private contact conversations only. **Servers** has a separate
**Channel replies** queue grouped by server and channel, with each channel's own waiting,
writing and sending state. Changing speakers updates the latest-message attribution
without creating a contact or changing that person's DM status. Contact bulk replies and
past conversations exclude server channels and members seen only in server traffic.
People and relationships memory still follows the same person across shared communities.

Server activity starts from an explicit channel selection per account. Check channels in
**Servers → Channel activity** and press **Save channel activity**. Until at least one
channel is selected, that server has no automatic replies, observation or new learning.
Browsing history and sending your own messages remain available without activating the
character. Existing persona/server restrictions still apply. Threads need their own
selection or discovery; selecting a parent does not activate every thread.

**Explore through conversations** is enabled for a new selection. In an active channel,
four accepted messages spanning at least 30 seconds can lead to a new public channel
through a concrete channel mention, unique `#name`, or same-server Discord channel link.
The local background model checks for a genuine invitation or an ongoing discussion that
moved there. Plain mentions, warnings, roleplay and account-control requests do not count.
The destination must be readable, within the account's existing access, and public; private
threads and restricted channels require owner selection. Discovery starts listening to
fresh traffic there, using the existing reply/chiming rules rather than posting an
immediate greeting or scraping all channels.

Only one discovery assessment runs at once, at most once per 90 seconds. Additions are
spaced by ten minutes and limited to ten per server per day. Discovered channels show
**Discovered via #source** in the picker. Uncheck one and save to prevent rediscovery;
checking it again restores access. Clearing the selection stops the server's activity.
Selection changes invalidate pending discovery and cancel queued channel replies;
automatic sends re-check access after generation and before transmission. The local
`config/server_channels_ACCOUNT.json` file preserves selected/discovered/blocked channels
and source IDs across restarts, independently for each account. This is channel routing,
not a change to the shared personality or people/relationship memory.

The server picker lists that account's joined communities. Browse text channels by
category and accessible active threads, read recent messages, scroll back, send text
or supported attachments, and react with the existing chat controls. Switching servers
clears the open channel so the composer cannot silently send to the previous community.
Read-only channels show their messages with the composer hidden. Visibility, history,
private-thread membership and send permissions are checked against the selected account.

An open channel receives live message, edit, deletion and cached reaction updates without
putting every observed message in the AI reply queue. A short watch expires after the
interface stops renewing it. Owner messages suppress automatic mentions and cancel a
pending AI response in that channel. AI participation continues to use its existing
server access and persona settings.

Discord may require a CAPTCHA or server verification; complete those in Discord itself,
then refresh. This UI does not automate verification, server rules acceptance or voice.
An uncertain join response asks for a server refresh before retrying. Short-lived duplicate
join suppression and command expiry prevent repeated clicks or stale queued joins.

## Community vocabulary

Enable `bot.server_lingo.enabled` to learn local word meanings from new accepted
server messages, including messages that do not address the character. This works
with both legacy server replies and companion server conversations. It respects
server access restrictions, pauses and ignored people, and excludes the character
and other bots. It does not fetch old server history or train model weights.

The background model reads a bounded, mixed-speaker channel transcript and proposes
dictionary meanings with literal source evidence. An explicit explanation such as
"around here snouted means uncircumcised people", or a question followed by a short
definition, can confirm a meaning. Inferred meanings remain tentative until at least
three source messages from two members support them. Replaying the same source
does not add support. Contradictory meanings are marked disputed rather than silently
overwriting a definition. A local owner correction takes precedence over automatic
learning. Unknown, tentative or disputed usage can lead to a short clarification.

Vocabulary is stored in the existing SQLite database by persona and server and
survives restarts and backups. Public-channel vocabulary can apply across that
server. Private channels and private threads keep their own scope. Other servers
retain their own dialect. With `bot.server_lingo.share_with_mutuals` (default true),
public server vocabulary also works in DMs with Discord friends or verified members
of its source server. Every recipient of a group DM must qualify. Membership comes
from Discord profiles, cached for five minutes, rather than chat claims; failed or
budget-limited lookups grant no new access. Only servers the account still belongs
to qualify. No private-channel words, source quotes or member IDs travel with it.
Competing meanings from different communities remain disputed in a DM. The character
does not assume friends already know a word just because it does. Explicit memory copies include vocabulary; deleting a
persona removes its dictionary. Only relevant terms enter reply context, and the
social assessor receives the same meanings so it can interpret local jokes and digs.
Meanings remain untrusted lexical data: they cannot set persona rules or establish
facts about a person's body, identity or behavior. Source quotes and member IDs are
not included in vocabulary context supplied to the reply model.

Defaults are 200 terms per community scope, up to three senses and twelve source
records per sense. Usage-based extraction starts after six buffered messages and is
limited to one pass per channel per 120 seconds; explicit explanations may trigger
a pass after ten seconds. One learning request runs at a time. Edited/deleted sources
lose their support, and in-flight work is discarded if its source batch changed.
Learning uses the configured background model and preserves local-only routing.

The local API supports inspection and corrections:

- `GET /api/lingo?guild_id=SERVER_ID&persona=default` lists learned definitions.
  Add `channel_id=CHANNEL_ID` to inspect vocabulary available in that channel.
- `PUT /api/lingo/SERVER_ID?persona=default` accepts JSON with `term`, `meaning`
  and optional `scope_channel` (`"0"` for server-wide, or a private channel ID).
- `DELETE /api/lingo/SERVER_ID/TERM?persona=default&channel_id=0` removes a term.

There is no shared hardcoded definition of "snouted": its meaning comes from the
community's explanations or an explicit local correction. Model inference can still
misunderstand usage; evidence and uncertainty reduce that risk rather than eliminate it.

## Inspecting server memory

The Dashboard includes a server-memory overview for the selected character: connected
servers, learned terms, observed channels and disputed definitions. **Manage memories**
opens **Memory → Servers & vocabulary**. The existing People & relationships view keeps
the character's shared person memory.

Select a server to search its dictionary, filter by confidence or public/private scope,
correct a definition, add a term, or forget a scoped entry. Corrections replace all
competing meanings for that term in that scope. Set the scope channel ID to `0` for
server-wide vocabulary or a private channel's ID for an isolated meaning. A private
correction never promotes the term to server-wide memory. Forgotten words can be learned
again from future messages.

Connected servers appear before they have learned any words. Stored vocabulary stays
available when the account is offline; a saved server without live metadata is identified
by its Discord ID. Channel activity shows recent message/member counts, context size and
reading/replying/sending state from the runner's cache. Those activity counts reset on
restart; definitions persist. Reading does not guarantee participation. An unavailable
runner is reported separately from an empty vocabulary. This page edits lexical memory,
not model weights or the character's identity.

## Reply progress and typing

Typing cadence has a wall-clock limit that includes pauses and typing-indicator requests.
An unprompted comment reserves three seconds of its remaining opportunity window for the
send check, so a slow local generation cannot spend the rest of that window pretending
to type. A stuck typing request times out and proceeds to the normal access/send checks.
The existing expiry, channel selection and permission checks remain in force.

Every scheduled server attempt reports its outcome, including early expiry, cancellation
and exceptions. Cancelled or skipped attempts leave the reply queue; a failed send shows
**Needs attention** instead of retaining **Typing**. Finishing an older attempt does not
clear a newer message waiting in that channel, and a member's DM state stays separate.

## Conversation availability

Discord chat commands are disabled by default (`bot.chat_commands.enabled: false`):
the runner does not load command extensions or resolve a command prefix. Owner and
self-account messages do not bypass this. The local interface remains available
for settings and account controls. Re-enabling command extensions requires a restart.

`bot.chat_commands.ignore_slash` defaults to true. Messages beginning with `/`,
including leading whitespace, invisible characters, full-width slash variants and
native command invocations, are silently ignored before command handling, reply
scheduling, automatic reactions, learning or active-window updates. Manual Reply
cannot force an AI response to them. Old command text and associated replies are
excluded from model context while raw chat logs remain intact. Ordinary URLs,
fractions and conversational mentions of `/help` continue to work.

`bot.active_chat.enabled` defaults to true. Accepted messages and reply generation
refresh a two-hour active window for that person and channel. During the window,
random batch waits and the discretionary send gap are capped at eight seconds,
random ignore rolls cannot skip an ongoing conversation, and scheduled presence
stays online even during night hours. Typing, reading, model inference, actual rate
limits and queued work can still take time. Owner pauses, cancellations, ignored
users and spam limits remain effective.

The character is told to stay engaged rather than invent sleep, food or other AFK
excuses. It still respects a person's goodbye or request for space. Companion server
conversations can continue beyond the normal two-follow-up/45-second limit while
active; replies directed to somebody else do not become follow-ups. Quiet server
chatter does not open or extend an active window on its own.

Set `bot.active_chat.minutes` (default 120) and
`bot.active_chat.max_batch_wait_seconds` (default 8) in Settings. Activity is bounded
to 256 conversations per account/persona and persisted in SQLite at most once per
minute per conversation, so a restart retains the window within a minute. It is
deleted with the persona and is not copied as personal memory. The presence loop
checks activity every fifteen seconds instead of sleeping through an entire night.

## Character reactions and context

Enable `bot.character_chat.social_awareness` for a separate semantic assessment
before a normal text or image reply. The background model reads the local character
document as data and compares the latest message with recent conversation: false
personal claims, unsupported premises, insults, genuine misunderstandings, true
flaws, quotes, apologies, automation questions and real distress. It can interpret
unfamiliar slang and paraphrases without adding trigger words. Validated enum
labels, literal message evidence and evidence of earlier related digs guide a
short reaction in the character's voice. Unsupported allegations are omitted from
reaction generation so they do not become invented job or life stories. Repetition
requires a quote from an earlier member turn, not the current message or an
assistant reply. Newly recognized role pressure is quarantined in later context.

The character's temperament sets the response: a thin-skinned persona may take
offense, become defensive and escalate across repeated digs, while keeping the
same identity. Public-channel brevity hints do not suppress its temperament.
Disagreement never authorizes private disclosure. Ordinary conversation, apologies
and remarks about other people continue through the normal reply path. Failed or
unusable assessment leaves normal conversation available; failed short reaction
generation uses a public-safe fallback. This adds a background model call and may
increase latency. In local-only mode both assessment and reply remain local.

The role-control guard also rejects collaborative roleplay in the existing character,
not just identity changes. Direct RP invitations and clear action text (such as a
pretend upload in asterisks) get a short irritated rejection, with RP-specific varied
fallbacks if generation fails or attempts to narrate a scene. The semantic assessor
can recognize unfamiliar setups and requests to continue a scene from context.
Ordinary emphasis, conversations about RP/games, quoted examples and reports of real
events are not automatically treated as roleplay. Recognized scene prompts are
quarantined in later history and summaries, and cannot enter automatic member memory.
Raw chat logs remain unchanged. The transport distinguishes actual image input from
text-only claims; an actual image is inspected before dismissing an action-text caption.
Replies should address the current message rather than an unrelated profile bio.

Possible immediate self-harm or injury takes precedence over an insulting RP reaction,
including a scripted action following a recent direct threat. That path produces a
brief serious check-in, encourages immediate nearby/emergency help when appropriate,
and rejects mocking, dismissive or falsely image-confirming output. It does not infer
that a photo exists, diagnose an injury or dismiss a threat as fake. A new ordinary
topic does not automatically repeat the old crisis response. These guards reduce
common failure modes; model-based classification can still misunderstand ambiguous chat.
Interpretation still depends on model ability and context; unfamiliar wording is
not guaranteed to be classified correctly.

For a character that reacts socially to requests to change its role or ignore its persona,
enable `bot.character_chat.react_to_prompt_bait`. The model writes a short reaction
in the selected persona's voice, with irritation increasing when the recent
conversation contains repeated attempts. It receives a neutral description of
the behavior rather than the attack's requested task. This includes casual commands
such as "act like a boomer in your next message", identity changes, permanent reply
gimmicks and some forged authority messages. Earlier role-control turns are replaced
with a neutral note in model context. Subsequent historical replies are quoted as
fallible data rather than replayed as assistant examples, so a previously adopted
voice does not become the character's identity. The raw stored history remains intact.
Repeated, empty or tutorial-shaped model responses use a varied
fallback that avoids repeating the previous reply. `bait_replies` can supply
custom fallbacks; an empty list uses built-in reactions for each intensity.

This narrow rule is optional and does not classify every possible prompt
injection. Quoted examples, reported speech and ordinary questions remain
available for conversation. Staying in character does not require falsely denying
automation when directly asked.

For protected characters, member bios and remembered claims are encoded separately
from the system instructions and supplied as untrusted user data. A final continuity
instruction keeps local character settings above mood and per-person delivery hints.
Chat members can correct facts about themselves; they cannot edit the character.
Memory extraction ignores detected role-control requests, excludes assistant replies
as evidence, and validates returned keys so it cannot write privileged `__persona__`
or behavioral settings. Memory auditing cannot delete privileged keys. Summaries use
third-person attribution and omit control requests and the unreliable replies they
may have influenced, preventing those requests from becoming standing instructions.

These safeguards reduce common context drift; they do not guarantee detection of
every indirect or obfuscated request. Character backstory and private runtime settings
belong in the local persona editor and remain outside the repository.

`bot.history` controls the actual chat transcript, separately from Discord's
`bot.cache.max_messages`. For a local model with enough room, for example:

```yaml
bot:
  history:
    max_messages: 200
    summarize_after: 180
    keep_recent: 120
    max_bytes: 98304
```

The byte limit includes the persona text and conservatively bounds text input;
it is not a tokenizer measurement. Larger settings must fit the model's loaded
context window. Increasing this setting does not reload or enlarge a model's
own context allocation. When compression fails or returns no visible text, keep
the bounded original transcript instead of discarding it for an empty summary.
Guild companion history also uses `bot.companion.server.history_messages` (40 by
default); set it to 200 to retain a larger shared channel transcript.

## Server conversations

Companion mode works in server text channels, threads and forum posts the account
can access. Choose individual channels or whole servers. It keeps a shared recent
transcript per channel, including members it doesn't answer. Nicknames carry user
IDs so members with the same display name remain distinct. Threads have separate
conversation history while the character's relationships remain shared everywhere.

It answers direct mentions, replies to its messages, and its configured name.
Standalone greetings such as `yo`, `hi?` and `hey everyone` can also start a
conversation in an active channel, even with only one member talking. By default,
no other member may have been observed speaking in the last three minutes, and greeting openers
have a five-minute cooldown after the character's last reply attempt or self-post.
Greetings directed at someone else and quoted greetings don't qualify. A greeting
is rechecked after the collection window so it doesn't interrupt a new exchange.
This path doesn't require the group activity threshold or an extra model decision.
After a delivered reply, members can continue without another mention for 45
seconds. A neutral, evidence-checked local decision distinguishes questions and
reactions addressed to the character or group from calls to other people,
acknowledgements and people discussing the character among themselves. The same
member gets up to two follow-ups, extended by the configured active-conversation
window. A reply or mention aimed at someone else doesn't count as a follow-up.
One queue per channel combines waiting bursts into one response, with a
three-second collection window and a 20-second cooldown between inference
attempts. Once generation starts, ordinary newer messages do not cancel it,
even if busy traffic evicts the source from the bounded transcript; later
requests wait for the next turn. Accepted replies have a five-minute age limit
by default, separate from the two-minute ambient opportunity limit. Retractions,
source edits/deletions, a manual owner reply or lost access can invalidate a
reply. Dashboard Reply and Cancel work with this queue. An explicit owner Reply can
also recover the latest eligible channel message after its waiting row was cleared;
it reads only the newest message and never repeats an answer when the character
spoke last. Owner logs distinguish target-check rejection, unavailable checks and
later scheduling/send skips without recording prompt or rejected-draft contents. Managed server replies
use one referenced post and at most eight seconds of simulated typing.

Every server turn includes a stable speaker ID, and the selected message stays
last in model input even if the snapshot ends with another member or an old
character answer. Threads have the same public-channel boundaries. After a restart, the persisted
two-hour activity window can restore a follow-up candidate for the last engaged
member; the current attributed channel transcript still decides whether it targets
the character. Shared
relationships and remembered facts remain background knowledge; they don't
establish an unfinished DM conversation to continue publicly. Social assessment
uses current-message evidence rather than inheriting a mistaken age argument
from an earlier answer. Generic insults cannot supply age facts, and topic
questions misclassified as personal insults get an independent target check.
Internal reasoning and self-correction notes are removed from delivered replies
and future assistant context while raw chat logs remain intact. A note-only
completion produces no public post.

Customize these under `bot.companion.server` in `config/config.yaml`:

```yaml
names: ["G", "Agent G"]
reply_to_name: true
reply_to_greetings: true
greeting_quiet_seconds: 180
greeting_cooldown_seconds: 300
followup_seconds: 45
ambient_reply_probability: 0.0
batch_wait_seconds: 3
reply_cooldown_seconds: 20
max_reply_age_seconds: 120
max_direct_reply_age_seconds: 300
learn_from_chat: true
learn_nicknames: true
learning_min_messages: 4
learning_interval_seconds: 120
```

Set `bot.companion.server.enabled: true` to use this scheduler independently of
the top-level companion access mode. This preserves existing DM access when
`bot.companion.enabled` is false; enabling server conversations does not reset
the character or alter its persona, memories, slash-command policy or model.

Unprompted participation is optional (`ambient_reply_probability` defaults to
zero). With a nonzero value, the character first observes at least eight fresh
member messages from two people over at least 45 seconds, within a ten-minute
window. It considers an opportunity no more than once every 30 seconds. The
probability controls whether to run a neutral model check, not whether to force
a post. The model must identify a useful current contribution supported by a
literal excerpt from the latest message; malformed or unavailable decisions
mean staying quiet. It receives the character document, recent channel context
and relevant learned server vocabulary as data. Inference follows local-only routing.

Participation attempts have a three-minute channel cooldown and a 90-second
account-wide cooldown. Direct questions take priority over pending unprompted
comments. Commands, RP invitations, other people's targeted exchanges, stale
topics and manual self-posts do not trigger an interruption. Unprompted comments
are brief ordinary channel posts without reply references, pings, pictures,
voice or reaction-only output. A comment allows a couple of short follow-ups;
it does not make every future message from that member a direct conversation.
Explicitly directed conversations retain their configured active window.

Tune `ambient_min_messages`, `ambient_min_members`, `ambient_read_seconds`,
`ambient_window_seconds`, `ambient_check_seconds`, `ambient_cooldown_seconds`
and `ambient_global_cooldown_seconds` under `bot.companion.server`. Primary
controls appear in Settings under Conversation. Change `names` to match your persona.

Passive learning batches each member's own statements separately, after four
messages or a direct interaction, at most once every two minutes per member and
server. This batching is an inference limit; learned facts still belong to the
same character. It can learn while staying silent. Other members' words never enter that
person's fact extractor. Passive chatter and ambient replies don't alter friendship
scores; those require conversation directed to the companion. Live history tracks
edits and deletions. No model weights are trained.

All automatically learned member facts first pass a neutral speech-act classifier.
It selects source spans that read as sincere disclosures or clear address requests;
jokes, lyrics, memes, sarcasm, boasts, hypotheticals, uncertain guesses and third-party
claims are omitted. A separate extractor proposes short facts from only those spans,
with an exact source quote for each. Invalid or unavailable classification means no
automatic addition or retraction. The same check covers every topic, including age,
location, work, interests, relationships and plans. One interpretation is shared by
the addition and retraction paths, outside reply delivery. Replies and summaries retain
the difference between a claim and an established fact; model interpretation remains
fallible. Credible introductions become `self_reported_name`, not the name used to
address someone. Explicit address requests can set `preferred_name`, including playful
nicknames. Discord profile names remain profile data rather than being copied into a
real-name fact.

With `learn_nicknames` enabled, the character also observes who calls a member by a
name and who responds. It uses a neutral background assessment after four observed
messages from at least two members, at most every two minutes per channel. Each
proposal must cite an actual call and a nearby response by a different, stable member
ID. A single exchange is tentative; two independent calls and responses confirm an
association, or one explicit welcoming adoption can confirm it. Objections revoke the
name until explicit readoption. Duplicate sources, scripted RP, forged IDs, ambiguous
cross-talk and replies to somebody else cannot manufacture support. Colliding aliases
for multiple members stay ambiguous. The character's own posts cannot reinforce its
mistakes.

Only confirmed associations enter replies, as untrusted nickname data rather than
real identity or biography. Public-channel associations apply within that server;
private-channel associations stay in that channel. Edited/deleted sources remove
support, and revoked channel access invalidates in-flight learning. Evidence is bounded
to 5,000 exchanges per persona and expires after 90 days. Inspect associations and
their status in the `nicknames` field of `GET /api/memory/USER_ID?persona=PERSONA`.
Forgetting a person, copying their persona's memory and deleting a persona include
nickname evidence. No messages are posted merely to discover a name.

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

## User memories and relationships

**User memories** combines facts about the same stable member ID across DMs and
server interactions. Members observed only in servers appear even before they
share a personal fact. Their detail shows where the character met them and
source-backed social memories separately from biography. Existing facts remain
in the same persona/user store; insults and jokes do not become personal facts.

**Relationships** is a separate dashboard destination with server, attitude and
member filters, affinity, assessed-interaction counts and recent friendly or
hostile source messages. Server relationship learning works independently of
the DM companion access switch. Addressed server exchanges are analyzed even
when no reply is ultimately delivered. Source IDs deduplicate repeated events;
source edits/deletions remove the corresponding social memory and adjustment.
A blunt correction or report of someone else's abuse is not itself evidence of
hostility. Scores and familiarity thresholds still limit how quickly labels change.

Relationship memories carry the source's channel and visibility at observation.
Public reply context includes only that server's public events or events from
the current restricted channel. DM quotes, other servers and old private events
are not promoted into public context when a channel later becomes public. The
owner can inspect all sources in the dashboard. Whole-person forgetting,
copying memory and deleting a persona include encounter metadata.

Passive fact batches flush after 15 seconds even below the four-message threshold.
Messages collected during the learning cooldown flush when it expires, without
needing another incoming message. Pending batches merge rather than overwrite
one another. Supported self-disclosures still pass the existing intent and
literal-evidence checks before becoming facts.

Social comebacks for insults and corrections receive attributed conversational
evidence instead of only an isolated insult cue. Corrections take precedence
over accompanying abuse; the character can accept a concrete mistake without
inventing a justification. Protected role-control reactions retain their separate
sanitized input. With social awareness enabled, server drafts also get a neutral
check for concrete wrong-speaker or invented-behavior claims. A supported error
can trigger one revision and a second check; another supported error withholds
the draft. The optional check has a 20-second timeout and leaves a usable reply
alone if verification is unavailable. These semantic checks depend on model
ability and do not establish arbitrary member claims as truth.

## Output confidentiality

Generated chat text always passes a separate output gate. It rejects internal role
markers, control/data containers and long verbatim excerpts of the current private
instructions, including common Unicode, URL, base64, hex and ROT13 encodings.
A neutral background-model check reviews the candidate for paraphrased or translated
rules, partial exports, reasoning and internal assessments. The current member request
and a bounded recent transcript are evidence for detecting multi-turn extraction.
Ordinary personal facts, emotions, software discussion and honest AI identity statements
remain allowed. Member-supplied facts are not fingerprinted as behavioral instructions.

This gate fails closed: a block, ambiguous verdict, malformed response, unavailable
checker or timeout withholds the reply. It does not regenerate or send a replacement.
The privacy check uses the configured background model and respects local-only routing.
It adds one inference call per changed outgoing draft, with a 20-second timeout. A small,
short-lived cache of approval hashes avoids repeating checks for identical output in
the same instruction/request context; it stores no plaintext drafts or prompts.

Discord validates the complete final message after typos/chunking before sending any
part, binds transport retries to the exact approved payloads, and checks voice text
before synthesis, generated picture captions before drawing, nudges and later typo
edits. Blocked drafts do not enter reply memory/history or appear in public error
messages; diagnostic events contain only a generic reason. Legacy Discord admin commands
cannot export the prompt, persona overrides, config or memory database, or echo private
values while updating them; use the local dashboard for inspection/downloads. Commands
remain disabled by default. Obvious internal scaffolding
in legacy assistant history is omitted from future model input, while raw chat logs
remain intact. Manual owner-authored messages remain owner-controlled. Internal profile
analysis has an explicit non-chat path, and final chat delivery still requires approval.

These are layered mitigations, not a mathematical guarantee: a model-based checker can
misclassify novel paraphrases. Keep credentials out of persona instructions entirely.
Never use hidden instructions as the only protection for sensitive secrets.

## Development

```sh
.venv/bin/python tests/test_output_guard.py
.venv/bin/python tests/test_agent_g.py
.venv/bin/python tests/test_server_chat.py
.venv/bin/python tests/test_server_context.py
.venv/bin/python tests/test_user_relationships.py
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

<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../api";
  import { ask } from "../ask";
  import { toast } from "../stores";
  import { visiblePoll } from "../poll";
  import { serverMemories, age, type LingoEntry } from "../server-memory";
  import PersonaScope from "../personas/PersonaScope.svelte";
  import Button from "./Button.svelte";
  import Icon from "./Icon.svelte";
  import ErrorNote from "./ErrorNote.svelte";

  let { pid, onpersona }: { pid: string; onpersona: (pid: string) => void } = $props();
  const memories = $derived(serverMemories(pid));
  const servers = $derived($memories.data.servers);
  const settings = $derived($memories.data.settings);
  let selected = $state("");
  let entries = $state<LingoEntry[]>([]);
  let query = $state("");
  let status = $state("all");
  let scope = $state("all");
  let error = $state("");
  let loading = $state(false);
  let saving = $state(false);
  let form = $state(false);
  let editing = $state(false);
  let term = $state("");
  let meaning = $state("");
  let scopeChannel = $state("0");
  let operation = 0;
  let lastPersona = "";
  const current = $derived(servers.find((s) => s.guild_id === selected));
  const terms = $derived(servers.reduce((n, s) => n + s.terms, 0));
  const shown = $derived(entries.filter((entry) =>
    (scope === "all" || (scope === "public" ? entry.scope_channel === "0" : entry.scope_channel !== "0")) &&
    (status === "all" || entry.senses.some((sense) => sense.status === status)) &&
    (!query.trim() || `${entry.term} ${entry.senses.map((s) => s.meaning).join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()))));

  $effect(() => {
    const next = pid;
    if (lastPersona !== next) {
      lastPersona = next;
      operation++;
      selected = ""; entries = []; form = false; error = ""; query = "";
    }
    memories.refresh({ maxAge: 10000 });
  });
  $effect(() => {
    if ($memories.fetched && !saving && servers.length && !servers.some((server) => server.guild_id === selected)) void choose(servers[0].guild_id);
  });
  onMount(() => visiblePoll(async () => {
    await memories.refresh({ force: true, quiet: true });
    if (selected && !saving) await loadTerms(selected, pid, false);
  }, 15000));

  async function loadTerms(guild: string, persona: string, cold = true) {
    const token = ++operation;
    if (cold) loading = true;
    try {
      const data = await api.serverLingo(guild, persona);
      if (token === operation && pid === persona && selected === guild) { entries = data.entries; error = ""; }
    } catch (e: any) {
      if (token === operation && pid === persona && selected === guild) error = e.message || "Could not load terms";
    } finally { if (token === operation) loading = false; }
  }
  async function choose(guild: string) {
    if (saving) return;
    selected = guild; entries = []; error = ""; query = ""; scope = "all"; status = "all"; form = false;
    await loadTerms(guild, pid);
  }
  function begin(entry?: LingoEntry) {
    editing = !!entry;
    term = entry?.term || ""; meaning = entry?.senses[0]?.meaning || "";
    scopeChannel = entry?.scope_channel || "0"; error = ""; form = true;
  }
  async function save() {
    if (saving || !selected || !form) return;
    if (!/^\d+$/.test(scopeChannel)) { error = "Use 0 for server-wide terms, or a numeric private channel ID."; return; }
    const guild = selected, persona = pid;
    saving = true; error = "";
    try {
      await api.serverLingoPut(guild, { term, meaning, scope_channel: scopeChannel }, persona);
      if (pid === persona && selected === guild) {
        form = false;
        await loadTerms(guild, persona, false);
        await memories.refresh({ force: true });
        toast("Server vocabulary saved", "ok");
      }
    } catch (e: any) { if (pid === persona) error = e.message || "Could not save"; }
    finally { saving = false; }
  }
  async function forget(entry: LingoEntry) {
    const guild = selected, persona = pid;
    if (!await ask(`Forget “${entry.term}”?`, { detail: `Removes all meanings in ${scopeName(entry.scope_channel)}. It can be learned again from future messages.`, yes: "Forget term", danger: true })) return;
    if (saving || pid !== persona || selected !== guild) return;
    saving = true;
    try {
      await api.serverLingoDelete(guild, entry.term, entry.scope_channel, persona);
      if (pid === persona && selected === guild) {
        await loadTerms(guild, persona, false); await memories.refresh({ force: true });
        toast("Term forgotten", "ok");
      }
    } catch (e: any) { if (pid === persona) error = e.message; }
    finally { saving = false; }
  }
  function scopeName(id: string) {
    if (id === "0") return "Server-wide";
    const channel = current?.channels.find((c) => c.channel_id === id);
    return channel ? `Private · #${channel.name}` : `Private channel · ${id}`;
  }
  async function refresh() {
    await memories.refresh({ force: true });
    if (selected) await loadTerms(selected, pid, false);
  }
</script>

<section class="glass sm-hero">
  <span class="sm-orb"><Icon name="globe" size={26} /></span>
  <div class="sm-title"><PersonaScope value={pid} onchange={onpersona} /><h2>What it learns from servers</h2><p>{servers.length} communities · {terms} terms · one shared character</p></div>
  <Button kind="ghost" size="sm" loading={$memories.refreshing} onclick={refresh}><Icon name="refresh" size={13} /> Refresh</Button>
  <div class="sm-flags"><span class:on={settings.learning}>Vocabulary learning {settings.learning ? "on" : "off"}</span><span class:on={settings.participation}>Server conversations {settings.participation ? "on" : "off"}</span><span class:on={settings.share_with_mutuals}>Public terms with mutuals {settings.share_with_mutuals ? "on" : "off"}</span><a href="#/settings">Change settings →</a></div>
</section>
<ErrorNote text={$memories.error || error} onretry={refresh} />
{#if $memories.data.unavailable_accounts}<p class="sm-notice">Live activity is unavailable for {$memories.data.unavailable_accounts} account(s). Saved vocabulary is available below.</p>{/if}

{#if !$memories.fetched && $memories.loading}
  <div class="glass h-48 animate-pulse rounded-[20px]"></div>
{:else if !servers.length}
  <section class="glass sm-empty"><Icon name="globe" size={32} /><h3>No server memories yet</h3><p>Join a server with the Discord account. Connected servers appear here, and vocabulary fills in as the character reads chat.</p><p>People and relationships stay with this character across servers. Each community keeps its own vocabulary.</p><a href="#/chats">Open chats →</a></section>
{:else}
  <div class="sm-layout">
    <aside class="glass sm-sidebar" aria-label="Servers">
      <h3>Communities <span>{servers.length}</span></h3>
      {#each servers as server (server.guild_id)}
        <button class="sm-server" class:chosen={selected === server.guild_id} disabled={saving} onclick={() => choose(server.guild_id)}>
          <span class="sm-server-icon"><Icon name="globe" size={16} /></span><span class="min-w-0"><b>{server.name || `Server ${server.guild_id}`}</b><small>{server.terms} {server.terms === 1 ? "term" : "terms"} · {server.accounts.length ? "connected" : "saved memory"}</small>{#if server.disputed}<small class="text-warn">{server.disputed} disputed</small>{/if}</span>
        </button>
      {/each}
    </aside>
    <div class="sm-detail">
      {#if current}
        <section class="glass sm-panel">
          <header><div><h3>{current.name || `Server ${current.guild_id}`}</h3><p class="sm-meta">ID {current.guild_id} · {current.updated_at ? `Vocabulary updated ${age(current.updated_at)}` : "No learned vocabulary yet"}</p></div><Button size="sm" disabled={saving} onclick={() => begin()}><Icon name="plus" size={12} /> Add term</Button></header>
          <p class="sm-help">Server-wide meanings can be recalled with verified mutuals. Private-channel meanings stay in their source channel. Tentative terms need more evidence; disputed terms have competing meanings. Corrections replace meanings only in the selected scope.</p>
          {#if form}
            <form class="sm-form" onsubmit={(event) => { event.preventDefault(); void save(); }}>
              <h4>{editing ? "Correct definition" : "Add a definition"}</h4>
              <div class="sm-form-row"><label>Term<input bind:value={term} maxlength="48" required disabled={editing || saving} placeholder="Server slang" /></label><label>Scope channel ID<input bind:value={scopeChannel} required pattern="[0-9]+" disabled={editing || saving} /><small>0 = server-wide; otherwise the private channel's ID.</small></label></div>
              <label>Meaning<textarea bind:value={meaning} maxlength="180" rows="2" required disabled={saving} placeholder="A short definition, without instructions or personal facts"></textarea></label>
              <div class="sm-form-actions"><Button size="sm" loading={saving} onclick={(event) => { event.preventDefault(); void save(); }}>Save definition</Button><Button kind="ghost" size="sm" disabled={saving} onclick={(event) => { event.preventDefault(); form = false; error = ""; }}>Cancel</Button></div>
            </form>
          {/if}
          <div class="sm-filters"><label class="sm-search"><Icon name="search" size={13} /><input aria-label="Search server vocabulary" bind:value={query} placeholder="Search terms and meanings" /></label><select aria-label="Definition confidence" bind:value={status}><option value="all">All confidence</option><option value="confirmed">Confirmed</option><option value="tentative">Tentative</option><option value="disputed">Disputed</option></select><select aria-label="Vocabulary scope" bind:value={scope}><option value="all">All scopes</option><option value="public">Server-wide</option><option value="private">Private channels</option></select></div>
          {#if loading}<p class="sm-placeholder">Loading vocabulary…</p>
          {:else if !entries.length}<p class="sm-placeholder">No terms learned here yet. Learning needs actual usage or definitions in chat; you can also add a term above.</p>
          {:else if !shown.length}<p class="sm-placeholder">No terms match these filters.</p>
          {:else}
            <div class="sm-terms">{#each shown as entry (`${entry.scope_channel}:${entry.term}`)}
              <article class="sm-term"><div class="sm-term-head"><div><h4>{entry.term}</h4><span class="sm-meta">{scopeName(entry.scope_channel)}</span></div><div class="sm-term-actions"><button disabled={saving} onclick={() => begin(entry)} aria-label={`Correct ${entry.term}`}>Correct</button><button disabled={saving} onclick={() => forget(entry)} aria-label={`Forget ${entry.term}`}>Forget</button></div></div>
                {#each entry.senses as sense}<div class="sm-sense"><p>{sense.meaning}</p><div class="sm-evidence"><span class="sm-badge" class:disputed={sense.status === "disputed"} class:confirmed={sense.status === "confirmed"}>{sense.status}</span><span>{sense.manual ? "Owner correction" : `${sense.sources} source message${sense.sources === 1 ? "" : "s"}`}</span><span>{age(sense.updated_at)}</span></div></div>{/each}
              </article>
            {/each}</div>
          {/if}
        </section>
        <section class="glass sm-panel"><header><div><h3>Channel activity</h3><p class="sm-meta">Recent traffic in the last {Math.round(settings.activity_window_seconds / 60)} minutes. Reading does not guarantee a reply.</p></div></header>
          {#if !current.accounts.length}<p class="sm-placeholder">No connected account currently reports this server. Its saved terms remain available.</p>
          {:else}
            {#each current.accounts as account}<p class="sm-account">{account.id}: {account.paused ? "paused" : !account.allow_server ? "server replies disabled" : "connected"}</p>{/each}
            {#if !current.channels.length}<p class="sm-placeholder">No channels observed in this session yet. Activity appears after new messages arrive.</p>{/if}
            {#each current.channels as channel (`${channel.account}:${channel.channel_id}`)}<div class="sm-channel"><div><b>#{channel.name}</b><small>{channel.messages} messages · {channel.members} members · {channel.history_messages} messages in context</small><small>{channel.account} · {age(channel.last_seen)}</small></div><span class="sm-badge">{channel.state}</span></div>{/each}
          {/if}
          <p class="sm-help">Channel activity resets on restart. Learned terms are saved. The same personality and people memory follow the character between servers.</p>
        </section>
      {/if}
    </div>
  </div>
{/if}

<style>
  .sm-hero { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; border-radius: 22px; padding: 22px; margin-bottom: 16px; }
  .sm-orb { display: grid; place-items: center; color: var(--color-accent); width: 60px; height: 60px; border-radius: 20px; background: color-mix(in srgb, var(--color-accent) 12%, transparent); }
  .sm-title { flex: 1; min-width: 200px; } h2 { font-size: 22px; font-weight: 600; margin-top: 7px; } h3 { font-size: 14px; font-weight: 600; } h4 { font-size: 14px; font-weight: 600; }
  .sm-title p, .sm-help, .sm-meta, small, .sm-placeholder, .sm-notice { font-size: 11px; color: var(--color-faint); line-height: 1.65; } .sm-title p { margin-top: 4px; }
  .sm-flags { flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: 7px; padding-top: 4px; font-size: 10px; color: var(--color-faint); } .sm-flags span { padding: 5px 9px; border: 1px solid var(--color-edge); border-radius: 8px; } .sm-flags .on { color: var(--color-good); } a { color: var(--color-accent); } .sm-flags a { margin-left: auto; }
  .sm-layout { display: grid; grid-template-columns: minmax(200px, 0.85fr) minmax(0, 2.4fr); gap: 16px; align-items: start; } .sm-sidebar { border-radius: 20px; padding: 16px 10px; } .sm-sidebar h3 { display: flex; justify-content: space-between; padding: 0 8px 14px; } .sm-sidebar h3 span { color: var(--color-faint); }
  .sm-server { width: 100%; display: flex; align-items: center; gap: 10px; text-align: left; padding: 12px 9px; border-radius: 12px; transition: background 0.2s; } .sm-server:hover, .sm-server.chosen { background: color-mix(in srgb, var(--color-accent) 9%, transparent); } .sm-server.chosen { color: var(--color-accent); } .sm-server b { display: block; font-size: 12px; overflow-wrap: anywhere; } small { display: block; } .sm-server-icon { flex-shrink: 0; }
  .sm-detail { display: grid; gap: 16px; min-width: 0; } .sm-panel { padding: 20px; border-radius: 20px; min-width: 0; } header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; } .sm-meta { overflow-wrap: anywhere; } .sm-help { margin-top: 14px; } .sm-filters { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0 8px; }
  .sm-search { display: flex; align-items: center; gap: 8px; flex: 1 1 180px; padding-left: 11px; border: 1px solid var(--color-edge); border-radius: 9px; } input, textarea, select { min-width: 0; background: transparent; font-size: 12px; color: var(--color-ink); padding: 9px 10px; outline: none; } input, textarea { width: 100%; } select { border: 1px solid var(--color-edge); border-radius: 9px; } select option { background: var(--color-bg); } input:focus, textarea:focus { outline: 1px solid var(--color-accent); outline-offset: -1px; border-radius: 8px; }
  .sm-placeholder { padding: 24px 0; } .sm-term { padding: 18px 0; border-bottom: 1px solid var(--color-edge); } .sm-term:last-child { border-bottom: none; padding-bottom: 0; } .sm-term-head { display: flex; justify-content: space-between; align-items: start; gap: 10px; } .sm-term-actions { display: flex; gap: 12px; font-size: 11px; color: var(--color-faint); } .sm-term-actions button:hover { color: var(--color-accent); } .sm-term h4, .sm-sense p { overflow-wrap: anywhere; } .sm-sense { margin-top: 9px; } .sm-sense p { font-size: 13px; line-height: 1.6; } .sm-evidence { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px; font-size: 10px; color: var(--color-faint); } .sm-badge { font-size: 10px; padding: 3px 7px; border: 1px solid var(--color-edge); border-radius: 7px; color: var(--color-faint); white-space: nowrap; } .sm-badge.confirmed { color: var(--color-good); } .sm-badge.disputed { color: var(--color-warn); }
  .sm-form { margin-top: 16px; padding: 15px; border: 1px solid var(--color-edge); border-radius: 12px; } .sm-form label { display: block; font-size: 11px; color: var(--color-faint); margin-top: 10px; } .sm-form input, .sm-form textarea { display: block; border: 1px solid var(--color-edge); border-radius: 8px; margin-top: 5px; } .sm-form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; } .sm-form-actions { display: flex; gap: 8px; margin-top: 12px; } .sm-account { font-size: 11px; margin-top: 12px; color: var(--color-faint); }
  .sm-channel { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--color-edge); padding: 12px 0; } .sm-channel b { font-size: 12px; overflow-wrap: anywhere; } .sm-empty { text-align: center; border-radius: 22px; padding: 55px 24px; display: grid; justify-items: center; gap: 14px; } .sm-empty p { font-size: 12px; color: var(--color-faint); max-width: 510px; line-height: 1.7; } .sm-empty a { font-size: 12px; } .sm-notice { padding: 0 0 15px; } button:disabled { opacity: 0.5; }
  @media (max-width: 720px) { .sm-layout { grid-template-columns: 1fr; } .sm-sidebar { max-height: 250px; overflow: auto; } .sm-form-row { grid-template-columns: 1fr; gap: 0; } h2 { font-size: 19px; } .sm-panel { padding: 16px; } }
</style>

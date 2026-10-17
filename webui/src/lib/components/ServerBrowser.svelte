<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { api } from "../api";
  import { toast } from "../stores";
  import { visiblePoll } from "../poll";
  import { memorySection } from "../server-memory";
  import { viewPersona } from "../stores";
  import Button from "./Button.svelte";
  import Icon from "./Icon.svelte";
  import type { ChatUser } from "../chats";
  import { refreshServerChannels } from "../chats";

  type Guild = { id: string; name: string; icon: string; members?: number };
  type Channel = { id: string; name: string; category: string; thread: boolean; parent: string;
                   topic: string; can_read: boolean; can_send: boolean; reason: string; slowmode: number;
                   active: boolean; origin: "selected" | "discovered" | "inactive";
                   discovery?: { source_channel_name: string; added_at: number } };
  let { target, selectedId, pending = [], onselect, onclear }: { target: string; selectedId: string; pending?: ChatUser[]; onselect: (channel: ChatUser) => void; onclear: () => void } = $props();
  let servers = $state<Guild[]>([]);
  let guild = $state("");
  let persona = $state("default");
  let channels = $state<Channel[]>([]);
  let query = $state("");
  let loading = $state(false);
  let error = $state("");
  let invite = $state("");
  let joining = $state(false);
  let inspecting = $state(false);
  let showJoin = $state(false);
  let preview = $state<{ invite: string; guild: Guild; already_joined: boolean } | null>(null);
  let inspectedTarget = "";
  let generation = 0;
  let joinedPending = $state<Guild | null>(null);
  let channelLoading = $state(false);
  let chosen = $state<string[]>([]);
  let explore = $state(true);
  let dirty = $state(false);
  let saving = $state(false);
  const pendingGroups = $derived([...new Set(pending.map((row) => row.guild_id || row.guild_name || "Server"))]
    .map((id) => ({ id, name: pending.find((row) => (row.guild_id || row.guild_name || "Server") === id)?.guild_name || "Server",
                   channels: pending.filter((row) => (row.guild_id || row.guild_name || "Server") === id) })));
  const stateOf = (row: ChatUser) => ({ waiting: "Waiting to reply", writing: "Writing", checking: "Checking reply", sending: "Sending", queued: "Queued",
    cooldown: "Cooldown", skipped: "Skipped", failed: "Needs attention" }[row.state?.state || ""] || "Awaiting response");
  async function openPending(row: ChatUser) {
    const account = target;
    const expectedGuild = row.guild_id || guild;
    if (row.guild_id && row.guild_id !== guild) await chooseGuild(row.guild_id);
    if (target !== account || guild !== expectedGuild) return;
    const channel = channels.find((item) => item.id === row.channel_id);
    if (channel) await open(channel);
    else error = "This channel is not available in the current server list. Refresh servers and try again.";
  }
  const current = $derived(servers.find((s) => s.id === guild) ?? joinedPending);
  const groups = $derived.by(() => {
    const rows = channels.filter((c) => `${c.name} ${c.topic} ${c.parent}`.toLowerCase().includes(query.toLowerCase()));
    return [...new Set(rows.map((c) => c.category))].map((name) => ({ name, channels: rows.filter((c) => c.category === name) }));
  });

  $effect(() => {
    const account = target;
    untrack(() => {
      generation++; guild = ""; channels = []; servers = []; error = ""; preview = null; joinedPending = null;
      chosen = []; explore = true; dirty = false; saving = false;
      if (account) void load(account);
    });
  });
  onMount(() => visiblePoll(async () => {
    if (target && !joining) {
      await load(target, true);
      if (selectedId.startsWith("ch")) await watch(selectedId.slice(2), target);
    }
  }, 30000));
  async function load(account = target, quiet = false) {
    const token = generation;
    if (!quiet) loading = true;
    try {
      const data = await api.servers(account);
      if (target !== account || token !== generation) return;
      if (!data.ok) throw new Error(data.reason || "Could not load servers");
      servers = data.servers;
      persona = data.persona || "default";
      if (joinedPending && servers.some((s) => s.id === joinedPending?.id)) {
        const joinedId = joinedPending.id; joinedPending = null; await chooseGuild(joinedId);
      }
      error = "";
      if (!guild && servers.length) await chooseGuild(servers[0].id);
      else if (guild && !joinedPending && !servers.some((s) => s.id === guild)) { guild = ""; channels = []; onclear(); }
      else if (quiet && guild && !dirty && !saving && !channelLoading) await readChannels(guild, account, token);
    } catch (e: any) { if (target === account && token === generation) error = e.message; }
    finally { if (target === account && token === generation) loading = false; }
  }
  async function chooseGuild(id: string) {
    if (joining || saving) return;
    if (guild !== id) onclear();
    guild = id; channels = []; chosen = []; query = ""; dirty = false; channelLoading = true;
    const account = target, token = generation;
    await readChannels(id, account, token);
    if (guild === id && account === target && token === generation) channelLoading = false;
  }
  async function readChannels(id: string, account: string, token: number) {
    try {
      const data = await api.serverChannels(id, account);
      if (guild !== id || account !== target || token !== generation || dirty || saving) return;
      if (!data.ok) throw new Error(data.reason || "Could not read channels");
      channels = data.channels; chosen = channels.filter((c) => c.active).map((c) => c.id);
      explore = data.activity?.explore ?? true; error = "";
    } catch (e: any) { if (guild === id && account === target && token === generation) error = e.message; }
  }
  function toggleChannel(id: string, checked: boolean) {
    chosen = checked ? [...chosen, id] : chosen.filter((cid) => cid !== id);
    dirty = true;
  }
  async function saveActivity() {
    if (saving || !guild) return;
    const account = target, id = guild, token = generation;
    saving = true; error = "";
    try {
      const data = await api.serverActivity(id, account, chosen, explore);
      if (guild !== id || account !== target || token !== generation) return;
      if (!data.ok) throw new Error(data.reason || "Could not save channel activity");
      channels = data.channels; chosen = channels.filter((c) => c.active).map((c) => c.id);
      explore = data.activity.explore; dirty = false;
      toast(chosen.length ? "Channel activity saved" : "Automatic activity stopped in this server", "ok");
      void refreshServerChannels(account, { force: true, quiet: true });
    } catch (e: any) { if (guild === id && account === target && token === generation) error = e.message; }
    finally { if (account === target && token === generation) saving = false; }
  }
  async function watch(id: string, account: string) {
    try { await api.serverWatch(id, account); } catch { /* the next heartbeat retries; history remains readable */ }
  }
  async function open(channel: Channel) {
    if (!current || !channel.can_read) return;
    onselect({ id: `ch${channel.id}`, kind: "server", channel_id: channel.id, name: `#${channel.name}`,
               channel_name: channel.name, guild_id: current.id, guild_name: current.name,
               where: `#${channel.name} · ${current.name}`, snippet: "", count: 0,
               can_send: channel.can_send, channel_reason: channel.reason });
    await watch(channel.id, target);
  }
  async function inspect() {
    if (inspecting || joining || !target) return;
    const account = target, value = invite;
    inspecting = true; preview = null; error = "";
    try {
      const data = await api.serverInvite(value, account);
      if (account !== target || value !== invite) return;
      if (!data.ok) throw new Error(data.reason);
      inspectedTarget = account; preview = data;
    } catch (e: any) { if (account === target) error = e.message; }
    finally { inspecting = false; }
  }
  async function join() {
    if (!preview || joining || target !== inspectedTarget) return;
    const account = target, value = preview;
    joining = true; error = "";
    try {
      const data = await api.serverJoin(value.invite, value.guild.id, account);
      if (account !== target) return;
      if (!data.ok) throw new Error(data.reason);
      joinedPending = data.guild; showJoin = false; preview = null; invite = "";
      toast(data.already_joined ? `Already in ${data.guild.name}` : `Joined ${data.guild.name}`, "ok");
      // The gateway's guild-create event may arrive after the join response.
      joining = false;
      await load(account, true);
      if (servers.some((s) => s.id === data.guild.id)) await chooseGuild(data.guild.id);
    } catch (e: any) { if (account === target) error = e.message; }
    finally { joining = false; }
  }
</script>

<div class="sb-browser">
  <div class="sb-actions"><h2>Servers</h2><button onclick={() => load()} title="Refresh servers" aria-label="Refresh servers"><Icon name="refresh" size={14} /></button><button class="sb-add" onclick={() => { showJoin = !showJoin; }} disabled={!target || joining}><Icon name="plus" size={12} /> Join server</button></div>
  {#if showJoin}
    <form class="sb-join" onsubmit={(e) => { e.preventDefault(); void inspect(); }}>
      <label>Discord invite<input bind:value={invite} oninput={() => { preview = null; }} disabled={joining} placeholder="https://discord.gg/…" autocomplete="off" /></label>
      <Button size="sm" kind="ghost" loading={inspecting} disabled={!invite.trim() || joining} onclick={(e) => { e.preventDefault(); void inspect(); }}>Preview invite</Button>
      {#if preview}<div class="sb-preview"><b>{preview.guild.name}</b>{#if preview.guild.members}<small>{preview.guild.members.toLocaleString()} members</small>{/if}<p>Join as the Discord account selected above.</p><Button size="sm" loading={joining} onclick={(e) => { e.preventDefault(); void join(); }}>{preview.already_joined ? "Open joined server" : "Join this server"}</Button></div>{/if}
    </form>
  {/if}
  {#if error}<p class="sb-error" role="alert">{error}</p>{/if}
  {#if pending.length}
    <section class="sb-pending" aria-label="Channel reply queue"><h3>Channel replies <span>{pending.length}</span></h3>
      {#each pendingGroups as group (group.id)}<div class="sb-pending-group"><h4>{group.name}</h4>
        {#each group.channels as row (row.id)}<button class="sb-pending-row" class:chosen={selectedId === row.id} onclick={() => openPending(row)}>
          <span class="sb-pending-top"><b>{row.name}</b><span class="sb-status">{stateOf(row)}</span></span>
          <small>{row.latest_author?.name ? `${row.latest_author.name}: ` : ""}{row.snippet || "Message waiting in this channel"}</small>
        </button>{/each}
      </div>{/each}
    </section>
  {/if}
  {#if loading && !servers.length}<p class="sb-empty">Loading this account's servers…</p>
  {:else if !servers.length && !joinedPending}<p class="sb-empty">No joined servers yet. Paste an invite with Join server to get started.</p>
  {:else}
    <label class="sb-label">Server<select value={guild} onchange={(e) => chooseGuild(e.currentTarget.value)} disabled={joining || saving}><option value="" disabled>Choose a server</option>{#each servers as server (server.id)}<option value={server.id}>{server.name}</option>{/each}</select></label>
    {#if joinedPending}<p class="sb-empty">Waiting for Discord to finish loading {joinedPending.name}. Refresh servers in a moment.</p>{/if}
    {#if guild && !joinedPending}
      <section class="sb-activity" aria-label="Channel activity settings">
        <h3>Channel activity <span>{chosen.length} active{dirty ? " · unsaved" : ""}</span></h3>
        <p>Check the channels where the character should start. Opening a channel only lets you browse it.</p>
        <label class="sb-explore"><input type="checkbox" checked={explore} disabled={saving} onchange={(e) => { explore = e.currentTarget.checked; dirty = true; }} />Explore through conversations</label>
        <p>After chatting, it can follow invitations or channel references into other public channels. Turning a channel off prevents rediscovery. No channels selected means no automatic activity.</p>
        <Button size="sm" loading={saving} disabled={!dirty || channelLoading} onclick={saveActivity}>Save channel activity</Button>
      </section>
    {/if}
    <label class="sb-search"><Icon name="search" size={12} /><input bind:value={query} placeholder="Find a channel" aria-label="Find a server channel" /></label>
    <div class="sb-channels">
      {#if channelLoading}<p class="sb-empty">Loading channels…</p>{:else if !channels.length}<p class="sb-empty">No readable text channels or active threads. Complete any server verification in Discord, then refresh.</p>{:else if !groups.length}<p class="sb-empty">No matching channels.</p>{/if}
      {#each groups as group (group.name)}<section><h3>{group.name}</h3>{#each group.channels as channel (channel.id)}
        <div class="sb-channel-row">
          <input class="sb-channel-check" type="checkbox" aria-label={`Activate #${channel.name}`} checked={chosen.includes(channel.id)} disabled={!channel.can_read || saving} onchange={(e) => toggleChannel(channel.id, e.currentTarget.checked)} />
          <button class="sb-channel" class:chosen={selectedId === `ch${channel.id}`} disabled={!channel.can_read} onclick={() => open(channel)} title={channel.topic || channel.reason || channel.name}><span class="sb-hash">{channel.thread ? "↳" : "#"}</span><span class="sb-channel-name">{channel.name}{#if channel.thread}<small>in #{channel.parent}</small>{/if}
            <small class:sb-status={channel.active}>{channel.active ? (channel.origin === "discovered" ? `Discovered via #${channel.discovery?.source_channel_name || "conversation"}` : "Selected") : "Inactive"}</small>
            {#if pending.find((row) => row.channel_id === channel.id)}{@const row = pending.find((row) => row.channel_id === channel.id)!}<small class="sb-status">{stateOf(row)}</small>{/if}</span>{#if !channel.can_send}<Icon name="eye" size={11} />{/if}</button>
        </div>
      {/each}</section>{/each}
    </div>
    <p class="sb-foot">Text channels and accessible active threads. <a href="#/memory" onclick={() => { viewPersona.set(persona); memorySection.set("servers"); }}>Server memories →</a> · <a href="#/relationships" onclick={() => viewPersona.set(persona)}>Relationships →</a></p>
  {/if}
</div>

<style>
  .sb-activity { border: 1px solid var(--color-edge); border-radius: 12px; padding: 10px; display: grid; gap: 8px; flex-shrink: 0; }
  .sb-activity h3 { padding: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; } .sb-activity h3 span { text-transform: none; letter-spacing: 0; color: var(--color-muted); }
  .sb-activity p { font-size: 10px; line-height: 1.5; color: var(--color-muted); }
  .sb-explore { display: flex; align-items: center; gap: 7px; font-size: 11px; color: var(--color-ink); } .sb-explore input, .sb-channel-check { width: 14px; height: 14px; flex-shrink: 0; accent-color: var(--color-accent); }
  .sb-channel-row { display: flex; align-items: center; gap: 3px; } .sb-channel-row .sb-channel { min-width: 0; flex: 1; }
  .sb-browser { overflow-y: auto; } .sb-browser .sb-channels { min-height: 160px; flex: 1 0 160px; }
  .sb-pending { border: 1px solid var(--color-edge); border-radius: 12px; padding: 9px; max-height: 230px; overflow: auto; flex-shrink: 0; }
  .sb-pending h3 { padding: 0 0 8px; display: flex; justify-content: space-between; } .sb-pending h4 { font-size: 11px; color: var(--color-muted); padding: 5px 4px; }
  .sb-pending-row { display: block; width: 100%; text-align: left; padding: 9px 5px; border-radius: 8px; } .sb-pending-row:hover, .sb-pending-row.chosen { background: color-mix(in srgb, var(--color-accent) 9%, transparent); }
  .sb-pending-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; } .sb-pending-top b { font-size: 12px; } .sb-status { font-size: 10px; color: var(--color-accent); } .sb-pending-row small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 3px; }

  .sb-browser { height: 100%; display: flex; flex-direction: column; min-height: 0; gap: 12px; padding: 12px 6px; } .sb-actions { display: flex; align-items: center; gap: 12px; } h2 { font-size: 14px; font-weight: 600; flex: 1; } .sb-actions button { color: var(--color-faint); } .sb-add { display: flex; align-items: center; gap: 5px; font-size: 11px; white-space: nowrap; }
  .sb-label, .sb-join label { display: block; font-size: 11px; color: var(--color-muted); } select, input { min-width: 0; width: 100%; color: var(--color-ink); background: transparent; font-size: 12px; outline: none; } select, .sb-join input { display: block; padding: 9px; border: 1px solid var(--color-edge); border-radius: 9px; margin-top: 6px; } option { background: var(--color-bg); } .sb-search { display: flex; align-items: center; gap: 8px; padding: 9px; border: 1px solid var(--color-edge); border-radius: 9px; color: var(--color-faint); } .sb-channels { min-height: 0; overflow: auto; flex: 1; } h3 { font-size: 10px; text-transform: uppercase; color: var(--color-faint); letter-spacing: 0.08em; padding: 14px 8px 7px; }
  .sb-channel { display: flex; width: 100%; text-align: left; align-items: center; gap: 9px; padding: 9px; border-radius: 9px; font-size: 12px; color: var(--color-muted); } .sb-channel.chosen, .sb-channel:hover { color: var(--color-accent); background: color-mix(in srgb, var(--color-accent) 10%, transparent); } .sb-channel-name { flex: 1; min-width: 0; overflow-wrap: anywhere; } small { display: block; color: var(--color-faint); font-size: 10px; } .sb-hash { color: var(--color-faint); font-size: 17px; } .sb-empty, .sb-foot, .sb-error { font-size: 11px; line-height: 1.6; padding: 8px; color: var(--color-muted); } .sb-error { color: var(--color-warn); border: 1px solid var(--color-edge); border-radius: 9px; } .sb-foot { font-size: 10px; border-top: 1px solid var(--color-edge); } a { color: var(--color-accent); }
  .sb-join { display: grid; gap: 10px; padding: 12px; border: 1px solid var(--color-edge); border-radius: 12px; } .sb-preview { display: grid; gap: 7px; font-size: 12px; } .sb-preview p { color: var(--color-muted); font-size: 11px; line-height: 1.5; } button:disabled { opacity: 0.5; } input:focus-visible, select:focus-visible { outline: 1px solid var(--color-accent); outline-offset: 2px; }
</style>

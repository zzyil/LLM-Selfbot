<script lang="ts">
  import { get } from "svelte/store";
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { resource } from "../lib/resource";
  import { viewPersona } from "../lib/stores";
  import { memorySection } from "../lib/server-memory";
  import { visiblePoll } from "../lib/poll";
  import PersonaScope from "../lib/personas/PersonaScope.svelte";
  import Icon from "../lib/components/Icon.svelte";
  import Button from "../lib/components/Button.svelte";
  import ErrorNote from "../lib/components/ErrorNote.svelte";

  type Context = { kind: string; guild_id: string; guild_name: string; channel_name: string; messages: number };
  type Interaction = { message_id: string; stance: string; quote: string; at: number; guild_id: string; channel_id: string; guild_name: string; channel_name: string; kind: string };
  type Person = { user_id: string; username: string; display_name?: string; facts: Record<string, string>; contexts?: Context[];
    relationship: { relationship: string; affinity: number; interactions: number; updated_at: number }; interactions: Interaction[] };
  let pid = $state(get(viewPersona) || "default");
  let query = $state("");
  let guild = $state("");
  let status = $state("");
  function relationshipsFor(id: string) {
    return resource(`relationships:${id}`, () => api.relationships(id),
      { people: [] as Person[], servers: [] as { id: string; name: string }[], learning_enabled: false });
  }
  const data = $derived(relationshipsFor(pid));
  const people = $derived<Person[]>($data.data.people ?? []);
  const shown = $derived(people.filter((p) =>
    (!guild || p.contexts?.some((c) => c.guild_id === guild)) &&
    (!status || p.relationship.relationship === status) &&
    (!query.trim() || `${p.display_name || ""} ${p.username} ${p.user_id}`.toLowerCase().includes(query.trim().toLowerCase()))));
  const familiar = $derived(people.filter((p) => ["friend", "close_friend"].includes(p.relationship.relationship)).length);
  const strained = $derived(people.filter((p) => ["wary", "foe"].includes(p.relationship.relationship)).length);
  $effect(() => { data.refresh({ maxAge: 10000 }); });
  onMount(() => visiblePoll(() => data.refresh({ force: true, quiet: true }), 15000));
  function switchTo(next: string) { pid = next; viewPersona.set(next); guild = ""; status = ""; }
  function label(value: string) { return value.replaceAll("_", " "); }
  function source(event: Interaction) {
    return event.kind === "server" ? `${event.guild_name || "Server"} · #${event.channel_name || event.channel_id}`
      : event.kind === "dm" ? "Direct message" : "Earlier interaction";
  }
  function age(at: number) {
    const minutes = Math.max(0, Math.floor((Date.now()/1000-at)/60));
    return minutes < 1 ? "just now" : minutes < 60 ? `${minutes}m ago` : minutes < 1440 ? `${Math.floor(minutes/60)}h ago` : `${Math.floor(minutes/1440)}d ago`;
  }
</script>

<section class="glass rel-hero">
  <Icon name="accounts" size={28} />
  <div><PersonaScope value={pid} onchange={switchTo} /><h2>Relationships</h2><p>One character’s attitude toward people met in servers and DMs.</p></div>
  <Button kind="ghost" size="sm" loading={$data.refreshing} onclick={() => data.refresh({ force: true })}><Icon name="refresh" size={13} /> Refresh</Button>
  <div class="rel-counts"><span>{people.length} people</span><span>{familiar} friends</span><span>{strained} strained</span><span>Learning {$data.data.learning_enabled ? "on" : "off"}</span></div>
</section>
<ErrorNote text={$data.error} onretry={() => data.refresh({ force: true })} />
<section class="glass rel-filters">
  <label><Icon name="search" size={14} /><input aria-label="Search relationships" bind:value={query} placeholder="Search people or user IDs" /></label>
  <select aria-label="Filter relationships by server" bind:value={guild}><option value="">All servers and DMs</option>{#each $data.data.servers as server}<option value={server.id}>{server.name || server.id}</option>{/each}</select>
  <select aria-label="Filter relationship" bind:value={status}><option value="">Every relationship</option>{#each ["new", "acquaintance", "friend", "close_friend", "wary", "foe"] as value}<option value={value}>{label(value)}</option>{/each}</select>
  <a href="#/memory" onclick={() => memorySection.set("people")}>User memories →</a>
</section>
{#if !$data.fetched && $data.loading}<p class="rel-empty glass">Loading relationships…</p>
{:else if !shown.length}<section class="rel-empty glass"><h3>{people.length ? "No matching people" : "No interactions recorded yet"}</h3><p>People appear after accepted server or DM interactions. Attitudes grow from source-backed exchanges; ordinary banter does not become biography.</p></section>
{:else}
  <div class="rel-grid">{#each shown as person (person.user_id)}
    <article class="glass rel-person">
      <header><div><h3>{person.display_name || person.username || person.user_id}</h3><small>{person.user_id}</small></div><span class="rel-badge" class:strained={["wary", "foe"].includes(person.relationship.relationship)}>{label(person.relationship.relationship)}</span></header>
      <div class="rel-meter" role="meter" aria-label="Affinity" aria-valuemin={-1} aria-valuemax={1} aria-valuenow={person.relationship.affinity}><i style={`width:${Math.max(0, Math.min(100, (person.relationship.affinity+1)*50))}%`}></i></div>
      <p class="rel-meta">{person.relationship.interactions} assessed interactions · affinity {person.relationship.affinity.toFixed(2)}{#if person.relationship.interactions < 5} · provisional{/if}</p>
      <div class="rel-contexts">{#each person.contexts || [] as context}<span>{context.kind === "dm" ? "DMs" : `${context.guild_name || context.guild_id} · #${context.channel_name || "channel"}`} · {context.messages} messages</span>{/each}</div>
      <h4>Recent social memories</h4>
      {#if !person.interactions.length}<p class="rel-meta">No clear friendly or hostile exchange recorded. Neutral conversation still counts toward familiarity.</p>{/if}
      {#each person.interactions as event (event.message_id)}<div class="rel-event"><span class="rel-meta">{event.stance} · {source(event)} · {age(event.at)}</span><blockquote>{event.quote}</blockquote>{#if event.channel_id}<a href={`https://discord.com/channels/${event.guild_id || "@me"}/${event.channel_id}/${event.message_id}`} target="_blank" rel="noreferrer">View source ↗</a>{/if}</div>{/each}
      {#if Object.keys(person.facts).length}<details><summary>{Object.keys(person.facts).length} remembered user facts</summary>{#each Object.entries(person.facts) as [key,value]}<p class="rel-fact"><b>{label(key)}</b>: {value}</p>{/each}</details>{/if}
    </article>
  {/each}</div>
{/if}
<p class="rel-note">Affinities follow the same user across communities. Private conversations stay private in the character’s public replies. These labels describe the character’s inferred attitude, not mutual friendship.</p>

<style>
  .rel-hero,.rel-filters,.rel-person,.rel-empty { border-radius:20px;padding:22px; }
  .rel-hero { display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:16px; }
  .rel-hero>div:nth-child(2) { flex:1; }.rel-hero h2 { font-size:24px;font-weight:650;margin-top:8px; }
  .rel-hero p,.rel-meta,.rel-note,small { color:var(--color-faint);font-size:12px; }.rel-counts { display:flex;gap:18px;width:100%;font-size:13px; }
  .rel-filters { display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:16px; }.rel-filters label { display:flex;align-items:center;gap:10px;flex:1;min-width:190px; }
  input,select { background:var(--field,rgba(128,128,128,.08));border:1px solid var(--color-edge);border-radius:10px;padding:10px;color:inherit; } input { width:100%; }
  .rel-grid { display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,350px),1fr));gap:16px; }.rel-person header { display:flex;align-items:flex-start;justify-content:space-between;gap:12px; }.rel-person h3 { font-size:17px;font-weight:650; }
  .rel-badge { border:1px solid var(--color-edge);border-radius:30px;padding:5px 9px;font-size:11px;white-space:nowrap; }.rel-badge.strained { color:var(--color-warn,#f4bb75); }
  .rel-meter { height:5px;background:rgba(128,128,128,.16);border-radius:4px;margin:18px 0 8px;overflow:hidden; }.rel-meter i { display:block;height:100%;background:var(--color-accent,#98a8ff); }
  .rel-contexts { display:flex;flex-wrap:wrap;gap:6px;margin:14px 0; }.rel-contexts span { font-size:11px;background:rgba(128,128,128,.08);padding:5px 8px;border-radius:8px; }
  h4 { font-size:12px;font-weight:650;margin-top:18px; }.rel-event { margin-top:12px;padding-top:10px;border-top:1px solid var(--color-edge); } blockquote { font-size:13px;margin:7px 0;overflow-wrap:anywhere; }
  a { font-size:12px;color:var(--color-accent,#98a8ff); }.rel-event a { font-size:11px; }.rel-note { margin:18px 4px;line-height:1.6; } details { margin-top:15px;font-size:12px; }.rel-fact { margin-top:8px; }.rel-empty p { margin-top:8px;color:var(--color-faint);font-size:13px; }
</style>

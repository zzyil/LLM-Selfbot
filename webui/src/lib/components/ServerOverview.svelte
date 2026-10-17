<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../api";
  import { viewPersona } from "../stores";
  import { visiblePoll } from "../poll";
  import { serverMemories, memorySection } from "../server-memory";
  import { serversFocus, serverChannelCache } from "../chats";
  import PersonaScope from "../personas/PersonaScope.svelte";
  import Icon from "./Icon.svelte";
  const memories = $derived(serverMemories($viewPersona || "default"));
  const servers = $derived($memories.data.servers);
  const accountIds = $derived(new Set(servers.flatMap((s) => s.accounts.map((a) => a.id))));
  const pending = $derived(Object.entries($serverChannelCache).filter(([id]) => accountIds.has(id)).reduce((n, [, entry]) => n + entry.channels.length, 0));
  const terms = $derived(servers.reduce((n, s) => n + s.terms, 0));
  const disputes = $derived(servers.reduce((n, s) => n + s.disputed, 0));
  const joined = $derived(servers.filter((s) => s.accounts.length).length);
  const channels = $derived(new Set(servers.flatMap((s) => s.channels.map((c) => c.channel_id))).size);
  $effect(() => { memories.refresh({ maxAge: 10000 }); });
  onMount(() => visiblePoll(() => memories.refresh({ force: true, quiet: true }), 15000));
  async function browse() {
    try {
      const accounts = (await api.accounts()).accounts || [];
      serversFocus.set(accounts.find((a: any) => a.platform === "discord" && a.persona === ($viewPersona || "default"))?.id || "");
    } catch { serversFocus.set(""); }
    window.location.hash = "/servers";
  }
  function open() {
    memorySection.set("servers");
    window.location.hash = "/memory";
  }
</script>

<section class="glass server-overview" aria-label="Server memory overview">
  <div class="server-heading">
    <span class="server-icon"><Icon name="globe" size={21} /></span>
    <div><h2>Server memories</h2><p>{#if $memories.error}{$memories.error}{:else if !$memories.fetched}Checking server memory…{:else}One character, with a vocabulary for each community.{/if}</p></div>
  </div>
  <div class="server-numbers">
    <span><b>{joined}</b> connected servers</span>
    <span><b>{terms}</b> learned terms</span>
    <span><b>{channels}</b> observed channels</span>
    <span><b>{pending}</b> channel replies</span>
    {#if disputes}<span class="text-warn"><b>{disputes}</b> disputed</span>{/if}
  </div>
  <div class="server-controls"><PersonaScope value={$viewPersona || "default"} onchange={(pid) => viewPersona.set(pid)} /><button class="jelly server-open" onclick={browse}>Open servers <Icon name="globe" size={12} /></button><button class="jelly server-open" onclick={open}>Manage memories <Icon name="chevron" size={12} /></button></div>
  {#if $memories.data.unavailable_accounts}<p class="server-note">Live activity unavailable for {$memories.data.unavailable_accounts} account(s). Saved terms are still shown.</p>{/if}
</section>

<style>
  .server-overview { border-radius: 20px; padding: 20px 22px; margin-bottom: 20px; display: flex; align-items: center; gap: 18px 28px; flex-wrap: wrap; }
  .server-heading { display: flex; align-items: center; gap: 12px; flex: 1 1 250px; }
  .server-icon { display: grid; place-items: center; width: 44px; height: 44px; border-radius: 14px; background: color-mix(in srgb, var(--color-accent) 12%, transparent); color: var(--color-accent); }
  h2 { font-size: 14px; font-weight: 650; } p, .server-numbers { color: var(--color-faint); font-size: 11px; } p { margin-top: 4px; }
  .server-numbers { display: flex; gap: 16px; flex-wrap: wrap; } .server-numbers span { display: flex; flex-direction: column; gap: 3px; } b { font-size: 21px; color: var(--color-ink); font-weight: 600; }
  .server-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; } .server-open { display: flex; align-items: center; gap: 7px; color: var(--color-accent); font-size: 12px; } .server-note { flex-basis: 100%; }
</style>

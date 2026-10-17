import { writable } from "svelte/store";
import { api } from "./api";
import { resource } from "./resource";

export const memorySection = writable<"people" | "servers">("people");
export type ServerChannel = {
  channel_id: string; name: string; account: string; messages: number; members: number;
  history_messages: number; last_seen: number | null; state: string;
};
export type ServerMemory = {
  guild_id: string; name: string; terms: number; private_terms: number; disputed: number; updated_at: number;
  accounts: { id: string; paused: boolean; allow_server: boolean }[]; channels: ServerChannel[];
};
export type ServerMemories = {
  servers: ServerMemory[]; live_accounts: number; unavailable_accounts: number;
  settings: { learning: boolean; share_with_mutuals: boolean; participation: boolean;
              ambient_probability: number; activity_window_seconds: number };
};
export type LingoEntry = {
  term: string; scope_channel: string;
  senses: { meaning: string; status: string; manual: boolean; sources: number; updated_at: number }[];
};
export function serverMemories(persona: string) {
  return resource<ServerMemories>(`serverMemory:${persona}`, () => api.serverMemories(persona), {
    servers: [], live_accounts: 0, unavailable_accounts: 0,
    settings: { learning: false, share_with_mutuals: true, participation: false,
                ambient_probability: 0, activity_window_seconds: 600 },
  });
}
export function age(at: number | null) {
  if (!at) return "No activity yet";
  const minutes = Math.max(0, Math.floor((Date.now() / 1000 - at) / 60));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return new Date(at * 1000).toLocaleDateString();
}

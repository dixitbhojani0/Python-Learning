/** The ONLY place backend URLs live. Control plane is JSON over /api/v1. */
import type { KnowledgeInfo, Scene, SessionInfo } from "../types";

/** Same-origin by default; set VITE_API_BASE when the backend lives on another host. */
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api/v1";

export async function fetchScript(): Promise<Scene[]> {
  const resp = await fetch(`${BASE}/script`);
  if (!resp.ok) throw new Error(`script fetch failed: ${resp.status}`);
  return (await resp.json()).scenes;
}

/** All scenes including disabled ones — admin panel only. */
export async function fetchAllScenes(): Promise<Scene[]> {
  const resp = await fetch(`${BASE}/script?all=1`);
  if (!resp.ok) throw new Error(`script fetch failed: ${resp.status}`);
  return (await resp.json()).scenes;
}

export async function saveScript(scenes: Scene[]): Promise<void> {
  const resp = await fetch(`${BASE}/script`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenes }),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
}

export async function createSession(): Promise<SessionInfo> {
  const resp = await fetch(`${BASE}/session`, { method: "POST" });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || resp.statusText);
  return data;
}

export async function fetchKnowledge(): Promise<KnowledgeInfo> {
  const resp = await fetch(`${BASE}/knowledge`);
  if (!resp.ok) throw new Error(`knowledge fetch failed: ${resp.status}`);
  return resp.json();
}

export async function uploadDocument(name: string, sizeKb: number): Promise<void> {
  const resp = await fetch(`${BASE}/knowledge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, sizeKb }),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
}

export async function reindexDocument(id: number): Promise<void> {
  const resp = await fetch(`${BASE}/knowledge/${id}/reindex`, { method: "POST" });
  if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
}

export async function deleteDocument(id: number): Promise<void> {
  const resp = await fetch(`${BASE}/knowledge/${id}`, { method: "DELETE" });
  if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
}

export function logToServer(...parts: unknown[]): void {
  const msg = parts
    .map((p) => (p instanceof Error ? p.stack || p.message : typeof p === "object" ? JSON.stringify(p) : String(p)))
    .join(" ");
  fetch(`${BASE}/log`, { method: "POST", body: `${new Date().toISOString()} ${msg}` }).catch(() => {});
}

/** The ONLY place backend URLs live. Control plane is JSON over /api/v1. */
import type { Scene, SessionInfo } from "../types";

const BASE = "/api/v1";

export async function fetchScript(): Promise<Scene[]> {
  const resp = await fetch(`${BASE}/script`);
  if (!resp.ok) throw new Error(`script fetch failed: ${resp.status}`);
  return (await resp.json()).scenes;
}

export async function createSession(): Promise<SessionInfo> {
  const resp = await fetch(`${BASE}/session`, { method: "POST" });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || resp.statusText);
  return data;
}

export function logToServer(...parts: unknown[]): void {
  const msg = parts
    .map((p) => (p instanceof Error ? p.stack || p.message : typeof p === "object" ? JSON.stringify(p) : String(p)))
    .join(" ");
  fetch(`${BASE}/log`, { method: "POST", body: `${new Date().toISOString()} ${msg}` }).catch(() => {});
}

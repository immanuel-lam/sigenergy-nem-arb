const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

/** GET a JSON resource from the FastAPI backend. */
export async function api<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json() as Promise<T>;
}

/** POST a JSON body to the FastAPI backend. timeoutMs defaults to 180s. */
export async function apiPost<T>(
  path: string,
  body?: unknown,
  timeoutMs = 180_000,
): Promise<T> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return (await r.json()) as T;
  } catch (e) {
    if ((e as { name?: string }).name === "AbortError") {
      throw new Error(`${path} timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw e;
  } finally {
    clearTimeout(t);
  }
}

export const API_BASE = BASE;

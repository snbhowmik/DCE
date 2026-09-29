// Thin client for the FastAPI backend (/api/v1, proxied by next.config.ts rewrites).
import type { Brief, CompareResult, DatasetRow, DecisionRow, Payload } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`/api/v1${path}`, { cache: "no-store" });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {}
    throw new ApiError(r.status, String(detail));
  }
  return r.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`/api/v1${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new ApiError(r.status, detail);
  }
  return r.json() as Promise<T>;
}

const payloads = new Map<string, Promise<Payload>>();

export const api = {
  datasets: () => get<DatasetRow[]>("/datasets"),
  payload: (runId: string) => {
    if (!payloads.has(runId)) {
      const p = get<Payload>(`/runs/${runId}`);
      p.catch(() => payloads.delete(runId));
      payloads.set(runId, p);
    }
    return payloads.get(runId)!;
  },
  narrative: (runId: string) => get<Brief>(`/runs/${runId}/narrative`),
  compare: (world: string) => get<CompareResult>(`/compare?world=${encodeURIComponent(world)}`),
  decisions: (runId?: string) =>
    get<DecisionRow[]>(`/decisions${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`),
  decide: (body: {
    run_id: string;
    kind: string;
    key: string;
    summary: string;
    action: "accept" | "reject";
    reason: string;
    user?: string;
    details?: Record<string, unknown>;
  }) => post<DecisionRow>("/decisions", body),
  startRun: (world_id: string, modes: string[]) =>
    post<{ job_id: string; status: string }>("/runs", { world_id, modes }),
  job: (jobId: string) =>
    get<{ job_id: string; status: string; runs: { run_id: string; mode: string; status?: string }[]; error?: string }>(
      `/jobs/${jobId}`,
    ),
};

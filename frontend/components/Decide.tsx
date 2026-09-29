"use client";
// Accept / reject a recommendation with a reason (PRD US6, FR-30). Writes to /api/v1/decisions.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DecisionRow } from "@/lib/types";
import { StatusPill } from "./ui";

const listeners = new Set<() => void>();
const cache = new Map<string, DecisionRow[]>();

async function load(runId: string) {
  cache.set(runId, await api.decisions(runId));
  listeners.forEach((l) => l());
}

export function useDecisions(runId: string) {
  const [, tick] = useState(0);
  useEffect(() => {
    const l = () => tick((t) => t + 1);
    listeners.add(l);
    if (!cache.has(runId)) void load(runId);
    return () => {
      listeners.delete(l);
    };
  }, [runId]);
  return { rows: cache.get(runId) ?? [], reload: () => load(runId) };
}

export function Decide({
  runId,
  kind,
  itemKey,
  summary,
  details,
}: {
  runId: string;
  kind: string;
  itemKey: string;
  summary: string;
  details?: Record<string, unknown>;
}) {
  const { rows, reload } = useDecisions(runId);
  const latest = rows.find((r) => r.rec_id === `${runId}:${kind}:${itemKey}`);
  const [open, setOpen] = useState<null | "accept" | "reject">(null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!open) return;
    setBusy(true);
    setErr(null);
    try {
      await api.decide({ run_id: runId, kind, key: itemKey, summary, action: open, reason, details });
      await reload();
      setOpen(null);
      setReason("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (open)
    return (
      <div className="mt-2 grid gap-2 rounded-md border border-hair bg-surface-2 p-3">
        <label className="text-[12px] text-ink-2" htmlFor={`r-${itemKey}`}>
          Reason to {open} <span className="text-ink-3">(logged with the run)</span>
        </label>
        <textarea
          id={`r-${itemKey}`}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          autoFocus
          className="w-full rounded border border-hair bg-surface px-2 py-1.5 text-[13px]"
          placeholder={open === "accept" ? "e.g. partner confirmed capacity for February" : "e.g. cost too high for a surplus month"}
        />
        {err && <div className="text-[12px] text-critical">{err}</div>}
        <div className="flex gap-2">
          <button
            onClick={submit}
            disabled={busy || reason.trim().length < 3}
            className="rounded bg-ink px-3 py-1 text-[12.5px] font-medium text-[color:var(--surface)] disabled:opacity-50"
          >
            {busy ? "Saving…" : `Log ${open}`}
          </button>
          <button onClick={() => setOpen(null)} className="rounded px-2 py-1 text-[12.5px] text-ink-2 hover:bg-surface">
            Cancel
          </button>
        </div>
      </div>
    );

  return (
    <div className="flex flex-wrap items-center gap-2">
      {latest && (
        <StatusPill tone={latest.action === "accept" ? "good" : "neutral"}>
          {latest.action === "accept" ? "Accepted" : "Rejected"}: {latest.reason}
        </StatusPill>
      )}
      <button onClick={() => setOpen("accept")} className="rounded border border-hair px-2 py-0.5 text-[12px] hover:bg-surface-2">
        Accept
      </button>
      <button onClick={() => setOpen("reject")} className="rounded border border-hair px-2 py-0.5 text-[12px] hover:bg-surface-2">
        Reject
      </button>
    </div>
  );
}

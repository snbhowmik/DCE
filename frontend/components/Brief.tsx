"use client";
// The narrative brief: written by the configured LLM (Groq) or the deterministic template, and
// always checked number-by-number against the run payload before it is shown (P4, FR-27).
import { useState } from "react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import { Badge, Card, Skeleton, StatusPill } from "./ui";

export function Brief({ runId }: { runId: string }) {
  const { data, error, loading } = useAsync(runId, () => api.narrative(runId));
  const [open, setOpen] = useState(false);
  if (loading) return <Skeleton h={170} />;
  if (error || !data)
    return (
      <Card title="Planning brief">
        <p className="text-[13px] text-ink-2">Brief unavailable: {error}</p>
      </Card>
    );
  const llm = data.source === "llm";
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          Planning brief
          {llm ? <Badge>written by {data.model}</Badge> : <Badge>template brief</Badge>}
          <StatusPill tone="good">every number checked against the run</StatusPill>
        </span>
      }
      right={
        <button onClick={() => setOpen((o) => !o)} className="text-[12.5px] text-ink-2 underline-offset-2 hover:underline">
          {open ? "Hide" : "Show"} grounding facts
        </button>
      }
    >
      <p className="text-[18px] font-semibold leading-snug tracking-[-0.01em]">{data.headline}</p>
      <div className="mt-3 grid gap-5 md:grid-cols-[1.6fr_1fr]">
        <ul className="space-y-1.5 text-[13.5px] text-ink-2">
          {data.findings.map((f, i) => (
            <li key={i} className="flex gap-2">
              <span className="mt-[9px] inline-block h-[3px] w-2.5 shrink-0 bg-ink-3" />
              {f}
            </li>
          ))}
        </ul>
        <div>
          <div className="mb-1.5 text-[12px] font-medium uppercase tracking-[0.06em] text-ink-3">Actions</div>
          <ul className="space-y-1.5 text-[13.5px]">
            {data.actions.map((a, i) => (
              <li key={i} className="flex gap-2">
                <span className="mt-[9px] inline-block h-[3px] w-2.5 shrink-0 bg-brand" />
                {a}
              </li>
            ))}
          </ul>
        </div>
      </div>
      {!llm && data.fallback_reason && (
        <p className="mt-3 text-[12px] text-ink-3">
          Why a template: {data.fallback_reason}. The template uses only payload numbers, so the brief is always
          available offline.
        </p>
      )}
      {open && (
        <div className="mt-4 rounded-md bg-surface-2 p-3">
          <div className="mb-1 text-[12px] font-medium text-ink-2">
            Facts given to the model (the only numbers it may use):
          </div>
          <ul className="list-disc space-y-0.5 pl-5 font-mono text-[11.5px] text-ink-2">
            {data.facts.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
          {data.log && data.log.length > 0 && (
            <details className="mt-2 text-[12px] text-ink-2">
              <summary className="cursor-pointer">Model exchange ({data.log.length} call{data.log.length > 1 ? "s" : ""})</summary>
              {data.log.map((l, i) => (
                <pre key={i} className="mt-2 whitespace-pre-wrap rounded bg-surface p-2 font-mono text-[11px]">
                  {l.response}
                </pre>
              ))}
            </details>
          )}
        </div>
      )}
    </Card>
  );
}

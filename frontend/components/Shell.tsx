"use client";
// App frame: sidebar navigation + context bar (world, strategy mode, run provenance, re-run).
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { day, modeLabel, shortHash } from "@/lib/format";
import { useSelection } from "@/lib/selection";
import { MODES } from "@/lib/types";
import { ChatDock } from "./ChatDock";
import { StatusIcon } from "./ui";

const NAV = [
  { href: "/", label: "Overview", n: "01" },
  { href: "/plan", label: "Allocation plan", n: "02" },
  { href: "/markets", label: "Markets", n: "03" },
  { href: "/accounts", label: "B2B accounts", n: "04" },
  { href: "/alerts", label: "Alerts & mitigations", n: "05" },
  { href: "/scenarios", label: "Scenarios", n: "06" },
  { href: "/decisions", label: "Decision log", n: "07" },
  { href: "/health", label: "Data & model health", n: "08" },
] as const;

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { payload, scenarioRunId, setScenario } = useSelection();
  const alerts = payload?.risk.alerts ?? [];
  const hasBreach = alerts.some((a) => a.kind === "breach");
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-hair bg-surface px-4 py-5 lg:flex">
        <Link href="/" className="mb-8 flex items-center gap-2.5 px-2">
          <span className="inline-block h-6 w-2 bg-brand" aria-hidden />
          <span className="font-display text-[17px] font-black leading-none tracking-[-0.03em]">
            Biokraft DCE
          </span>
        </Link>
        <nav className="flex flex-col gap-0.5" aria-label="Screens">
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`group relative flex items-center gap-3 rounded-md px-2 py-1.5 text-[13.5px] transition-colors ${
                  active ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink"
                }`}
              >
                {active && <span className="absolute -left-4 top-1.5 h-5 w-[3px] bg-brand" aria-hidden />}
                <span className="font-mono text-[11px] text-ink-3">{n.n}</span>
                <span className="flex-1">{n.label}</span>
                {n.href === "/alerts" && alerts.length > 0 && (
                  <StatusIcon tone={hasBreach ? "critical" : "warning"} />
                )}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto px-2 text-[11.5px] leading-snug text-ink-3">
          Data: synthetic drops from an independent generator, used to test the pipeline. Figures are not
          Biokraft actuals.
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <ContextBar />
        {scenarioRunId && payload?.run.kind === "scenario" && (
          <div className="border-b border-hair bg-[color:var(--warning-wash)]">
            <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-5 py-2 text-[13px] lg:px-8">
              <strong>Viewing a what-if scenario</strong>
              <span className="text-ink-2">{(payload.run.changes ?? []).join(" · ") || "strategy only"}</span>
              <button onClick={() => setScenario(null)} className="ml-auto rounded border border-hair bg-surface px-2 py-0.5 text-[12.5px] hover:bg-surface-2">
                Back to base plan
              </button>
            </div>
          </div>
        )}
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-5 py-6 lg:px-8">{children}</main>
        <ChatDock />
      </div>
    </div>
  );
}

function ContextBar() {
  const { datasets, world, setWorld, mode, setMode, payload, runId, refresh } = useSelection();
  const [job, setJob] = useState<{ id: string; status: string; error?: string } | null>(null);

  useEffect(() => {
    if (!job || job.status === "succeeded" || job.status === "failed") return;
    const t = setInterval(async () => {
      try {
        const j = await api.job(job.id);
        setJob({ id: j.job_id, status: j.status, error: j.error });
        if (j.status === "succeeded") await refresh();
      } catch {
        setJob((cur) => (cur ? { ...cur, status: "failed" } : cur));
      }
    }, 1500);
    return () => clearInterval(t);
  }, [job, refresh]);

  const rerun = async () => {
    if (!world) return;
    try {
      const j = await api.startRun(world, [...MODES]);
      setJob({ id: j.job_id, status: j.status });
    } catch (e) {
      setJob({ id: "-", status: "failed", error: e instanceof Error ? e.message : String(e) });
    }
  };
  const running = job && (job.status === "queued" || job.status === "running");

  return (
    <div className="sticky top-0 z-20 border-b border-hair bg-[color:var(--plane)]/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-5 gap-y-2 px-5 py-3 lg:px-8">
        <label className="flex items-center gap-2 text-[12.5px] text-ink-2">
          World
          <select
            value={world ?? ""}
            onChange={(e) => setWorld(e.target.value)}
            className="max-w-[280px] rounded-md border border-hair bg-surface px-2 py-1 font-mono text-[12.5px] text-ink"
          >
            {(datasets ?? []).map((d) => (
              <option key={d.world_id} value={d.world_id}>
                {d.world_id}
                {d.name ? ` · ${d.name}` : ""}
                {d.runs.length ? "" : " (no runs)"}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-center gap-2 text-[12.5px] text-ink-2">
          Strategy
          <div role="radiogroup" aria-label="Strategy mode" className="flex rounded-md border border-hair bg-surface p-0.5">
            {MODES.map((m) => (
              <button
                key={m}
                role="radio"
                aria-checked={mode === m}
                onClick={() => setMode(m)}
                className={`rounded px-2.5 py-1 text-[12.5px] transition-colors ${
                  mode === m ? "bg-ink font-medium text-[color:var(--surface)]" : "text-ink-2 hover:text-ink"
                }`}
              >
                {modeLabel(m)}
              </button>
            ))}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-3 text-[12px] text-ink-3">
          {payload && (
            <span className="hidden items-center gap-2 md:flex" title="Run provenance: every figure on screen comes from this run">
              <span className="font-mono text-ink-2">{runId}</span>
              <span>· data {shortHash(payload.run.dataset_hash)}</span>
              <span>· git {shortHash(payload.run.git_sha, 7)}</span>
              <span>· {day(payload.run.created_at)}</span>
            </span>
          )}
          <button
            onClick={rerun}
            disabled={!world || !!running}
            className="rounded-md border border-hair bg-surface px-2.5 py-1 text-[12.5px] text-ink hover:bg-surface-2 disabled:opacity-60"
            title="Run forecast → plan → stress test for all three modes on this world"
          >
            {running ? `Running… (${job?.status})` : "Re-run world"}
          </button>
          {job?.status === "failed" && <span className="text-critical">Run failed{job.error ? `: ${job.error}` : ""}</span>}
        </div>
      </div>
    </div>
  );
}

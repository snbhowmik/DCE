"use client";
// Ranked mitigations per alert (T6.3): each feasible playbook lever is re-solved and stress-tested
// against this run; ranked by risk removed per (cash cost + harm).
import { useState } from "react";
import { api } from "@/lib/api";
import { day, inr, kg, pct, shortDay } from "@/lib/format";
import { useSelection } from "@/lib/selection";
import { useAsync } from "@/lib/useAsync";
import type { Mitigation, Payload } from "@/lib/types";
import { Badge, Card, Skeleton, StatusIcon } from "./ui";

const STATUS: Record<string, string> = {
  too_late: "too late",
  not_applicable: "not applicable",
  in_plan: "already optimized in the plan",
  no_benefit: "no benefit in this situation",
};

export function Mitigations({ p }: { p: Payload }) {
  const { world, runId, setScenario } = useSelection();
  const { data, error, loading } = useAsync(runId ? `mit:${runId}` : null, () => api.mitigations(runId!));
  const [busy, setBusy] = useState<string | null>(null);
  if (!p.risk.alerts.length) return null;
  if (loading) return <Skeleton h={220} />;
  if (error || !data) return <Card title="Ranked mitigations">Could not evaluate mitigations: {error}</Card>;

  const tryIt = async (m: Mitigation) => {
    if (!world || !m.levers) return;
    setBusy(m.id + m.alert);
    try {
      const r = await api.scenario(world, m.levers);
      setScenario(r.run_id);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      title="Ranked mitigations"
      subtitle={`Every feasible option re-solved and stress-tested on this run (${data.seconds} s). Ranked by risk removed per rupee of cost plus harm; only options that can take effect before the alert are offered.`}
    >
      <div className="grid gap-6">
        {p.risk.alerts.map((a, ai) => {
          const rows = data.mitigations.filter((m) => m.alert === ai);
          const ranked = rows.filter((m) => m.status === "ranked").sort((x, y) => (x.rank ?? 99) - (y.rank ?? 99));
          const other = rows.filter((m) => m.status !== "ranked");
          const breach = a.kind === "breach";
          return (
            <div key={ai}>
              <div className="mb-2 flex items-center gap-2 text-[13.5px] font-medium">
                <StatusIcon tone={breach ? "critical" : "warning"} />
                {breach ? "Shortfall" : "Surplus"} from {shortDay(a.start)} · in {a.weeks_until} wk · expected {kg(a.expected_kg)}
              </div>
              {ranked.length === 0 ? (
                <p className="text-[13px] text-ink-2">No feasible mitigation reduces this risk; the plan already does what it can in time.</p>
              ) : (
                <table className="dtable tnum text-[12.5px]">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Mitigation</th>
                      <th className="r">{breach ? "Shortfall removed" : "Waste avoided"}</th>
                      <th className="r">Contribution impact</th>
                      <th className="r">B2B / D2C fill</th>
                      <th>Harm · reversible</th>
                      <th>Act by</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {ranked.map((m) => (
                      <tr key={m.id}>
                        <td className="font-mono">{m.rank}</td>
                        <td>
                          <div className="font-medium">
                            <span className="font-mono text-ink-3">{m.id}</span> {m.name}
                          </div>
                          <div className="text-[11.5px] text-ink-3">{m.effect}</div>
                        </td>
                        <td className="r font-medium">{kg(m.risk_removed_kg)}</td>
                        <td className={`r ${(m.cost_inr ?? 0) > 0 ? "text-critical" : "text-good"}`}>
                          {(m.cost_inr ?? 0) > 0 ? "−" : "+"}
                          {inr(Math.abs(m.cost_inr ?? 0))}
                        </td>
                        <td className="r text-ink-2">
                          {pct(m.delta_b2b_fill ?? 0, 2).replace(/^(?!-)/, "+")} / {pct(m.delta_d2c_fill ?? 0, 2).replace(/^(?!-)/, "+")}
                        </td>
                        <td className="text-ink-2">
                          {m.harm.toFixed(1)} · {m.reversible}
                        </td>
                        <td>{day(m.act_by)}</td>
                        <td>
                          <button onClick={() => tryIt(m)} disabled={!!busy} className="rounded border border-hair px-2 py-0.5 text-[12px] hover:bg-surface-2 disabled:opacity-50">
                            {busy === m.id + m.alert ? "…" : "Try it →"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {other.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {other.map((m) => (
                    <Badge key={m.id}>
                      {m.id} {m.name}: {STATUS[m.status]}
                      {m.status === "too_late" && m.lead_time_weeks != null ? ` (needs ${m.lead_time_weeks} wk)` : ""}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

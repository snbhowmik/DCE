"use client";
// Onboarding simulator (T7.1, PRD US4): a candidate B2B account re-solved across start months ×
// ramp profiles and compared with the plan without it.
import { useState } from "react";
import { api } from "@/lib/api";
import { inr, kg, modeLabel, monthLabel, pct, regionName } from "@/lib/format";
import { useSelection } from "@/lib/selection";
import type { OnboardingResult, Payload } from "@/lib/types";
import { Badge, Card, StatusPill } from "./ui";

const RAMP: Record<string, string> = { full: "Full volume", "50_100": "50% → 100%", "33_66_100": "33% → 66% → 100%" };
const CLASS: Record<string, { label: string; tone: "good" | "warning" | "critical" }> = {
  accept_now: { label: "Accept now", tone: "good" },
  accept_from: { label: "Accept, starting later", tone: "good" },
  phase: { label: "Accept with a phased ramp", tone: "warning" },
  decline: { label: "Decline", tone: "critical" },
};

export function Onboarding({ p }: { p: Payload }) {
  const { world, mode, setScenario } = useSelection();
  const [c, setC] = useState({ volume: 1500, price: 650, penalty: 80, region: p.run.regions[0] ?? "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<OnboardingResult | null>(null);
  const month = (i: number) => (p.run.months[i] ? monthLabel(p.run.months[i].start) : `month ${i + 1}`);

  const run = async () => {
    if (!world) return;
    setBusy(true);
    setErr(null);
    try {
      setRes(
        await api.onboard({
          world_id: world,
          mode,
          candidate: { volume_kg_per_month: c.volume, price_inr_per_kg: c.price, penalty_inr_per_kg: c.penalty, region_id: c.region },
        }),
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const num = (k: "volume" | "price" | "penalty", label: string, step: number) => (
    <label className="grid gap-1 text-[12.5px] text-ink-2">
      {label}
      <input
        type="number"
        min={0}
        step={step}
        value={c[k]}
        onChange={(e) => setC({ ...c, [k]: Number(e.target.value) })}
        className="rounded-md border border-hair bg-surface px-2 py-1.5 text-[13px] text-ink tnum"
      />
    </label>
  );
  const rec = res?.recommendation;
  return (
    <Card
      title="Onboarding simulator"
      subtitle={`Should we take on a new wholesale account? The whole horizon is re-solved with it for every start month and ramp, under the ${modeLabel(mode)} strategy.`}
    >
      <div className="grid gap-3 md:grid-cols-5">
        {num("volume", "Volume (kg / month)", 100)}
        {num("price", "Price (₹ / kg)", 10)}
        {num("penalty", "Shortfall penalty (₹ / kg)", 10)}
        <label className="grid gap-1 text-[12.5px] text-ink-2">
          Region
          <select value={c.region} onChange={(e) => setC({ ...c, region: e.target.value })} className="rounded-md border border-hair bg-surface px-2 py-1.5 text-[13px] text-ink">
            {p.run.regions.map((r) => (
              <option key={r} value={r}>
                {regionName(r)}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-end">
          <button onClick={run} disabled={busy || c.volume <= 0 || c.price <= 0} className="w-full rounded-md bg-ink px-3 py-2 text-[13px] font-medium text-[color:var(--surface)] disabled:opacity-50">
            {busy ? "Simulating…" : "Simulate"}
          </button>
        </div>
      </div>
      {err && <p className="mt-3 text-[12.5px] text-critical">{err}</p>}
      {res && rec && (
        <div className="mt-5 grid gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <StatusPill tone={CLASS[rec.class].tone}>{CLASS[rec.class].label}</StatusPill>
            {rec.class !== "decline" && (
              <span className="text-[13.5px] font-medium">
                Start {month(rec.start_month)} · {RAMP[rec.ramp]}
              </span>
            )}
            <span className="text-[12px] text-ink-3">{res.options.length} options solved in {res.seconds} s</span>
            <button onClick={() => setScenario(res.run_id)} className="ml-auto rounded-md border border-hair px-2.5 py-1 text-[12.5px] hover:bg-surface-2">
              Open {rec.class === "decline" ? "best option" : "this plan"} in all screens →
            </button>
          </div>
          <ul className="grid gap-1 text-[13px] text-ink-2">
            {rec.reasons.map((r, i) => (
              <li key={i}>· {r}</li>
            ))}
          </ul>
          <div className="overflow-x-auto">
            <table className="dtable tnum text-[12.5px]">
              <thead>
                <tr>
                  <th>Start</th>
                  <th>Ramp</th>
                  <th className="r">Δ contribution</th>
                  <th className="r">Δ revenue</th>
                  <th className="r">Existing B2B fill</th>
                  <th className="r">Candidate served</th>
                  <th className="r">D2C displaced</th>
                  <th className="r">P(any B2B short)</th>
                  <th className="r">Peak capacity share</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {res.options.map((o) => {
                  const chosen = o.start_month === rec.start_month && o.ramp === rec.ramp;
                  return (
                    <tr key={`${o.start_month}${o.ramp}`} className={chosen ? "font-semibold" : ""}>
                      <td>{month(o.start_month)}</td>
                      <td>{RAMP[o.ramp]}</td>
                      <td className={`r ${o.delta.contribution_inr > 0 ? "text-good" : "text-critical"}`}>
                        {o.delta.contribution_inr > 0 ? "+" : ""}
                        {inr(o.delta.contribution_inr)}
                      </td>
                      <td className="r">{inr(o.delta.revenue_inr)}</td>
                      <td className="r">{pct(o.b2b_fill_existing, 2)}</td>
                      <td className="r">{pct(o.candidate_fill, 1)}</td>
                      <td className="r">{kg(Math.max(0, -o.delta.d2c_allocated_kg))}</td>
                      <td className="r">{pct(o.p_any_b2b_shortfall, 1)}</td>
                      <td className="r">{pct(o.capacity_share, 0)}</td>
                      <td className="font-normal">
                        {o.acceptable ? <Badge>acceptable</Badge> : <span className="text-[12px] text-ink-3">{o.issues.join("; ")}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}

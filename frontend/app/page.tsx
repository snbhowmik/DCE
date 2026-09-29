"use client";
// 01 Overview: brief, headline KPIs, demand vs supply fan, alerts, strategy comparison (PRD §8.1).
import Link from "next/link";
import { useMemo } from "react";
import { Brief } from "@/components/Brief";
import { FanChart, type Span } from "@/components/charts/FanChart";
import { RunGate } from "@/components/RunGate";
import { Card, LegendKey, Num, PageHeader, Stat, StatusIcon, StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import { day, inr, kg, modeLabel, pct, shortDay } from "@/lib/format";
import { useSelection } from "@/lib/selection";
import type { Payload } from "@/lib/types";
import { useAsync } from "@/lib/useAsync";

export default function Overview() {
  return <RunGate>{(p) => <OverviewBody p={p} />}</RunGate>;
}

function OverviewBody({ p }: { p: Payload }) {
  const k = p.kpis;
  const r = p.run;
  const end = r.months[r.months.length - 1].end;
  return (
    <div className="grid gap-5">
      <PageHeader
        title="Overview"
        lede={
          <>
            <span className="font-mono">{r.world_id}</span> · {modeLabel(r.mode)} strategy · plan for {day(r.horizon[0])} to{" "}
            {day(end)} ({r.months.length} months, {r.horizon.length} weeks). Capacity planned at its P
            {Math.round(r.mode_config.q_capacity * 100)} level; stress-tested over {p.stress.n_paths} simulated futures.
          </>
        }
      />
      <Brief runId={r.run_id} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Expected revenue"
          value={inr(k.revenue_inr.mean)}
          sub={`P10 ${inr(k.revenue_inr.p10)} · P90 ${inr(k.revenue_inr.p90)}`}
          src="kpis.revenue_inr.mean"
        />
        <Stat label="B2B fill rate" value={pct(k.b2b_fill_rate.mean, 1)} sub="expected, over all futures" src="kpis.b2b_fill_rate.mean" />
        <Stat label="D2C fill rate" value={pct(k.d2c_fill_rate.mean, 1)} sub="expected, over all futures" src="kpis.d2c_fill_rate.mean" />
        <Stat
          label="Chance any B2B account is short"
          value={pct(k.p_any_b2b_shortfall, 1)}
          sub="by more than 0.1% of its orders"
          src="kpis.p_any_b2b_shortfall"
          tone={k.p_any_b2b_shortfall > 0.1 ? "critical" : undefined}
        />
        <Stat
          label="Expected waste"
          value={kg(k.waste_kg.mean)}
          sub={`P90 ${kg(k.waste_kg.p90)} · perishable`}
          src="kpis.waste_kg.mean"
        />
        <Stat
          label="Co-manufacturing"
          value={k.coman_requested_kg > 0 ? kg(k.coman_requested_kg) : "Not used"}
          sub={k.coman_requested_kg > 0 ? `${inr(k.coman_cost_inr)} requested` : "in-house covers the plan"}
          src="kpis.coman_requested_kg"
        />
      </div>
      <div className="grid gap-5 xl:grid-cols-[1fr_360px]">
        <DemandSupply p={p} />
        <div className="grid content-start gap-5">
          <AlertsCard p={p} />
          <SplitCard p={p} />
        </div>
      </div>
      <ModeCompare p={p} />
    </div>
  );
}

function DemandSupply({ p }: { p: Payload }) {
  const { history, demandHist, supplyHist } = useMemo(() => {
    const byWeek = new Map<string, number>();
    for (const h of p.demand.history) byWeek.set(h.week_start, (byWeek.get(h.week_start) ?? 0) + h.demand_kg);
    const weeks = [...byWeek.keys()].sort();
    const out = new Map(p.capacity.history.map((c) => [c.week_start, c.output_kg]));
    return {
      history: weeks,
      demandHist: weeks.map((w) => byWeek.get(w) ?? null),
      supplyHist: weeks.map((w) => out.get(w) ?? null),
    };
  }, [p]);
  const spans: Span[] = p.risk.alerts.map((a) => ({
    start: a.start,
    end: a.end,
    tone: a.kind === "breach" ? "critical" : "warning",
    label: a.kind === "breach" ? "Shortfall risk" : "Surplus risk",
  }));
  const sw = p.capacity.supply_with_plan;
  return (
    <Card
      title="Demand vs. supply, weekly"
      subtitle="Last 52 weeks of actual requested demand and in-house output, then the 13-week forecast. Bands are P10–P90; lines are the median."
      right={
        <div className="flex flex-wrap gap-3">
          <LegendKey color="var(--s-demand)" label="Demand" />
          <LegendKey color="var(--s-supply)" label="Supply (in-house + planned co-man)" />
        </div>
      }
    >
      <FanChart
        ariaLabel="Weekly demand and supply: history and 13-week forecast with P10–P90 bands"
        history={history}
        horizon={p.run.horizon}
        spans={spans}
        series={[
          {
            key: "supply",
            label: "Supply",
            color: "var(--s-supply)",
            band: "var(--band-supply)",
            history: supplyHist,
            forecast: { q10: sw.map((s) => s.supply_q10), q50: sw.map((s) => s.supply_q50), q90: sw.map((s) => s.supply_q90) },
          },
          {
            key: "demand",
            label: "Demand",
            color: "var(--s-demand)",
            band: "var(--band-demand)",
            history: demandHist,
            forecast: p.demand.forecast.total,
          },
        ]}
      />
    </Card>
  );
}

function AlertsCard({ p }: { p: Payload }) {
  const alerts = p.risk.alerts;
  return (
    <Card
      title="Alerts"
      subtitle={`Shortfall flagged when P(demand > supply) ≥ ${pct(p.risk.breach_threshold, 0)} (this mode's threshold).`}
      right={
        <Link href="/alerts" className="text-[12.5px] text-ink-2 hover:underline">
          Details →
        </Link>
      }
    >
      {alerts.length === 0 ? (
        <StatusPill tone="good">No shortfall or surplus alerts</StatusPill>
      ) : (
        <ul className="grid gap-3">
          {alerts.map((a, i) => (
            <li key={i} className="flex gap-3">
              <StatusIcon tone={a.kind === "breach" ? "critical" : "warning"} size={16} />
              <div className="text-[13px]">
                <div className="font-medium">
                  {a.kind === "breach" ? "Shortfall risk" : "Surplus risk"} from {shortDay(a.start)}
                  <span className="font-normal text-ink-3"> · in {a.weeks_until} wk</span>
                </div>
                <div className="text-ink-2">
                  Peak probability <Num src={`risk.alerts[${i}].peak_probability`}>{pct(a.peak_probability, 0)}</Num>, expected{" "}
                  <Num src={`risk.alerts[${i}].expected_kg`}>{kg(a.expected_kg)}</Num>
                  {a.kind === "surplus" ? " that may spoil" : " short"}.
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function SplitCard({ p }: { p: Payload }) {
  const k = p.kpis;
  const total = k.d2c_allocated_kg + k.b2b_allocated_kg || 1;
  const d = k.d2c_allocated_kg / total;
  return (
    <Card title="Where the output goes" subtitle={`${kg(k.allocated_kg)} allocated of ${kg(k.demand_kg)} forecast demand`}>
      <div className="flex h-3 w-full overflow-hidden rounded-sm" role="img" aria-label={`D2C ${pct(d, 0)}, B2B ${pct(1 - d, 0)}`}>
        <div style={{ width: `${d * 100}%`, background: "var(--s-d2c)" }} />
        <div className="w-[2px] bg-surface" />
        <div className="flex-1" style={{ background: "var(--s-b2b)" }} />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-[13px]">
        <div>
          <LegendKey kind="band" color="var(--s-d2c)" label="D2C" />
          <div className="mt-0.5 text-[17px] font-semibold">
            <Num src="kpis.d2c_allocated_kg">{kg(k.d2c_allocated_kg)}</Num>
          </div>
        </div>
        <div>
          <LegendKey kind="band" color="var(--s-b2b)" label="B2B" />
          <div className="mt-0.5 text-[17px] font-semibold">
            <Num src="kpis.b2b_allocated_kg">{kg(k.b2b_allocated_kg)}</Num>
          </div>
        </div>
      </div>
      {k.unmet_kg > 0.5 && (
        <p className="mt-2 text-[12.5px] text-ink-2">
          Unmet in the plan: <Num src="kpis.unmet_kg">{kg(k.unmet_kg)}</Num>
        </p>
      )}
    </Card>
  );
}

function ModeCompare({ p }: { p: Payload }) {
  const { world, setMode } = useSelection();
  const { data } = useAsync(world ? `cmp:${world}:${p.run.run_id}` : null, () => api.compare(world!));
  if (!data) return null;
  const rows = [
    { label: "Expected revenue", f: (m: (typeof data.modes)[0]) => inr(m.kpis.revenue_inr.mean) },
    { label: "Expected contribution", f: (m: (typeof data.modes)[0]) => inr(m.kpis.contribution_inr.mean) },
    { label: "B2B fill rate", f: (m: (typeof data.modes)[0]) => pct(m.kpis.b2b_fill_rate.mean, 2) },
    { label: "D2C fill rate", f: (m: (typeof data.modes)[0]) => pct(m.kpis.d2c_fill_rate.mean, 2) },
    { label: "Chance any B2B account is short", f: (m: (typeof data.modes)[0]) => pct(m.kpis.p_any_b2b_shortfall, 1) },
    { label: "Expected waste", f: (m: (typeof data.modes)[0]) => kg(m.kpis.waste_kg.mean) },
    { label: "Co-man requested", f: (m: (typeof data.modes)[0]) => (m.kpis.coman_requested_kg ? kg(m.kpis.coman_requested_kg) : "—") },
    { label: "D2C / B2B allocated", f: (m: (typeof data.modes)[0]) => `${kg(m.kpis.d2c_allocated_kg)} / ${kg(m.kpis.b2b_allocated_kg)}` },
    { label: "Alerts", f: (m: (typeof data.modes)[0]) => (m.alerts.length ? m.alerts.map((a) => (a.kind === "breach" ? "shortfall" : "surplus")).join(", ") : "none") },
  ];
  return (
    <Card
      title="Same data, three strategies"
      subtitle="The forecast is identical across modes (hash-checked); only the optimizer's weights, risk quantile and gates change."
    >
      <div className="overflow-x-auto">
        <table className="dtable tnum text-[13px]">
          <thead>
            <tr>
              <th />
              {data.modes.map((m) => (
                <th key={m.mode} className="r">
                  <button
                    onClick={() => setMode(m.mode)}
                    className={`rounded px-1.5 py-0.5 ${m.mode === p.run.mode ? "bg-ink text-[color:var(--surface)]" : "hover:bg-surface-2"}`}
                  >
                    {modeLabel(m.mode)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.label}>
                <td className="text-ink-2">{row.label}</td>
                {data.modes.map((m) => (
                  <td key={m.mode} className={`r ${m.mode === p.run.mode ? "font-semibold" : ""}`}>
                    <span className="trace" tabIndex={0} data-trace={`${m.run_id}\ncompare · ${row.label}`}>
                      {row.f(m)}
                    </span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

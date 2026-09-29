"use client";
// 02 Allocation plan: month × line allocation with reasons, co-man, spend, shadow prices,
// stress test vs. rule baselines (PRD §8.2, FR-14, FR-18, FR-19, NFR-7).
import { Fragment, useMemo } from "react";
import { Decide } from "@/components/Decide";
import { RunGate } from "@/components/RunGate";
import { Badge, Card, CellBar, LegendKey, Num, PageHeader, StatusPill } from "@/components/ui";
import { fixed, inr, kg, kgFull, monthLabel, pct, regionName } from "@/lib/format";
import type { AllocationRow, Payload } from "@/lib/types";

export default function PlanPage() {
  return <RunGate>{(p) => <Plan p={p} />}</RunGate>;
}

function Plan({ p }: { p: Payload }) {
  return (
    <div className="grid gap-5">
      <PageHeader
        title="Allocation plan"
        lede={
          <>
            One mixed-integer program decides kg per channel, region and month, marketing spend and co-manufacturing
            together. Solver status: <strong>{p.plan.status}</strong>. Hover a cell for the solver&apos;s reasons.
          </>
        }
      />
      <AllocationMatrix p={p} />
      <div className="grid gap-5 xl:grid-cols-2">
        <Coman p={p} />
        <ShadowPrices p={p} />
      </div>
      <Baselines p={p} />
      <Spend p={p} />
    </div>
  );
}

const REASON_LABEL: Record<string, string> = {
  fully_served: "fully served",
  capacity_short: "capacity short",
  floor: "contract floor",
  concentration_cap: "concentration cap",
  ineligible: "not eligible",
};

function AllocationMatrix({ p }: { p: Payload }) {
  const months = p.run.months;
  const groups = useMemo(() => {
    const by = new Map<string, AllocationRow[]>();
    for (const r of p.plan.allocation) {
      const key = `${r.channel}|${r.channel === "D2C" ? r.region_id : r.account_id}`;
      by.set(key, [...(by.get(key) ?? []), r]);
    }
    const rows = [...by.entries()].map(([key, rs]) => ({
      channel: rs[0].channel,
      label: rs[0].channel === "D2C" ? regionName(rs[0].region_id) : (rs[0].account_id ?? ""),
      sub: rs[0].channel === "B2B" ? regionName(p.accounts.find((a) => a.account_id === rs[0].account_id)?.region_id) : null,
      key,
      byMonth: new Map(rs.map((r) => [r.month, r])),
      total: rs.reduce((a, r) => a + r.allocated_kg, 0),
    }));
    return (["B2B", "D2C"] as const).map((ch) => ({ ch, rows: rows.filter((r) => r.channel === ch).sort((a, b) => b.total - a.total) }));
  }, [p]);
  const max = Math.max(...p.plan.allocation.map((r) => r.demand_kg), 1);
  return (
    <Card
      title="Allocation by line and month"
      subtitle={`Allocated kg vs. forecast demand at the mode's quantile. B2B lines are commitments; D2C lines are regions.`}
      right={
        <div className="flex gap-3">
          <LegendKey kind="band" color="var(--s-b2b)" label="B2B" />
          <LegendKey kind="band" color="var(--s-d2c)" label="D2C" />
        </div>
      }
    >
      <div className="overflow-x-auto">
        <table className="dtable tnum text-[13px]">
          <thead>
            <tr>
              <th>Line</th>
              {months.map((m) => (
                <th key={m.idx} className="r" colSpan={2}>
                  {monthLabel(m.start)} <span className="font-normal">({m.n_weeks} wk)</span>
                </th>
              ))}
              <th className="r">Total</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <Fragment key={g.ch}>
                <tr>
                  <td colSpan={2 + months.length * 2} className="pt-4 text-[12px] font-medium uppercase tracking-[0.06em] text-ink-3">
                    {g.ch === "B2B" ? "B2B accounts" : "D2C regions"}
                  </td>
                </tr>
                {g.rows.map((row) => (
                  <tr key={row.key}>
                    <td className="whitespace-nowrap">
                      <span className="mr-2 inline-block h-2 w-2 rounded-sm" style={{ background: g.ch === "B2B" ? "var(--s-b2b)" : "var(--s-d2c)" }} />
                      <span className={g.ch === "B2B" ? "font-mono text-[12.5px]" : ""}>{row.label}</span>
                      {row.sub && <span className="ml-1.5 text-[12px] text-ink-3">{row.sub}</span>}
                    </td>
                    {months.map((m) => {
                      const c = row.byMonth.get(m.idx);
                      if (!c) return <td key={m.idx} colSpan={2} />;
                      const reasons = (c.reasons ?? []).map((r) => `${REASON_LABEL[r.code] ?? r.code}: ${r.detail}`).join("\n");
                      return (
                        <Fragment key={m.idx}>
                          <td className="r w-28">
                            <span
                              className="trace"
                              tabIndex={0}
                              data-trace={`${p.run.run_id}\nplan.allocation · ${row.label} · month ${m.idx + 1}\n${reasons}`}
                            >
                              {kgFull(c.allocated_kg)}
                            </span>
                            {c.unmet_kg > 0.5 && <div className="text-[11.5px] text-critical">{kg(c.unmet_kg)} unmet</div>}
                          </td>
                          <td className="w-24">
                            <CellBar value={c.allocated_kg} max={max} color={g.ch === "B2B" ? "var(--s-b2b)" : "var(--s-d2c)"} />
                          </td>
                        </Fragment>
                      );
                    })}
                    <td className="r font-medium">{kgFull(row.total)}</td>
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {p.plan.slacks.length > 0 && (
        <div className="mt-4 grid gap-1">
          <StatusPill tone="critical">Constraints relaxed to find a plan</StatusPill>
          {p.plan.slacks.map((s) => (
            <div key={s.name} className="text-[12.5px] text-ink-2">
              {s.description}: {num1(s.amount)}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

const num1 = (x: number) => x.toLocaleString("en-IN", { maximumFractionDigits: 1 });

function Coman({ p }: { p: Payload }) {
  const partners = p.capacity.partners;
  return (
    <Card
      title="Co-manufacturing"
      subtitle="Activation honours lead time, minimum weekly volume and minimum run. Delivered volume is haircut by the partner's reliability."
    >
      {partners.length === 0 ? (
        <p className="text-[13px] text-ink-2">No co-man partners in this world.</p>
      ) : (
        partners.map((pt) => {
          const rows = p.plan.coman.filter((c) => c.coman_id === pt.coman_id);
          const active = rows.filter((r) => r.active);
          return (
            <div key={pt.coman_id} className="mb-4 last:mb-0">
              <div className="mb-2 flex flex-wrap items-center gap-2 text-[13px]">
                <span className="font-mono font-medium">{pt.coman_id}</span>
                <Badge>lead time {pt.lead_time_weeks} wk</Badge>
                <Badge>
                  {kgFull(pt.min_kg_per_week)}–{kgFull(pt.max_kg_per_week)}/wk
                </Badge>
                <Badge>{inr(pt.unit_cost_inr_per_kg)}/kg</Badge>
                <Badge>
                  reliability {pct(pt.reliability_mean, 0)}
                  {pt.reliability_from_prior ? " (prior)" : ""}
                </Badge>
              </div>
              <table className="dtable tnum text-[13px]">
                <thead>
                  <tr>
                    <th>Month</th>
                    <th>Status</th>
                    <th className="r">Requested</th>
                    <th className="r">Expected delivered</th>
                    <th className="r">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.month}>
                      <td>{monthLabel(p.run.months[r.month].start)}</td>
                      <td>{r.active ? <Badge>active</Badge> : <span className="text-ink-3">off</span>}</td>
                      <td className="r">
                        <Num src={`plan.coman[${pt.coman_id}, month ${r.month}].requested_kg`}>{kgFull(r.requested_kg)}</Num>
                      </td>
                      <td className="r">{kgFull(r.expected_delivered_kg)}</td>
                      <td className="r">{inr(r.cost_inr)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {active.length > 0 && (
                <div className="mt-3">
                  <Decide
                    runId={p.run.run_id}
                    kind="coman"
                    itemKey={pt.coman_id}
                    summary={`Activate ${pt.coman_id}: ${kg(active.reduce((a, r) => a + r.requested_kg, 0))} over ${active.length} month(s)`}
                    details={{ months: active.map((r) => r.month), requested_kg: active.map((r) => r.requested_kg) }}
                  />
                </div>
              )}
            </div>
          );
        })
      )}
    </Card>
  );
}

function ShadowPrices({ p }: { p: Payload }) {
  const rows = [...p.plan.binding].sort((a, b) => Math.abs(b.shadow_price) - Math.abs(a.shadow_price)).slice(0, 8);
  return (
    <Card title="What is binding" subtitle="Shadow prices from the LP relaxation: the value of relaxing each constraint by one unit.">
      {rows.length === 0 ? (
        <p className="text-[13px] text-ink-2">Nothing binds: capacity and contracts leave slack everywhere.</p>
      ) : (
        <table className="dtable tnum text-[13px]">
          <thead>
            <tr>
              <th>Constraint</th>
              <th>Meaning</th>
              <th className="r">Value</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.constraint}>
                <td className="font-mono text-[12px]">{b.constraint}</td>
                <td className="text-ink-2">{b.meaning}</td>
                <td className="r">
                  <Num src={`plan.binding[${b.constraint}].shadow_price`}>{inr(b.shadow_price, false)}</Num>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-3 text-[12px] text-ink-3">
        A negative capacity value means extra supply would only add waste cost: the plan is not capacity-constrained in
        that month.
      </p>
    </Card>
  );
}

const METRICS: { key: string; label: string; f: (v: number) => string; better: "high" | "low" }[] = [
  { key: "revenue_inr", label: "Revenue", f: (v) => inr(v), better: "high" },
  { key: "contribution_inr", label: "Contribution", f: (v) => inr(v), better: "high" },
  { key: "b2b_fill_rate", label: "B2B fill", f: (v) => pct(v, 2), better: "high" },
  { key: "d2c_fill_rate", label: "D2C fill", f: (v) => pct(v, 2), better: "high" },
  { key: "any_b2b_shortfall", label: "P(any B2B short)", f: (v) => pct(v, 1), better: "low" },
  { key: "b2b_penalty_inr", label: "B2B penalties", f: (v) => inr(v), better: "low" },
  { key: "waste_kg", label: "Waste", f: (v) => kg(v), better: "low" },
];

const PLAN_LABEL: Record<string, string> = {
  b2b_first: "Rule: B2B first",
  proportional: "Rule: proportional",
  fcfs: "Rule: first come, first served",
};

function Baselines({ p }: { p: Payload }) {
  const plans = [...new Set(p.stress.comparison.map((c) => c.plan))];
  const get = (plan: string, m: string) => p.stress.comparison.find((c) => c.plan === plan && c.metric === m);
  return (
    <Card
      title="Stress test: optimizer vs. rule-based baselines"
      subtitle={`Every plan is replayed on the same ${p.stress.n_paths} simulated futures of demand, batch yield and co-man delivery. Expected values; best in each column in bold.`}
    >
      <div className="overflow-x-auto">
        <table className="dtable tnum text-[13px]">
          <thead>
            <tr>
              <th>Plan</th>
              {METRICS.map((m) => (
                <th key={m.key} className="r">
                  {m.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {plans.map((plan) => (
              <tr key={plan}>
                <td className="whitespace-nowrap">
                  {PLAN_LABEL[plan] ?? (
                    <span className="font-medium">
                      Optimizer <Badge>{plan}</Badge>
                    </span>
                  )}
                </td>
                {METRICS.map((m) => {
                  const v = get(plan, m.key);
                  const vals = plans.map((pl) => get(pl, m.key)?.mean ?? NaN);
                  const best = m.better === "high" ? Math.max(...vals) : Math.min(...vals);
                  const isBest = v && Math.abs(v.mean - best) <= Math.abs(best) * 1e-4 + 1e-9;
                  return (
                    <td key={m.key} className={`r ${isBest ? "font-semibold" : "text-ink-2"}`}>
                      {v ? (
                        <span
                          className="trace"
                          tabIndex={0}
                          data-trace={`${p.run.run_id}\nstress.comparison · ${plan} · ${m.key}\nP10 ${m.f(v.p10)} · P90 ${m.f(v.p90)}`}
                        >
                          {m.f(v.mean)}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-[12px] text-ink-3">
        Baselines plan on the same forecasts, spend the marketing plan and never activate co-man. Contribution subtracts
        production, co-man, spend and B2B penalties from revenue.
      </p>
    </Card>
  );
}

function Spend({ p }: { p: Payload }) {
  const regions = p.run.regions;
  const rows = regions.map((r) => {
    const s = p.plan.spend.filter((x) => x.region_id === r);
    return {
      r,
      planned: s.reduce((a, x) => a + x.planned_inr, 0),
      rec: s.reduce((a, x) => a + x.recommended_inr, 0),
      low: s.some((x) => x.low_confidence),
      gated: s.some((x) => x.gated),
      res: s[0]?.res ?? null,
    };
  });
  if (!rows.some((r) => r.planned || r.rec)) return null;
  const max = Math.max(...rows.map((r) => Math.max(r.planned, r.rec)), 1);
  const allLow = rows.every((r) => r.low);
  return (
    <Card
      title="Marketing spend by region"
      subtitle="Spend is a decision variable: it creates D2C demand that must then be served. Expansion needs response evidence; thin evidence gets a capped exploration budget."
    >
      {allLow && (
        <div className="mb-3">
          <StatusPill tone="warning">
            Spend response not identifiable in any region on this data: realized spend is zero for most of history, so spend is held
            at plan
          </StatusPill>
        </div>
      )}
      <table className="dtable tnum text-[13px]">
        <thead>
          <tr>
            <th>Region</th>
            <th className="r">Planned (3 mo)</th>
            <th className="r">Recommended</th>
            <th className="w-1/3" />
            <th className="r">RES</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows
            .sort((a, b) => b.rec - a.rec)
            .map((row) => (
              <tr key={row.r}>
                <td>{regionName(row.r)}</td>
                <td className="r text-ink-2">{inr(row.planned)}</td>
                <td className="r font-medium">
                  <Num src={`plan.spend[${row.r}].recommended_inr (sum)`}>{inr(row.rec)}</Num>
                </td>
                <td>
                  <CellBar value={row.rec} max={max} />
                </td>
                <td className="r">{fixed(row.res)}</td>
                <td>{row.low ? <Badge>held at plan: low confidence</Badge> : row.gated ? <Badge>gated</Badge> : <Badge>optimized</Badge>}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </Card>
  );
}

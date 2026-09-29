"use client";
// 05 Alerts & mitigations: weekly shortfall/surplus probabilities, alert episodes, the playbook
// (ranking by re-solve lands with T6.2–T6.3) (PRD §8.5, FR-22–25, US2, US8).
import { ProbChart } from "@/components/charts/ProbChart";
import { Decide } from "@/components/Decide";
import { RunGate } from "@/components/RunGate";
import { Badge, Card, Num, PageHeader, StatusIcon, StatusPill, Upcoming } from "@/components/ui";
import { day, kg, kgFull, pct, shortDay } from "@/lib/format";
import type { Payload } from "@/lib/types";

export default function AlertsPage() {
  return <RunGate>{(p) => <Alerts p={p} />}</RunGate>;
}

const PLAYBOOK = [
  ["M1", "D2C waitlist / pre-order", "days", "yes", "goodwill cost, low cash cost"],
  ["M2", "Spend throttle / reallocation", "days to act, weeks to take effect", "yes", "lost growth in the throttled region"],
  ["M3", "Co-man activation", "contract lead time", "partly (minimum commitments)", "cost premium"],
  ["M4", "B2B onboarding deferral or phased ramp", "immediate", "yes", "relationship risk, delayed revenue"],
  ["M5", "Delivery-slot / promise-date spreading", "days", "yes", "customer convenience"],
  ["M6", "Contract-aware rebalancing", "immediate", "yes", "explicit penalty cost; last resort"],
];

function addWeeks(iso: string, w: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 7 * w);
  return d.toISOString().slice(0, 10);
}

function Alerts({ p }: { p: Payload }) {
  const r = p.risk;
  const w = r.weekly;
  const partner = p.capacity.partners[0];
  return (
    <div className="grid gap-5">
      <PageHeader
        title="Alerts & mitigations"
        lede={
          <>
            A shortfall alert fires when the chance that demand exceeds supply in a week reaches{" "}
            <strong>{pct(r.breach_threshold, 0)}</strong> (this mode&apos;s threshold). A surplus alert fires when supply exceeds
            demand by more than {pct(r.surplus_share, 0)} with at least {pct(r.surplus_probability, 0)} probability: with a{" "}
            {p.capacity.perishability.shelf_life_days}-day shelf life, surplus is waste.
          </>
        }
      />
      <div className="grid gap-4 lg:grid-cols-2">
        {r.alerts.length === 0 && (
          <Card title="No alerts">
            <StatusPill tone="good">Supply covers demand at this mode&apos;s thresholds for all 13 weeks</StatusPill>
          </Card>
        )}
        {r.alerts.map((a, i) => {
          const breach = a.kind === "breach";
          const actBy = breach && partner ? addWeeks(a.start, -partner.lead_time_weeks) : null;
          return (
            <Card
              key={i}
              title={
                <span className="flex items-center gap-2">
                  <StatusIcon tone={breach ? "critical" : "warning"} size={16} />
                  {breach ? "Shortfall risk" : "Surplus risk"}
                </span>
              }
              right={<Badge>{a.start === a.end ? `week of ${shortDay(a.start)}` : `${shortDay(a.start)} – ${shortDay(a.end)}`}</Badge>}
            >
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <div className="text-[12px] text-ink-2">Starts in</div>
                  <div className="text-[22px] font-semibold tnum">
                    <Num src={`risk.alerts[${i}].weeks_until`}>{a.weeks_until} wk</Num>
                  </div>
                </div>
                <div>
                  <div className="text-[12px] text-ink-2">Peak probability</div>
                  <div className="text-[22px] font-semibold tnum">
                    <Num src={`risk.alerts[${i}].peak_probability`}>{pct(a.peak_probability, 0)}</Num>
                  </div>
                </div>
                <div>
                  <div className="text-[12px] text-ink-2">Expected {breach ? "shortfall" : "surplus"}</div>
                  <div className="text-[22px] font-semibold tnum">
                    <Num src={`risk.alerts[${i}].expected_kg`}>{kg(a.expected_kg)}</Num>
                  </div>
                </div>
              </div>
              <p className="mt-3 text-[13px] text-ink-2">
                {breach
                  ? actBy
                    ? `Co-man needs ${partner.lead_time_weeks} weeks' notice: to cover this, activation must be decided by the week of ${day(actBy)}.`
                    : "No co-man partner: mitigations are demand-side (waitlist, spend throttle, rebalancing)."
                  : "Options from the playbook: raise spend in high-RES regions, accelerate B2B onboarding, or trim co-man volume before it is produced."}
              </p>
              <div className="mt-3">
                <Decide
                  runId={p.run.run_id}
                  kind="alert"
                  itemKey={`${a.kind}:${a.start}`}
                  summary={`${breach ? "Shortfall" : "Surplus"} alert from ${a.start}: acknowledge and act`}
                />
              </div>
            </Card>
          );
        })}
      </div>
      <Card title="Weekly risk" subtitle="Probability over the simulated futures, per week of the horizon.">
        <ProbChart
          weeks={w.map((x) => x.week_start)}
          breach={w.map((x) => x.p_breach)}
          surplus={w.map((x) => x.p_surplus)}
          threshold={r.breach_threshold}
          surplusThreshold={r.surplus_probability}
        />
        <div className="mt-4 overflow-x-auto">
          <table className="dtable tnum text-[12.5px]">
            <thead>
              <tr>
                <th>Week</th>
                <th className="r">Demand P50 (P10–P90)</th>
                <th className="r">Supply P50 (P10–P90)</th>
                <th className="r">P(shortfall)</th>
                <th className="r">E[shortfall]</th>
                <th className="r">P(surplus)</th>
                <th className="r">E[surplus]</th>
                <th>Flag</th>
              </tr>
            </thead>
            <tbody>
              {w.map((x) => (
                <tr key={x.week_start}>
                  <td>{shortDay(x.week_start)}</td>
                  <td className="r">
                    {kgFull(x.demand_q50)} <span className="text-ink-3">({kg(x.demand_q10)}–{kg(x.demand_q90)})</span>
                  </td>
                  <td className="r">
                    {kgFull(x.supply_q50)} <span className="text-ink-3">({kg(x.supply_q10)}–{kg(x.supply_q90)})</span>
                  </td>
                  <td className="r">{pct(x.p_breach, 1)}</td>
                  <td className="r">{kg(x.expected_shortfall_kg)}</td>
                  <td className="r">{pct(x.p_surplus, 1)}</td>
                  <td className="r">{kg(x.expected_surplus_kg)}</td>
                  <td>
                    {x.breach ? <StatusPill tone="critical">shortfall</StatusPill> : x.surplus ? <StatusPill tone="warning">surplus</StatusPill> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card
        title="Mitigation playbook"
        subtitle="From operations and revenue-management practice. A mitigation is only offered if it can take effect before the alert week."
      >
        <table className="dtable text-[13px]">
          <thead>
            <tr>
              <th>ID</th>
              <th>Mitigation</th>
              <th>Lead time</th>
              <th>Reversible</th>
              <th>Cost / harm</th>
            </tr>
          </thead>
          <tbody>
            {PLAYBOOK.map((m) => (
              <tr key={m[0]}>
                <td className="font-mono">{m[0]}</td>
                <td className="font-medium">{m[1]}</td>
                <td className="text-ink-2">{m[2]}</td>
                <td className="text-ink-2">{m[3]}</td>
                <td className="text-ink-2">{m[4]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Upcoming phase="Phase 6 · T6.2–T6.3" title="Ranked mitigations with quantified impact">
        Each feasible mitigation will be applied as a lever, re-solved and stress-tested; the list is then ranked by shortfall
        reduced per rupee (with harm and reversibility) and every option gets its act-by date.
      </Upcoming>
    </div>
  );
}

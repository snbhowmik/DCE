"use client";
// 04 B2B accounts: Account Quality Score with components, commitments, concentration, plan fill;
// onboarding simulator lands with T7.1 (PRD §8.4, US4).
import { Sparkfan } from "@/components/charts/Sparkfan";
import { RunGate } from "@/components/RunGate";
import { Badge, Card, Num, PageHeader, StatusPill, Upcoming, ZBar } from "@/components/ui";
import { day, fixed, kg, kgFull, pct, regionName } from "@/lib/format";
import type { Payload } from "@/lib/types";

export default function AccountsPage() {
  return <RunGate>{(p) => <Accounts p={p} />}</RunGate>;
}

const COMPONENTS = [
  ["Volume", "z_volume"],
  ["Stability", "z_stability"],
  ["Reach", "z_reach"],
  ["Margin", "z_margin"],
  ["Reliability", "z_reliability"],
  ["Penalty", "z_penalty"],
] as const;

function Accounts({ p }: { p: Payload }) {
  const rows = [...p.accounts].sort((a, b) => (b.aqs?.aqs ?? -9) - (a.aqs?.aqs ?? -9));
  const cap = p.run.mode_config;
  return (
    <div className="grid gap-5">
      <PageHeader
        title="B2B accounts"
        lede={
          <>
            Account Quality Score (AQS) weighs volume, order stability, reach, margin, payment reliability and contract
            penalty. The weighting follows the strategy mode: <Badge>{p.accounts.find((a) => a.aqs)?.aqs?.profile ?? "—"}</Badge>.
          </>
        }
      />
      <Card title="Accounts" subtitle="Components are z-scores against the other accounts (blue above average, red below).">
        <div className="overflow-x-auto">
          <table className="dtable tnum text-[13px]">
            <thead>
              <tr>
                <th>Account</th>
                <th>Region</th>
                <th className="r">Commitment / mo</th>
                <th className="r">AQS</th>
                {COMPONENTS.map(([l]) => (
                  <th key={l} className="text-[11.5px]">{l}</th>
                ))}
                <th className="r">Cap. share</th>
                <th className="r">Plan fill</th>
                <th>Next 13 wk</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => {
                const alloc = a.allocation.reduce((s, r) => s + r.allocated_kg, 0);
                const dem = a.allocation.reduce((s, r) => s + r.demand_kg, 0);
                const fc = p.demand.by_account[a.account_id];
                return (
                  <tr key={a.account_id} className={a.included ? "" : "opacity-60"}>
                    <td className="whitespace-nowrap">
                      <div className="font-mono text-[12.5px]">{a.account_id}</div>
                      <div className="text-[11.5px] text-ink-3">
                        {a.aqs?.account_type ?? "—"} · {a.status}
                        {a.end_assumed_rolling ? " · rolling renewal" : a.effective_end ? ` · ends ${day(a.effective_end)}` : ""}
                      </div>
                    </td>
                    <td>{regionName(a.region_id)}</td>
                    <td className="r">{kgFull(a.committed_kg_per_month)}</td>
                    <td className="r">
                      {a.aqs ? (
                        <>
                          <Num src={`accounts[${a.account_id}].aqs.aqs`}>{fixed(a.aqs.aqs)}</Num>
                          <div className="text-[11px] text-ink-3">
                            {a.aqs.prior ? "prior-based" : `conf. ${pct(a.aqs.confidence, 0)}`}
                          </div>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    {COMPONENTS.map(([l, k]) => (
                      <td key={l}>
                        <ZBar z={a.aqs ? a.aqs[k] : null} width={52} />
                      </td>
                    ))}
                    <td className="r">
                      {a.aqs ? pct(a.aqs.capacity_share, 1) : "—"}
                      {a.aqs?.exceeds_concentration_cap && (
                        <div>
                          <StatusPill tone="warning">above cap</StatusPill>
                        </div>
                      )}
                    </td>
                    <td className="r">
                      {a.in_plan ? (
                        <>
                          <Num src={`accounts[${a.account_id}].allocation`}>{pct(dem ? alloc / dem : null, 1)}</Num>
                          <div className="text-[11px] text-ink-3">{kg(alloc)}</div>
                        </>
                      ) : (
                        <span className="text-[12px] text-ink-3">{a.reason ?? "not in plan"}</span>
                      )}
                    </td>
                    <td>{fc && <Sparkfan q10={fc.q10} q50={fc.q50} q90={fc.q90} color="var(--s-b2b)" band="rgba(235,104,52,0.16)" width={96} height={28} />}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-[12px] text-ink-3">
          B2B demand is forecast per account from its order-to-commitment ratio; strategy mode {p.run.mode} plans B2B at the
          P{Math.round(Number(cap.q_demand_b2b ?? 0.5) * 100)} of that forecast.
        </p>
      </Card>
      <Upcoming phase="Phase 7 · T7.1" title="Onboarding simulator">
        Enter a candidate account (volume, price, penalty, region, earliest start). The engine re-solves the whole horizon with
        and without it across start months and ramp profiles (full, 50→100%, 33→66→100%) and recommends accept now, accept
        from month X, phase in, or decline, with revenue, fill-rate and D2C-displacement deltas.
      </Upcoming>
    </div>
  );
}

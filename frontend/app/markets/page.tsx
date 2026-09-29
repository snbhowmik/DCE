"use client";
// 03 Markets: per-region scorecards — funnel economics, Response Evidence Score with components,
// spend-response model status, recommended spend and the region's D2C forecast (PRD §8.3, US3).
import { Sparkfan } from "@/components/charts/Sparkfan";
import { RunGate } from "@/components/RunGate";
import { Badge, Card, Num, PageHeader, StatusPill, ZBar } from "@/components/ui";
import { day, fixed, inr, kg, num, pct, regionName } from "@/lib/format";
import type { Market, Payload } from "@/lib/types";

export default function MarketsPage() {
  return <RunGate>{(p) => <Markets p={p} />}</RunGate>;
}

function Markets({ p }: { p: Payload }) {
  const ms = [...p.markets].sort((a, b) => (b.res?.res ?? -9) - (a.res?.res ?? -9));
  const allLow = ms.every((m) => m.response?.low_confidence ?? true);
  const period = ms[0]?.funnel_period;
  return (
    <div className="grid gap-5">
      <PageHeader
        title="Markets"
        lede={
          <>
            D2C regions ranked by Response Evidence Score (RES): does spend in this region reliably turn into profitable,
            retained customers? Funnel figures cover {period ? `${day(period[0])} – ${day(period[1])}` : "the full history"}.
          </>
        }
      />
      {allLow && (
        <div className="rounded-lg border border-hair bg-surface px-4 py-3 text-[13px] text-ink-2">
          <StatusPill tone="warning">Data limitation</StatusPill>
          <span className="ml-2">
            In this data drop, realized marketing spend stops early in the history while planned spend continues. The
            spend-response curve can&apos;t be identified from that, so every region is marked low-confidence and held at its
            planned budget. The engine refuses to extrapolate rather than guess.
          </span>
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {ms.map((m, i) => (
          <Scorecard key={m.region_id} m={m} p={p} rank={i + 1} />
        ))}
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-grid py-1.5 text-[13px] last:border-0">
      <span className="text-ink-2">{label}</span>
      <span className="tnum font-medium">{children}</span>
    </div>
  );
}

function Scorecard({ m, p, rank }: { m: Market; p: Payload; rank: number }) {
  const f = m.funnel_all;
  const cac = f.cac_inr;
  const ltv = m.ltv?.ltv_inr ?? null;
  const fc = p.demand.by_region[m.region_id];
  const next = fc ? fc.q50.reduce((a, v) => a + v, 0) : null;
  const planned = m.spend.reduce((a, s) => a + s.planned_inr, 0);
  const rec = m.spend.reduce((a, s) => a + s.recommended_inr, 0);
  const base = `markets[${m.region_id}]`;
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <span className="font-mono text-[12px] text-ink-3">#{rank}</span>
          {regionName(m.region_id)}
        </span>
      }
      right={
        m.res ? (
          <div className="text-right">
            <div className="text-[11.5px] text-ink-3">RES</div>
            <div className="text-[20px] font-semibold leading-none tnum">
              <Num src={`${base}.res.res`}>{fixed(m.res.res)}</Num>
            </div>
            <div className="mt-0.5 text-[11px] text-ink-3">confidence {pct(m.res.confidence, 0)}</div>
          </div>
        ) : null
      }
    >
      {m.res && (
        <div className="mb-3 grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5 text-[12.5px]">
          {(
            [
              ["Spend lift", m.res.z_lift, "lift"],
              ["Unit economics", m.res.z_econ, "econ"],
              ["Retention", m.res.z_retention, "retention"],
              ["NPS", m.res.z_nps, "nps"],
            ] as const
          ).map(([label, z, key]) => (
            <div key={key} className="contents">
              <span className="text-ink-2">{label}</span>
              <span className="flex items-center gap-2">
                {m.res!.missing_components.includes(key) ? (
                  <span className="text-[12px] text-ink-3">no evidence</span>
                ) : (
                  <>
                    <ZBar z={z} />
                    <span className="tnum text-[12px] text-ink-3">{z >= 0 ? "+" : ""}{z.toFixed(2)}σ</span>
                  </>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
      <Row label="Visitors / leads / conversions">
        {num(f.unique_visitors, 0)} / {num(f.leads, 0)} / {num(f.conversions, 0)}
      </Row>
      <Row label="Bounce rate · lead→customer">
        {pct(f.bounce_rate, 0)} · {pct(f.conversion_rate, 1)}
      </Row>
      <Row label="CAC · LTV · LTV:CAC">
        <Num src={`${base}.funnel_all.cac_inr`}>{inr(cac)}</Num> · <Num src={`${base}.ltv.ltv_inr`}>{inr(ltv)}</Num> ·{" "}
        {cac && ltv ? `${(ltv / cac).toFixed(1)}×` : "—"}
      </Row>
      <Row label="Churn · NPS">
        {pct(f.churn_rate, 1)} · {f.nps == null ? "—" : f.nps.toFixed(0)}
      </Row>
      <div className="mt-3 flex items-center justify-between gap-3">
        <div>
          <div className="text-[12px] text-ink-2">D2C demand, next 13 weeks (P50)</div>
          <div className="text-[16px] font-semibold tnum">
            <Num src={`demand.by_region[${m.region_id}].q50 (sum)`}>{kg(next)}</Num>
          </div>
        </div>
        {fc && <Sparkfan q10={fc.q10} q50={fc.q50} q90={fc.q90} />}
      </div>
      <div className="mt-3 rounded-md bg-surface-2 px-3 py-2 text-[12.5px]">
        <div className="flex items-center justify-between">
          <span className="text-ink-2">Spend: planned → recommended</span>
          <span className="tnum font-medium">
            {inr(planned)} → <Num src={`${base}.spend.recommended_inr (sum)`}>{inr(rec)}</Num>
          </span>
        </div>
        <div className="mt-1 text-ink-3">
          {m.response?.low_confidence ? (
            <>
              <Badge>held at plan</Badge> {m.response.reasons.join("; ")}
            </>
          ) : m.response_skipped ? (
            m.response_skipped
          ) : (
            <>
              Elasticity {m.response?.elasticity?.toFixed(2)} · lift at mean spend {kg(m.response?.lift_at_mean_kg)}/wk
            </>
          )}
        </div>
      </div>
    </Card>
  );
}

"use client";
// 06 Scenarios: hands-on what-if workbench (T9.3 executor, PRD FR-28, US5). Levers edit copies of
// the cached forecast / capacity paths; the optimizer re-solves and stress-tests in about a second.
import { useEffect, useMemo, useState } from "react";
import { Badge, Card, PageHeader, StatusIcon, StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import { inr, kg, modeLabel, pct, shortDay } from "@/lib/format";
import { useSelection } from "@/lib/selection";
import type { Levers, Payload, ScenarioResult } from "@/lib/types";

const DEFAULT: Levers = {
  mode: "STABILITY",
  d2c_demand_pct: 0,
  b2b_demand_pct: 0,
  account_id: null,
  account_demand_pct: 0,
  from_week: 1,
  capacity_pct: 0,
  capacity_from_week: 1,
  capacity_to_week: 13,
  coman_available: true,
  budget_pct: 0,
};

const CUSTOM_DEFAULT = { q_capacity: 0.5, b2b_service_floor: 0.9, breach_threshold: 0.25, exploration_share: 0.05, pen: 1, gw: 0.3 };

export default function ScenariosPage() {
  const { world, mode, baseRunId, payload, setScenario } = useSelection();
  const [lv, setLv] = useState<Levers>({ ...DEFAULT, mode });
  const [custom, setCustom] = useState(CUSTOM_DEFAULT);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<{ r: ScenarioResult; p: Payload; base: Payload } | null>(null);
  const [history, setHistory] = useState<{ r: ScenarioResult; revenue: number }[]>([]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- follow the context-bar strategy
  useEffect(() => setLv((l) => ({ ...l, mode: l.mode === "CUSTOM" ? "CUSTOM" : mode })), [mode]);

  const accounts = payload?.run.accounts ?? [];
  const set = (k: keyof Levers) => (v: unknown) => setLv((l) => ({ ...l, [k]: v }));

  const solve = async (levers = lv) => {
    if (!world) return;
    setBusy(true);
    setErr(null);
    try {
      const body: Levers =
        levers.mode === "CUSTOM"
          ? {
              ...levers,
              mode_overrides: {
                q_capacity: custom.q_capacity,
                b2b_service_floor: custom.b2b_service_floor,
                breach_threshold: custom.breach_threshold,
                exploration_share: custom.exploration_share,
                weights: { pen: custom.pen, gw: custom.gw },
              },
            }
          : { ...levers, mode_overrides: null };
      const r = await api.scenario(world, body);
      const baseId = r.parent_run_id ?? baseRunId;
      const [p, base] = await Promise.all([api.payload(r.run_id), api.payload(baseId!)]);
      setRes({ r, p, base });
      setHistory((h) => [{ r, revenue: p.kpis.revenue_inr.mean }, ...h].slice(0, 8));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const presets = useMemo(() => {
    const big = [...(payload?.accounts ?? [])]
      .filter((a) => a.in_plan)
      .sort((a, b) => (b.committed_kg_per_month ?? 0) - (a.committed_kg_per_month ?? 0))[0]?.account_id;
    const m = lv.mode;
    return [
      ...(big ? [{ label: `${big} doubles orders from month 2`, lv: { ...DEFAULT, mode: m, account_id: big, account_demand_pct: 100, from_week: 5 } }] : []),
      { label: "Bioreactor outage: −40% capacity, weeks 3–6", lv: { ...DEFAULT, mode: m, capacity_pct: -40, capacity_from_week: 3, capacity_to_week: 6 } },
      { label: "Co-man partner unavailable", lv: { ...DEFAULT, mode: m, coman_available: false } },
      { label: "Viral surge: D2C +60% from week 4", lv: { ...DEFAULT, mode: m, d2c_demand_pct: 60, from_week: 4 } },
      { label: "Marketing budget −50%", lv: { ...DEFAULT, mode: m, budget_pct: -50 } },
      { label: "Growth push under the same data", lv: { ...DEFAULT, mode: "GROWTH" } },
    ];
  }, [payload, lv.mode]);

  return (
    <div className="grid gap-5">
      <PageHeader
        title="Scenarios"
        lede="Change the world and re-plan. Each what-if edits copies of the forecast and capacity samples, then the optimizer re-solves and re-runs the 500-future stress test, usually in about a second. Forecast models are never refit, and strategy never touches the forecast."
      />
      <div className="grid gap-5 xl:grid-cols-[380px_1fr]">
        <Card title="Levers" subtitle={world ? `World ${world}` : undefined}>
          <div className="grid gap-4 text-[13px]">
            <Field label="Strategy">
              <select value={lv.mode} onChange={(e) => set("mode")(e.target.value)} className="w-full rounded-md border border-hair bg-surface px-2 py-1.5">
                {["STABILITY", "GROWTH", "D2C_EXPANSION", "CUSTOM"].map((m) => (
                  <option key={m} value={m}>
                    {m === "CUSTOM" ? "Custom weights" : modeLabel(m)}
                  </option>
                ))}
              </select>
            </Field>
            {lv.mode === "CUSTOM" && (
              <div className="grid gap-3 rounded-md bg-surface-2 p-3">
                <Slider label="Plan capacity at quantile" value={custom.q_capacity} min={0.05} max={0.95} step={0.05} fmt={(v) => `P${Math.round(v * 100)}`} onChange={(v) => setCustom((c) => ({ ...c, q_capacity: v }))} />
                <Slider label="B2B service floor" value={custom.b2b_service_floor} min={0.5} max={1} step={0.01} fmt={(v) => pct(v, 0)} onChange={(v) => setCustom((c) => ({ ...c, b2b_service_floor: v }))} />
                <Slider label="Penalty weight (B2B shortfall)" value={custom.pen} min={0} max={5} step={0.1} fmt={(v) => `${v.toFixed(1)}×`} onChange={(v) => setCustom((c) => ({ ...c, pen: v }))} />
                <Slider label="Goodwill weight (D2C unmet)" value={custom.gw} min={0} max={2} step={0.1} fmt={(v) => `${v.toFixed(1)}×`} onChange={(v) => setCustom((c) => ({ ...c, gw: v }))} />
                <Slider label="Shortfall alert threshold" value={custom.breach_threshold} min={0.05} max={0.5} step={0.05} fmt={(v) => pct(v, 0)} onChange={(v) => setCustom((c) => ({ ...c, breach_threshold: v }))} />
                <Slider label="Exploration budget share" value={custom.exploration_share} min={0} max={0.3} step={0.01} fmt={(v) => pct(v, 0)} onChange={(v) => setCustom((c) => ({ ...c, exploration_share: v }))} />
              </div>
            )}
            <Slider label="D2C demand" value={lv.d2c_demand_pct ?? 0} min={-50} max={100} step={5} fmt={signed} onChange={set("d2c_demand_pct")} />
            <Slider label="B2B demand (all accounts)" value={lv.b2b_demand_pct ?? 0} min={-50} max={100} step={5} fmt={signed} onChange={set("b2b_demand_pct")} />
            <Field label="One account's orders">
              <div className="flex gap-2">
                <select value={lv.account_id ?? ""} onChange={(e) => set("account_id")(e.target.value || null)} className="flex-1 rounded-md border border-hair bg-surface px-2 py-1.5 font-mono text-[12px]">
                  <option value="">none</option>
                  {accounts.map((a) => (
                    <option key={a}>{a}</option>
                  ))}
                </select>
              </div>
            </Field>
            {lv.account_id && <Slider label={`${lv.account_id} orders`} value={lv.account_demand_pct ?? 0} min={-90} max={200} step={10} fmt={signed} onChange={set("account_demand_pct")} />}
            <Slider label="Demand changes apply from week" value={lv.from_week ?? 1} min={1} max={13} step={1} fmt={(v) => `wk ${v}`} onChange={set("from_week")} />
            <Slider label="In-house capacity" value={lv.capacity_pct ?? 0} min={-80} max={50} step={5} fmt={signed} onChange={set("capacity_pct")} />
            <div className="grid grid-cols-2 gap-3">
              <Slider label="from week" value={lv.capacity_from_week ?? 1} min={1} max={13} step={1} fmt={(v) => `wk ${v}`} onChange={set("capacity_from_week")} />
              <Slider label="to week" value={lv.capacity_to_week ?? 13} min={1} max={13} step={1} fmt={(v) => `wk ${v}`} onChange={set("capacity_to_week")} />
            </div>
            <Slider label="Marketing budget" value={lv.budget_pct ?? 0} min={-100} max={100} step={5} fmt={signed} onChange={set("budget_pct")} />
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={lv.coman_available ?? true} onChange={(e) => set("coman_available")(e.target.checked)} />
              Co-manufacturing available
            </label>
            {err && <div className="text-[12.5px] text-critical">{err}</div>}
            <div className="flex gap-2">
              <button onClick={() => solve()} disabled={busy || !world} className="flex-1 rounded-md bg-ink px-4 py-2 text-[13.5px] font-medium text-[color:var(--surface)] disabled:opacity-50">
                {busy ? "Re-solving…" : "Re-solve plan"}
              </button>
              <button onClick={() => setLv({ ...DEFAULT, mode })} className="rounded-md border border-hair px-3 py-2 text-[13px] hover:bg-surface-2">
                Reset
              </button>
            </div>
            <div>
              <div className="mb-1.5 text-[12px] text-ink-3">Try one</div>
              <div className="flex flex-wrap gap-1.5">
                {presets.map((pr) => (
                  <button
                    key={pr.label}
                    onClick={() => {
                      setLv(pr.lv);
                      void solve(pr.lv);
                    }}
                    className="rounded-full border border-hair px-2.5 py-1 text-left text-[12px] text-ink-2 hover:bg-surface-2 hover:text-ink"
                  >
                    {pr.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </Card>
        <div className="grid content-start gap-5">
          {!res && !busy && (
            <Card title="Result">
              <p className="text-[13px] text-ink-2">
                Move a lever and press <strong>Re-solve plan</strong>, or pick one of the suggestions. The result is compared with
                the base plan, and you can open it in every other screen.
              </p>
            </Card>
          )}
          {busy && !res && <div className="skeleton h-80" />}
          {res && <ResultView res={res} onOpen={() => setScenario(res.r.run_id)} />}
          {history.length > 1 && (
            <Card title="This session's scenarios">
              <ul className="grid gap-1.5 text-[12.5px]">
                {history.map((h) => (
                  <li key={h.r.run_id} className="flex items-center justify-between gap-3">
                    <span className="text-ink-2">
                      <Badge>{modeLabel(h.r.mode)}</Badge> {h.r.changes.join(" · ") || "no lever changes"}
                    </span>
                    <span className="tnum">{inr(h.revenue)}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

const signed = (v: number) => (v > 0 ? `+${v}%` : `${v}%`);

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1">
      <span className="text-[12.5px] text-ink-2">{label}</span>
      {children}
    </div>
  );
}

function Slider({ label, value, min, max, step, fmt, onChange }: { label: string; value: number; min: number; max: number; step: number; fmt: (v: number) => string; onChange: (v: number) => void }) {
  return (
    <label className="grid gap-1">
      <span className="flex justify-between text-[12.5px] text-ink-2">
        {label}
        <span className={`tnum font-medium ${value ? "text-ink" : "text-ink-3"}`}>{fmt(value)}</span>
      </span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-[color:var(--ink)]" />
    </label>
  );
}

type Row = { label: string; get: (p: Payload) => number; f: (v: number) => string; better: "high" | "low" | null; pp?: boolean };
const ROWS: Row[] = [
  { label: "Expected revenue", get: (p) => p.kpis.revenue_inr.mean, f: (v) => inr(v), better: "high" },
  { label: "Expected contribution", get: (p) => p.kpis.contribution_inr.mean, f: (v) => inr(v), better: "high" },
  { label: "B2B fill rate", get: (p) => p.kpis.b2b_fill_rate.mean, f: (v) => pct(v, 2), better: "high", pp: true },
  { label: "D2C fill rate", get: (p) => p.kpis.d2c_fill_rate.mean, f: (v) => pct(v, 2), better: "high", pp: true },
  { label: "Chance any B2B account is short", get: (p) => p.kpis.p_any_b2b_shortfall, f: (v) => pct(v, 1), better: "low", pp: true },
  { label: "Unmet in plan", get: (p) => p.kpis.unmet_kg, f: (v) => kg(v), better: "low" },
  { label: "Expected waste", get: (p) => p.kpis.waste_kg.mean, f: (v) => kg(v), better: "low" },
  { label: "Co-man requested", get: (p) => p.kpis.coman_requested_kg, f: (v) => kg(v), better: null },
  { label: "Marketing spend", get: (p) => p.kpis.spend_recommended_inr, f: (v) => inr(v), better: null },
  { label: "Forecast demand", get: (p) => p.kpis.demand_kg, f: (v) => kg(v), better: null },
];

function ResultView({ res, onOpen }: { res: { r: ScenarioResult; p: Payload; base: Payload }; onOpen: () => void }) {
  const { r, p, base } = res;
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          Scenario vs. base plan <Badge>{modeLabel(r.mode)}</Badge>
          <span className="text-[12px] font-normal text-ink-3">solved in {r.seconds.toFixed(1)} s</span>
        </span>
      }
      subtitle={r.changes.length ? r.changes.join(" · ") : "No lever changes (strategy only)"}
      right={
        <button onClick={onOpen} className="rounded-md border border-hair px-2.5 py-1 text-[12.5px] hover:bg-surface-2">
          Open in all screens →
        </button>
      }
    >
      <table className="dtable tnum text-[13px]">
        <thead>
          <tr>
            <th />
            <th className="r">Base ({modeLabel(base.run.mode)})</th>
            <th className="r">Scenario</th>
            <th className="r">Change</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map((row) => {
            const a = row.get(base);
            const b = row.get(p);
            const d = b - a;
            const tiny = Math.abs(d) <= Math.max(Math.abs(a), 1) * 1e-4;
            const good = row.better == null || tiny ? null : (d > 0) === (row.better === "high");
            return (
              <tr key={row.label}>
                <td className="text-ink-2">{row.label}</td>
                <td className="r text-ink-2">{row.f(a)}</td>
                <td className="r font-medium">
                  <span className="trace" tabIndex={0} data-trace={`${r.run_id}\nkpis · ${row.label}`}>
                    {row.f(b)}
                  </span>
                </td>
                <td className={`r ${good == null ? "text-ink-3" : good ? "text-good" : "text-critical"}`}>
                  {tiny ? "—" : `${d > 0 ? "+" : "−"}${row.pp ? `${(100 * Math.abs(d)).toFixed(2)} pp` : row.f(Math.abs(d))}`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="mt-4">
        <div className="mb-2 text-[12px] font-medium uppercase tracking-[0.06em] text-ink-3">Alerts in this scenario</div>
        {p.risk.alerts.length === 0 ? (
          <StatusPill tone="good">No shortfall or surplus alerts</StatusPill>
        ) : (
          <ul className="grid gap-1.5 text-[13px]">
            {p.risk.alerts.map((a, i) => (
              <li key={i} className="flex items-center gap-2">
                <StatusIcon tone={a.kind === "breach" ? "critical" : "warning"} />
                {a.kind === "breach" ? "Shortfall" : "Surplus"} from {shortDay(a.start)} (in {a.weeks_until} wk): peak {pct(a.peak_probability, 0)},
                expected {kg(a.expected_kg)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

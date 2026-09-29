"use client";
// 08 Data & model health: can the forecasts be trusted? Validation, backtests, calibration,
// anomalies, and the mode-invariance check (PRD §8.8, US7, FR-7–10).
import { RunGate } from "@/components/RunGate";
import { Badge, Card, CellBar, Num, PageHeader, Stat, StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import { day, modeLabel, num, pct, regionName, shortHash } from "@/lib/format";
import { useSelection } from "@/lib/selection";
import type { Payload } from "@/lib/types";
import { useAsync } from "@/lib/useAsync";

export default function HealthPage() {
  return <RunGate>{(p) => <Health p={p} />}</RunGate>;
}

const MODEL_LABEL: Record<string, string> = {
  lightgbm: "LightGBM quantile",
  auto_ets: "AutoETS",
  auto_theta: "AutoTheta",
  seasonal_naive_52: "Seasonal naive (52 wk)",
  window_average_8: "Window average (8 wk)",
};

function series(sid: string) {
  const [, reg, sku] = sid.split("|");
  return `${regionName(reg)} · ${sku}`;
}

function Health({ p }: { p: Payload }) {
  const h = p.health;
  const v = h.validation;
  const cov = h.coverage_mean_calibrated;
  const covOk = cov != null && cov >= 0.7 && cov <= 0.9;
  const models = Object.entries(h.model_counts).sort((a, b) => b[1] - a[1]);
  const maxM = Math.max(...models.map((m) => m[1]), 1);
  const cmap = new Map(h.coverage.map((c) => [c.series_id, c]));
  return (
    <div className="grid gap-5">
      <PageHeader
        title="Data & model health"
        lede="How far to trust the forecasts before trusting the plan. Accuracy is measured by rolling-origin backtests on history; a model is used only if it beats the seasonal-naive baseline."
      />
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Stat
          label="Data validation"
          value={v ? (v.ok ? "Passed" : "Failed") : "—"}
          sub={v ? `${v.n_errors} errors · ${v.n_warnings} warnings · contract ${v.contract_version}` : undefined}
          src="health.validation.ok"
          tone={v?.ok ? "good" : "critical"}
        />
        <Stat
          label="Series beating the baseline"
          value={pct(h.share_beating_baseline, 0)}
          sub="target ≥ 70% (MASE vs. seasonal naive)"
          src="health.share_beating_baseline"
          tone={h.share_beating_baseline != null && h.share_beating_baseline >= 0.7 ? "good" : "warning"}
        />
        <Stat
          label="P10–P90 coverage (calibrated)"
          value={pct(cov, 0)}
          sub={`target 70–90% · raw ${pct(h.coverage_mean_raw, 0)}`}
          src="health.coverage_mean_calibrated"
          tone={covOk ? "good" : "warning"}
        />
        <Stat
          label="Anomalies flagged"
          value={String(h.anomalies.length)}
          sub="winsorized before training; never extrapolated"
          src="health.anomalies"
        />
      </div>
      <div className="grid gap-5 xl:grid-cols-[1fr_1.4fr]">
        <div className="grid content-start gap-5">
          <Card title="Model selected per series" subtitle="Chosen by lowest pinball loss among models that beat the baseline.">
            <div className="grid gap-2">
              {models.map(([m, n]) => (
                <div key={m} className="grid grid-cols-[160px_1fr_32px] items-center gap-3 text-[13px]">
                  <span className="text-ink-2">{MODEL_LABEL[m] ?? m}</span>
                  <CellBar value={n} max={maxM} />
                  <span className="text-right tnum">{n}</span>
                </div>
              ))}
            </div>
          </Card>
          <ModeInvariance p={p} />
          <Card title="Validation report" subtitle={v?.history ? `History ${day(v.history.first_week)} – ${day(v.history.last_week)} (${v.history.n_weeks} weeks)` : undefined}>
            {v && v.issues.length > 0 ? (
              <ul className="grid gap-2 text-[12.5px]">
                {v.issues.map((i, k) => (
                  <li key={k} className="flex items-start gap-2">
                    <span className="shrink-0">
                      <StatusPill tone={i.severity === "error" ? "critical" : "warning"}>{i.severity}</StatusPill>
                    </span>
                    <span className="text-ink-2">
                      <span className="font-mono text-[11.5px]">{i.table}</span> {i.message}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <StatusPill tone="good">No issues</StatusPill>
            )}
          </Card>
        </div>
        <Card title="Backtest by series" subtitle="MASE below 1.0 beats the naive forecast's in-sample error; lower is better.">
          <div className="max-h-[640px] overflow-auto">
            <table className="dtable tnum text-[12.5px]">
              <thead className="sticky top-0 bg-surface">
                <tr>
                  <th>Series</th>
                  <th>Model</th>
                  <th className="r">MASE</th>
                  <th className="r">Baseline MASE</th>
                  <th className="r">Coverage raw → cal.</th>
                </tr>
              </thead>
              <tbody>
                {h.selection.map((s) => {
                  const c = cmap.get(s.series_id);
                  return (
                    <tr key={s.series_id}>
                      <td>{series(s.series_id)}</td>
                      <td className="text-ink-2">{MODEL_LABEL[s.model] ?? s.model}</td>
                      <td className="r">
                        <Num src={`health.selection[${s.series_id}].mase`}>{num(s.mase, 3)}</Num>
                      </td>
                      <td className="r text-ink-2">{num(s.baseline_mase, 3)}</td>
                      <td className="r">
                        {c ? (
                          <>
                            <span className="text-ink-3">{pct(c.coverage_raw, 0)}</span> → {pct(c.coverage_calibrated, 0)}
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
      {h.anomalies.length > 0 && (
        <Card title="Flagged demand spikes" subtitle="Robust z-score above threshold (e.g. viral content). Cleaned value used for training.">
          <div className="max-h-72 overflow-auto">
            <table className="dtable tnum text-[12.5px]">
              <thead className="sticky top-0 bg-surface">
                <tr>
                  <th>Week</th>
                  <th>Series</th>
                  <th className="r">Observed</th>
                  <th className="r">Baseline</th>
                  <th className="r">Robust z</th>
                  <th className="r">Used for training</th>
                </tr>
              </thead>
              <tbody>
                {[...h.anomalies]
                  .sort((a, b) => b.robust_z - a.robust_z)
                  .map((a, i) => (
                    <tr key={i}>
                      <td>{day(a.week_start)}</td>
                      <td>{series(a.series_id)}</td>
                      <td className="r">{num(a.y, 0)} kg</td>
                      <td className="r text-ink-2">{num(a.baseline, 0)} kg</td>
                      <td className="r">{num(a.robust_z, 1)}</td>
                      <td className="r">{num(a.y_clean, 0)} kg</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function ModeInvariance({ p }: { p: Payload }) {
  const { world } = useSelection();
  const { data } = useAsync(world ? `inv:${world}:${p.run.run_id}` : null, () => api.compare(world!));
  if (!data) return null;
  const hashes = new Set(data.modes.map((m) => m.forecast_hash));
  return (
    <Card title="Strategy never touches the forecast" subtitle="Forecast artifact hash for each mode's run on this world.">
      <ul className="grid gap-1.5 text-[13px]">
        {data.modes.map((m) => (
          <li key={m.mode} className="flex items-center justify-between">
            <span>{modeLabel(m.mode)}</span>
            <Badge mono>{shortHash(m.forecast_hash, 16)}</Badge>
          </li>
        ))}
      </ul>
      <div className="mt-3">
        {hashes.size === 1 ? (
          <StatusPill tone="good">Identical across {data.modes.length} modes</StatusPill>
        ) : (
          <StatusPill tone="critical">Forecast differs across modes</StatusPill>
        )}
      </div>
    </Card>
  );
}

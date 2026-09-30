"use client";
// Weekly history + forecast fan (P10–P90 band, P50 line) for one or more series on ONE kg axis.
// Crosshair + tooltip on hover (every series at that week), direct end labels, alert spans.
import { useMemo, useState } from "react";
import { kgFull, shortDay } from "@/lib/format";
import { compactKg, niceTicks, useWidth } from "./useWidth";

export interface FanSeries {
  key: string;
  label: string;
  color: string; // line color (CSS value)
  band?: string; // band fill (CSS value)
  history?: (number | null)[]; // aligned to `history` dates
  forecast?: { q10: number[]; q50: number[]; q90: number[] }; // aligned to `horizon` dates
}

export interface Span {
  start: string;
  end: string;
  tone: "critical" | "warning";
  label: string;
}

const M = { top: 28, right: 92, bottom: 30, left: 52 };

export function FanChart({
  history,
  horizon,
  series,
  spans = [],
  height = 320,
  ariaLabel,
}: {
  history: string[];
  horizon: string[];
  series: FanSeries[];
  spans?: Span[];
  height?: number;
  ariaLabel: string;
}) {
  const { ref, width } = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const weeks = useMemo(() => [...history, ...horizon], [history, horizon]);
  const H0 = history.length;
  const W = width - M.left - M.right;
  const Hh = height - M.top - M.bottom;

  const ymax = useMemo(() => {
    let m = 0;
    for (const s of series) {
      for (const v of s.history ?? []) if (v != null) m = Math.max(m, v);
      for (const v of s.forecast?.q90 ?? []) m = Math.max(m, v);
    }
    return m;
  }, [series]);
  const ticks = niceTicks(ymax * 1.05, 4);
  const top = ticks[ticks.length - 1];
  const x = (i: number) => M.left + (weeks.length <= 1 ? 0 : (i / (weeks.length - 1)) * W);
  const y = (v: number) => M.top + Hh - (v / top) * Hh;

  const path = (pts: number[][]) =>
    pts.map(([a, b], i) => `${i ? "L" : "M"}${a.toFixed(1)},${b.toFixed(1)}`).join("");

  // month ticks along x
  const monthTicks = weeks
    .map((w, i) => ({ w, i }))
    .filter(({ w, i }) => i === 0 || w.slice(5, 7) !== weeks[i - 1].slice(5, 7))
    .filter((_, k, arr) => arr.length <= 10 || k % Math.ceil(arr.length / 10) === 0);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    const i = Math.round(((px - M.left) / W) * (weeks.length - 1));
    setHover(i >= 0 && i < weeks.length ? i : null);
  };

  const valueAt = (s: FanSeries, i: number) => {
    if (i < H0) return s.history?.[i] != null ? { v: s.history[i] as number } : null;
    const j = i - H0;
    if (!s.forecast) return null;
    return { v: s.forecast.q50[j], lo: s.forecast.q10[j], hi: s.forecast.q90[j] };
  };

  // end labels, nudged apart only slightly; converging series fall back to legend + tooltip
  const ends = series
    .map((s) => {
      const last = s.forecast ? s.forecast.q50[s.forecast.q50.length - 1] : s.history?.[s.history.length - 1];
      return { s, yy: last != null ? y(last) : null };
    })
    .filter((e) => e.yy != null) as { s: FanSeries; yy: number }[];
  ends.sort((a, b) => a.yy - b.yy);
  for (let k = 1; k < ends.length; k++) if (ends[k].yy - ends[k - 1].yy < 14) ends[k].yy = ends[k - 1].yy + 14;

  const tip = hover != null ? { i: hover, left: x(hover) } : null;

  return (
    <div ref={ref} className="relative w-full select-none">
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={ariaLabel}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {/* gridlines + y ticks */}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={M.left + W} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--axis)" : "var(--grid)"} />
            <text x={M.left - 8} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--ink-3)" className="tnum">
              {compactKg(t)}
            </text>
          </g>
        ))}
        {/* alert spans */}
        {spans.map((sp, si) => {
          const a = weeks.indexOf(sp.start);
          const b = weeks.indexOf(sp.end);
          if (a < 0 || b < 0) return null;
          const half = W / Math.max(1, weeks.length - 1) / 2;
          return (
            <g key={`${sp.tone}${sp.start}`}>
              <rect
                x={x(a) - half}
                y={M.top}
                width={x(b) - x(a) + 2 * half}
                height={Hh}
                fill={sp.tone === "critical" ? "var(--critical-wash)" : "var(--warning-wash)"}
              />
              <text x={x(a) - half + 4} y={M.top + 12 + (si % 3) * 13} fontSize={11} fontWeight={500} fill={sp.tone === "critical" ? "var(--critical-text)" : "var(--warning-text)"}>
                {sp.label}
              </text>
            </g>
          );
        })}
        {/* now divider */}
        {H0 > 0 && horizon.length > 0 && (
          <g>
            <line x1={(x(H0 - 1) + x(H0)) / 2} x2={(x(H0 - 1) + x(H0)) / 2} y1={M.top - 14} y2={M.top + Hh} stroke="var(--axis)" />
            <text x={(x(H0 - 1) + x(H0)) / 2 + 6} y={M.top - 4} fontSize={11} fill="var(--ink-2)">
              Forecast →
            </text>
            <text x={(x(H0 - 1) + x(H0)) / 2 - 6} y={M.top - 4} fontSize={11} fill="var(--ink-3)" textAnchor="end">
              ← Actual
            </text>
          </g>
        )}
        {/* bands */}
        {series.map(
          (s) =>
            s.forecast &&
            s.band && (
              <path
                key={`band-${s.key}`}
                d={
                  path(s.forecast.q90.map((v, j) => [x(H0 + j), y(v)])) +
                  path(s.forecast.q10.map((v, j) => [x(H0 + j), y(v)]).reverse()).replace("M", "L") +
                  "Z"
                }
                fill={s.band}
              />
            ),
        )}
        {/* lines */}
        {series.map((s) => {
          const hist = (s.history ?? []).map((v, i) => [i, v] as const).filter(([, v]) => v != null) as [number, number][];
          const fc = s.forecast?.q50.map((v, j) => [H0 + j, v] as [number, number]) ?? [];
          const joined = hist.length && fc.length ? [...hist.slice(-1), ...fc] : fc;
          return (
            <g key={`line-${s.key}`} fill="none" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round">
              {hist.length > 1 && <path d={path(hist.map(([i, v]) => [x(i), y(v)] as [number, number]))} stroke={s.color} opacity={0.75} />}
              {joined.length > 1 && <path d={path(joined.map(([i, v]) => [x(i), y(v)] as [number, number]))} stroke={s.color} />}
            </g>
          );
        })}
        {/* x axis months */}
        {monthTicks.map(({ w, i }) => (
          <text key={w} x={x(i)} y={height - 10} fontSize={11} fill="var(--ink-3)" textAnchor="middle">
            {shortDay(w).split(" ")[1]} {w.slice(2, 4)}
          </text>
        ))}
        {/* end labels */}
        {ends.map(({ s, yy }) => (
          <g key={`end-${s.key}`}>
            <circle cx={M.left + W} cy={y(valueAt(s, weeks.length - 1)?.v ?? 0)} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
            <text x={M.left + W + 10} y={yy + 4} fontSize={12} fill="var(--ink-2)">
              {s.label}
            </text>
          </g>
        ))}
        {/* crosshair */}
        {tip && (
          <g pointerEvents="none">
            <line x1={tip.left} x2={tip.left} y1={M.top} y2={M.top + Hh} stroke="var(--ink-3)" />
            {series.map((s) => {
              const v = valueAt(s, tip.i);
              return v ? (
                <circle key={s.key} cx={tip.left} cy={y(v.v)} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              ) : null;
            })}
          </g>
        )}
      </svg>
      {tip && (
        <div
          className="pointer-events-none absolute top-2 z-10 min-w-48 rounded-md border border-hair bg-surface px-3 py-2 shadow-lg"
          style={{
            left: Math.min(Math.max(tip.left + 12, 0), width - 220),
          }}
        >
          <div className="mb-1 text-[12px] font-medium text-ink-2">
            Week of {shortDay(weeks[tip.i])} {weeks[tip.i].slice(0, 4)} · {tip.i < H0 ? "actual" : "forecast"}
          </div>
          {series.map((s) => {
            const v = valueAt(s, tip.i);
            if (!v) return null;
            return (
              <div key={s.key} className="flex items-center justify-between gap-4 text-[12px]">
                <span className="flex items-center gap-1.5 text-ink-2">
                  <span className="inline-block h-[2px] w-3" style={{ background: s.color }} />
                  {s.label}
                </span>
                <span className="tnum">
                  <strong className="font-semibold text-ink">{kgFull(v.v)}</strong>
                  {v.lo != null && (
                    <span className="ml-1 text-ink-3">
                      ({kgFull(v.lo).replace(" kg", "")}–{kgFull(v.hi)})
                    </span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

"use client";
// Weekly breach / surplus probability (0–100%, one axis) with the mode's threshold as a reference line.
// Status colors here mean status (shortfall = critical, surplus = warning); bars hover individually.
import { useState } from "react";
import { pct, shortDay } from "@/lib/format";
import { useWidth } from "./useWidth";

const M = { top: 16, right: 16, bottom: 28, left: 40 };

export function ProbChart({
  weeks,
  breach,
  surplus,
  threshold,
  surplusThreshold,
  height = 200,
}: {
  weeks: string[];
  breach: number[];
  surplus: number[];
  threshold: number;
  surplusThreshold: number;
  height?: number;
}) {
  const { ref, width } = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const W = width - M.left - M.right;
  const Hh = height - M.top - M.bottom;
  const slot = W / weeks.length;
  const bw = Math.min(12, (slot - 6) / 2);
  const y = (v: number) => M.top + Hh - v * Hh;
  const bar = (x0: number, v: number, fill: string, key: string) => {
    const h = Math.max(0, Hh * v);
    if (h < 0.5) return null;
    const r = Math.min(3, h);
    const yy = y(v);
    return (
      <path
        key={key}
        d={`M${x0},${yy + h}V${yy + r}Q${x0},${yy} ${x0 + r},${yy}H${x0 + bw - r}Q${x0 + bw},${yy} ${x0 + bw},${yy + r}V${yy + h}Z`}
        fill={fill}
      />
    );
  };
  return (
    <div ref={ref} className="relative w-full select-none">
      <svg width={width} height={height} role="img" aria-label="Weekly probability of shortfall and surplus">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={M.left} x2={M.left + W} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--axis)" : "var(--grid)"} />
            <text x={M.left - 6} y={y(t) + 4} fontSize={11} textAnchor="end" fill="var(--ink-3)" className="tnum">
              {t * 100}%
            </text>
          </g>
        ))}
        {weeks.map((w, i) => {
          const cx = M.left + slot * i + slot / 2;
          return (
            <g key={w} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={M.left + slot * i} y={M.top} width={slot} height={Hh} fill={hover === i ? "var(--surface-2)" : "transparent"} />
              {bar(cx - bw - 1, breach[i], "var(--critical)", "b")}
              {bar(cx + 1, surplus[i], "var(--warning)", "s")}
              {(i % Math.ceil(weeks.length / 7) === 0 || i === weeks.length - 1) && (
                <text x={cx} y={height - 8} fontSize={11} textAnchor="middle" fill="var(--ink-3)">
                  {shortDay(w)}
                </text>
              )}
            </g>
          );
        })}
        <line x1={M.left} x2={M.left + W} y1={y(threshold)} y2={y(threshold)} stroke="var(--critical-text)" strokeWidth={1} />
        <text x={M.left + W} y={y(threshold) - 4} fontSize={11} textAnchor="end" fill="var(--critical-text)">
          shortfall alert at {pct(threshold, 0)}
        </text>
        {Math.abs(surplusThreshold - threshold) > 0.04 && (
          <>
            <line x1={M.left} x2={M.left + W} y1={y(surplusThreshold)} y2={y(surplusThreshold)} stroke="var(--warning-text)" strokeWidth={1} />
            <text x={M.left + W} y={y(surplusThreshold) - 4} fontSize={11} textAnchor="end" fill="var(--warning-text)">
              surplus alert at {pct(surplusThreshold, 0)}
            </text>
          </>
        )}
      </svg>
      {hover != null && (
        <div
          className="pointer-events-none absolute top-0 z-10 rounded-md border border-hair bg-surface px-3 py-2 text-[12px] shadow-lg"
          style={{ left: Math.min(M.left + slot * hover + slot, width - 200) }}
        >
          <div className="mb-1 font-medium text-ink-2">Week of {shortDay(weeks[hover])}</div>
          <div className="flex justify-between gap-4">
            <span className="flex items-center gap-1.5 text-ink-2">
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: "var(--critical)" }} />
              P(shortfall)
            </span>
            <strong className="tnum">{pct(breach[hover])}</strong>
          </div>
          <div className="flex justify-between gap-4">
            <span className="flex items-center gap-1.5 text-ink-2">
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: "var(--warning)" }} />
              P(surplus)
            </span>
            <strong className="tnum">{pct(surplus[hover])}</strong>
          </div>
        </div>
      )}
    </div>
  );
}

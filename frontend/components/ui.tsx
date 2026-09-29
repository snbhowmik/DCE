"use client";
// Small UI kit: cards, stat tiles, traceable numbers, status pills, empty states.
import type { ReactNode } from "react";
import { useSelection } from "@/lib/selection";

/** A number with its provenance: hover shows run_id and the payload field it came from. */
export function Num({
  children,
  src,
  className = "",
}: {
  children: ReactNode;
  src: string;
  className?: string;
}) {
  const { runId } = useSelection();
  return (
    <span className={`trace ${className}`} tabIndex={0} data-trace={`${runId ?? "no run"}\n${src}`}>
      {children}
    </span>
  );
}

export function Card({
  title,
  subtitle,
  right,
  children,
  className = "",
  pad = true,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  pad?: boolean;
}) {
  return (
    <section
      className={`fade-up rounded-lg border border-hair bg-surface ${pad ? "p-5" : ""} ${className}`}
    >
      {(title || right) && (
        <header className={`mb-4 flex items-start justify-between gap-4 ${pad ? "" : "px-5 pt-5"}`}>
          <div>
            {title && <h2 className="text-[15px] font-semibold tracking-[-0.005em]">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-[13px] text-ink-2">{subtitle}</p>}
          </div>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  src,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  src: string;
  tone?: "good" | "critical" | "warning";
}) {
  const color =
    tone === "good" ? "text-good" : tone === "critical" ? "text-critical" : tone === "warning" ? "text-warning" : "";
  return (
    <div className="fade-up rounded-lg border border-hair bg-surface px-4 py-3.5">
      <div className="text-[12.5px] text-ink-2">{label}</div>
      <div className={`mt-1 text-[26px] font-semibold leading-tight tracking-[-0.02em] ${color}`}>
        <Num src={src}>{value}</Num>
      </div>
      {sub && <div className="mt-1 text-[12px] text-ink-3">{sub}</div>}
    </div>
  );
}

const STATUS = {
  critical: { color: "var(--critical)", text: "text-critical", wash: "var(--critical-wash)" },
  warning: { color: "var(--warning)", text: "text-warning", wash: "var(--warning-wash)" },
  good: { color: "var(--good)", text: "text-good", wash: "transparent" },
  neutral: { color: "var(--ink-3)", text: "text-ink-2", wash: "var(--surface-2)" },
} as const;

export type Tone = keyof typeof STATUS;

/** Status is never color alone: icon + label. */
export function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  const s = STATUS[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[12px] font-medium ${s.text}`}
      style={{ background: s.wash }}
    >
      <StatusIcon tone={tone} />
      {children}
    </span>
  );
}

export function StatusIcon({ tone, size = 12 }: { tone: Tone; size?: number }) {
  const c = STATUS[tone].color;
  if (tone === "critical")
    return (
      <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden>
        <path d="M6 1 L11 10.5 H1 Z" fill={c} />
        <rect x="5.4" y="4.2" width="1.2" height="3.4" rx=".6" fill="var(--surface)" />
        <circle cx="6" cy="9" r=".7" fill="var(--surface)" />
      </svg>
    );
  if (tone === "warning")
    return (
      <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden>
        <circle cx="6" cy="6" r="5" fill={c} />
        <rect x="5.4" y="3" width="1.2" height="3.8" rx=".6" fill="var(--surface)" />
        <circle cx="6" cy="8.6" r=".7" fill="var(--surface)" />
      </svg>
    );
  if (tone === "good")
    return (
      <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden>
        <circle cx="6" cy="6" r="5" fill={c} />
        <path d="M3.6 6.2 5.3 7.8 8.4 4.5" stroke="var(--surface)" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden>
      <circle cx="6" cy="6" r="4.5" fill="none" stroke={c} strokeWidth="1.4" />
    </svg>
  );
}

export function Badge({ children, mono = false }: { children: ReactNode; mono?: boolean }) {
  return (
    <span
      className={`inline-flex items-center rounded border border-hair bg-surface-2 px-1.5 py-px text-[11.5px] text-ink-2 ${mono ? "font-mono" : ""}`}
    >
      {children}
    </span>
  );
}

/** Honest placeholder for a screen whose backend phase has not landed yet (D-051). */
export function Upcoming({ phase, title, children }: { phase: string; title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-[color:var(--axis)] bg-surface px-5 py-5">
      <div className="flex items-center gap-2 text-[12px] font-medium uppercase tracking-[0.08em] text-ink-3">
        <span className="inline-block h-2 w-2 rounded-full bg-[color:var(--axis)]" />
        {phase}
      </div>
      <div className="mt-2 text-[15px] font-semibold">{title}</div>
      <div className="mt-1 max-w-3xl text-[13px] text-ink-2">{children}</div>
    </div>
  );
}

export function PageHeader({ title, lede, right }: { title: string; lede?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="font-display text-[28px] font-black leading-none tracking-[-0.03em]">{title}</h1>
        {lede && <p className="mt-2 max-w-3xl text-[14px] text-ink-2">{lede}</p>}
      </div>
      {right}
    </div>
  );
}

export function Skeleton({ h = 120, className = "" }: { h?: number; className?: string }) {
  return <div className={`skeleton ${className}`} style={{ height: h }} />;
}

export function LegendKey({ color, label, kind = "line" }: { color: string; label: string; kind?: "line" | "band" | "dot" }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-2">
      {kind === "line" && <span className="inline-block h-[2px] w-4 rounded" style={{ background: color }} />}
      {kind === "band" && <span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: color }} />}
      {kind === "dot" && <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />}
      {label}
    </span>
  );
}

/** Inline magnitude bar for table cells (single series → one color). */
export function CellBar({ value, max, color = "var(--s-d2c)" }: { value: number; max: number; color?: string }) {
  const w = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <div className="h-2 w-full min-w-16 rounded-sm bg-surface-2">
      <div className="h-2 rounded-r-[3px]" style={{ width: `${w * 100}%`, background: color }} />
    </div>
  );
}

/** Diverging bar around 0 for z-scores: blue positive, red negative, gray midline. */
export function ZBar({ z, max = 2, width = 96 }: { z: number | null; max?: number; width?: number }) {
  if (z == null || !Number.isFinite(z)) return <span className="text-ink-3">—</span>;
  const w = Math.min(1, Math.abs(z) / max) * 50;
  return (
    <div className="relative h-2 rounded-sm bg-surface-2" style={{ width }}>
      <div className="absolute left-1/2 top-[-2px] h-3 w-px bg-[color:var(--axis)]" />
      <div
        className="absolute top-0 h-2"
        style={{
          left: z >= 0 ? "50%" : `${50 - w}%`,
          width: `${w}%`,
          background: z >= 0 ? "var(--div-pos)" : "var(--div-neg)",
          borderRadius: z >= 0 ? "0 3px 3px 0" : "3px 0 0 3px",
        }}
      />
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-hair bg-surface p-5">
      <StatusPill tone="critical">Couldn&apos;t load</StatusPill>
      <p className="mt-2 text-[13px] text-ink-2">{message}</p>
      <p className="mt-1 text-[13px] text-ink-3">
        Is the API running? <code className="font-mono">make run-api</code> starts it on port 8000.
      </p>
    </div>
  );
}

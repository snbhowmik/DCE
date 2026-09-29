// Number formatting shared by every screen. Mirrors backend/dce/ai/narrative.py so the brief and
// the UI write the same figure the same way.

export function inr(x: number | null | undefined, compact = true): string {
  if (x == null || !Number.isFinite(x)) return "—";
  const sign = x < 0 ? "−" : "";
  const a = Math.abs(x);
  if (!compact) return `${sign}₹${Math.round(a).toLocaleString("en-IN")}`;
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(1)} L`;
  return `${sign}₹${Math.round(a).toLocaleString("en-IN")}`;
}

export function kg(x: number | null | undefined): string {
  if (x == null || !Number.isFinite(x)) return "—";
  return Math.abs(x) >= 1000 ? `${(x / 1000).toFixed(1)} t` : `${Math.round(x).toLocaleString("en-IN")} kg`;
}

export function kgFull(x: number | null | undefined): string {
  if (x == null || !Number.isFinite(x)) return "—";
  return `${Math.round(x).toLocaleString("en-IN")} kg`;
}

export function pct(x: number | null | undefined, digits = 1): string {
  if (x == null || !Number.isFinite(x)) return "—";
  return `${(100 * x).toFixed(digits)}%`;
}

export function num(x: number | null | undefined, digits = 2): string {
  if (x == null || !Number.isFinite(x)) return "—";
  return x.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

export function shortDay(iso: string): string {
  const [, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
}

export function monthLabel(iso: string): string {
  const [y, m] = iso.slice(0, 10).split("-").map(Number);
  return `${MONTHS[m - 1]} ${y}`;
}

export function regionName(id: string | null | undefined): string {
  if (!id) return "—";
  return id
    .replace(/^reg_/, "")
    .split("_")
    .map((w) => (w.length <= 3 && w === "ncr" ? "NCR" : w[0].toUpperCase() + w.slice(1)))
    .join(" ");
}

export function modeLabel(m: string): string {
  return m === "D2C_EXPANSION" ? "D2C expansion" : m[0] + m.slice(1).toLowerCase();
}

export function shortHash(h: string | null | undefined, n = 8): string {
  return h ? h.slice(0, n) : "—";
}

/** Fixed decimals without a "-0.00". */
export function fixed(x: number | null | undefined, digits = 2): string {
  if (x == null || !Number.isFinite(x)) return "—";
  const r = Number(x.toFixed(digits));
  return (Object.is(r, -0) ? 0 : r).toFixed(digits);
}

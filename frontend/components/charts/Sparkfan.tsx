"use client";
// Tiny forecast fan (band + median) for table rows and scorecards. Decorative summary: the numbers
// it shows are also printed next to it.

export function Sparkfan({
  q10,
  q50,
  q90,
  color = "var(--s-d2c)",
  band = "rgba(42,120,214,0.14)",
  width = 120,
  height = 32,
}: {
  q10: number[];
  q50: number[];
  q90: number[];
  color?: string;
  band?: string;
  width?: number;
  height?: number;
}) {
  const n = q50.length;
  if (!n) return null;
  const max = Math.max(...q90, 1);
  const x = (i: number) => (n === 1 ? 0 : (i / (n - 1)) * (width - 4)) + 2;
  const y = (v: number) => height - 2 - (v / max) * (height - 4);
  const line = q50.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
  const area =
    q90.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("") +
    q10
      .map((v, i) => [i, v] as const)
      .reverse()
      .map(([i, v]) => `L${x(i)},${y(v)}`)
      .join("") +
    "Z";
  return (
    <svg width={width} height={height} aria-hidden>
      <path d={area} fill={band} />
      <path d={line} stroke={color} strokeWidth={2} fill="none" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

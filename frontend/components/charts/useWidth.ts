"use client";
import { useEffect, useRef, useState } from "react";

/** Width of a container, tracked with ResizeObserver (charts render at their real pixel width). */
export function useWidth<T extends HTMLElement>(initial = 800) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(240, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, width };
}

/** Clean axis ticks: 0 and multiples of 1/2/2.5/5 × 10^k, about `n` of them. */
export function niceTicks(max: number, n = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / n;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  const out = [];
  for (let v = 0; v <= max * 1.0001 + step * 0.5 && out.length < 12; v += step) out.push(v);
  if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
  return out;
}

export function compactKg(v: number): string {
  if (v >= 1000) return `${(v / 1000).toFixed(v % 1000 === 0 ? 0 : 1)} t`;
  return `${Math.round(v)} kg`;
}

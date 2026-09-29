"use client";
// Renders children only once the selected run's payload is loaded; otherwise skeleton / error / empty.
import type { Payload } from "@/lib/types";
import { useSelection } from "@/lib/selection";
import { ErrorState, Skeleton } from "./ui";

export function RunGate({ children }: { children: (p: Payload) => React.ReactNode }) {
  const { payload, loading, error, world, mode, datasets } = useSelection();
  if (error) return <ErrorState message={error} />;
  if (loading || datasets == null)
    return (
      <div className="grid gap-4">
        <Skeleton h={36} className="w-72" />
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} h={88} />
          ))}
        </div>
        <Skeleton h={360} />
      </div>
    );
  if (!payload)
    return (
      <div className="rounded-lg border border-hair bg-surface p-6 text-[14px] text-ink-2">
        No run yet for <span className="font-mono">{world}</span> in {mode}. Use <strong>Re-run world</strong> above, or{" "}
        <code className="font-mono">uv run dce precompute --world {world}</code>.
      </div>
    );
  return <>{children(payload)}</>;
}

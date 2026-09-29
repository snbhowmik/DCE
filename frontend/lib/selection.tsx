"use client";
// Which world and strategy mode the dashboard is looking at, and the run payload for that pair.
// Persisted per browser (localStorage) so a reload keeps the view; every screen reads from here.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { DatasetRow, Mode, Payload } from "./types";

interface Selection {
  datasets: DatasetRow[] | null;
  world: string | null;
  mode: Mode;
  setWorld: (w: string) => void;
  setMode: (m: Mode) => void;
  runId: string | null;
  payload: Payload | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

const Ctx = createContext<Selection | null>(null);
const KEY = "dce.selection.v1";

function readStored(): { world?: string; mode?: Mode } {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function SelectionProvider({ children }: { children: React.ReactNode }) {
  const [datasets, setDatasets] = useState<DatasetRow[] | null>(null);
  const [world, setWorldState] = useState<string | null>(null);
  const [mode, setModeState] = useState<Mode>("STABILITY");
  const [payload, setPayload] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const ds = await api.datasets();
      setDatasets(ds);
      setError(null);
      const stored = readStored();
      setWorldState((cur) => {
        const pick = cur ?? stored.world;
        const withRuns = ds.filter((d) => d.runs.length);
        if (pick && ds.some((d) => d.world_id === pick)) return pick;
        return (withRuns[withRuns.length - 1] ?? ds[0])?.world_id ?? null;
      });
      if (stored.mode) setModeState(stored.mode);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void refresh();
  }, [refresh]);

  const runId = useMemo(() => {
    const d = datasets?.find((x) => x.world_id === world);
    return d?.runs.find((r) => r.mode === mode)?.run_id ?? null;
  }, [datasets, world, mode]);

  useEffect(() => {
    let live = true;
    if (!runId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- derived from selection
      setPayload(null);
      setLoading(datasets == null);
      return;
    }
    setLoading(true);
    api
      .payload(runId)
      .then((p) => live && (setPayload(p), setError(null)))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [runId, datasets]);

  const persist = (w: string | null, m: Mode) => {
    try {
      localStorage.setItem(KEY, JSON.stringify({ world: w, mode: m }));
    } catch {}
  };
  const setWorld = (w: string) => {
    setWorldState(w);
    persist(w, mode);
  };
  const setMode = (m: Mode) => {
    setModeState(m);
    persist(world, m);
  };

  return (
    <Ctx.Provider
      value={{ datasets, world, mode, setWorld, setMode, runId, payload, loading, error, refresh }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useSelection(): Selection {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSelection outside SelectionProvider");
  return v;
}

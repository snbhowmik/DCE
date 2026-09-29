"use client";
import { useEffect, useState } from "react";

/** Load something keyed by `key`; re-runs when the key changes. Null key = idle. */
export function useAsync<T>(key: string | null, load: () => Promise<T>) {
  const [state, setState] = useState<{ key: string | null; data: T | null; error: string | null }>({
    key: null,
    data: null,
    error: null,
  });
  useEffect(() => {
    if (!key) return;
    let live = true;
    load()
      .then((data) => live && setState({ key, data, error: null }))
      .catch((e) => live && setState({ key, data: null, error: e instanceof Error ? e.message : String(e) }));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `load` is keyed by `key`
  }, [key]);
  const fresh = state.key === key;
  return { data: fresh ? state.data : null, error: fresh ? state.error : null, loading: !!key && !fresh };
}

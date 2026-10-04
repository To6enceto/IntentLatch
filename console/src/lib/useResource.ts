import { useCallback, useEffect, useState, type DependencyList } from "react";
import { errorMessage } from "./api";

export type Resource<T> = {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => void;
  /** Applies a local change, such as a row the gateway just returned. */
  update: (change: (data: T) => T) => void;
};

/** Loads data when the dependencies change; keeps the previous data while reloading. */
export function useResource<T>(load: () => Promise<T>, deps: DependencyList): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    load().then(
      (value) => { if (active) { setData(value); setLoading(false); } },
      (reason: unknown) => { if (active) { setError(errorMessage(reason)); setLoading(false); } },
    );
    return () => { active = false; };
  }, [...deps, attempt]);

  const reload = useCallback(() => setAttempt((previous) => previous + 1), []);
  const update = useCallback((change: (data: T) => T) => {
    setData((current) => (current === undefined ? current : change(current)));
  }, []);

  return { data, error, loading, reload, update };
}

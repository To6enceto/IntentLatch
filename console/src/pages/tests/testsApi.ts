import { useEffect, useState } from "react";
import { api, errorMessage } from "../../lib/api";

// Mirrors gateway/src/intentlatch/testcases.py and runner.py.
export type Expected = "ALLOW" | "EDIT" | "BLOCK";
export type Actual = Expected | "ERROR";

export const PROMPT_MAX = 4000;
export const NAME_MAX = 128;
export const MUST_NOT_CONTAIN_MAX = 20;
export const ENTRY_MAX = 200;
/** The seeded test employee every predefined case runs as. */
export const TEST_EMPLOYEE_ID = "7e57ca5e-0000-4000-8000-000000000001";

export type TestCase = {
  code: string;
  name: string;
  prompt: string;
  model: string;
  run_as_employee_id: string;
  expected: Expected;
  expected_policy_code: string | null;
  must_not_contain: string[];
  predefined: boolean;
  created_at: string;
  updated_at: string;
};

export type CaseInput = Omit<TestCase, "predefined" | "created_at" | "updated_at">;

export type CaseResult = {
  case_code: string;
  expected: Expected;
  actual: Actual;
  fired_policy_codes: string[];
  passed: boolean;
  failure: string | null;
  policy_version: number | null;
  duration_ms: number;
};

export type TestRun = {
  id: string;
  started_at: string;
  finished_at: string | null;
  started_by: string;
  mode: "check";
  policy_version: number;
  status: "running" | "passed" | "failed";
  /** Final when the run ends; while it runs, passed and failed stay 0. */
  totals: { total: number; passed: number; failed: number };
  results?: CaseResult[];
};

const path = encodeURIComponent;

export const testsApi = {
  listCases: () => api<{ test_cases: TestCase[] }>("/admin/test-cases").then(({ test_cases }) => test_cases),
  createCase: (body: CaseInput) => api<{ test_case: TestCase }>("/admin/test-cases", { method: "POST", body }).then(({ test_case }) => test_case),
  updateCase: (code: string, body: Partial<CaseInput>) =>
    api<{ test_case: TestCase }>(`/admin/test-cases/${path(code)}`, { method: "PATCH", body }).then(({ test_case }) => test_case),
  /** Answers when the run ends; results are stored as each case finishes, so the run can be polled meanwhile. */
  run: (startedBy: string, cases?: string[]) =>
    api<{ test_run: TestRun }>("/admin/test-runs", { method: "POST", body: { started_by: startedBy, ...(cases ? { cases } : {}) } })
      .then(({ test_run }) => test_run),
  listRuns: () => api<{ test_runs: TestRun[] }>("/admin/test-runs").then(({ test_runs }) => test_runs),
  getRun: (id: string) => api<{ test_run: TestRun }>(`/admin/test-runs/${path(id)}`).then(({ test_run }) => test_run),
};

/** Passed and failed counts from the results so far, for a run still in progress. */
export function progress(run: TestRun) {
  const results = run.results ?? [];
  const passed = results.filter((result) => result.passed).length;
  return { done: results.length, passed, failed: results.length - passed, total: run.totals.total };
}

const POLL_MS = 700;

/** Loads a run and keeps polling it while it is running, so results appear as each case finishes. */
export function useRunDetail(runId: string | undefined) {
  const [run, setRun] = useState<TestRun>();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRun(undefined);
    setError(null);
    if (!runId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const next = await testsApi.getRun(runId!);
        if (!active) return;
        setRun(next);
        setError(null);
        if (next.status === "running") timer = setTimeout(() => void poll(), POLL_MS);
      } catch (reason) {
        if (!active) return;
        setError(errorMessage(reason));
        timer = setTimeout(() => void poll(), POLL_MS * 4);
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [runId]);

  return { run, error };
}

/** Finds the run a POST just started: the newest running one this user started since the click. */
export async function findStartedRun(startedBy: string, since: number): Promise<TestRun | undefined> {
  const runs = await testsApi.listRuns();
  return runs.find((run) => run.started_by === startedBy && run.status === "running" && new Date(run.started_at).getTime() >= since - 5000);
}

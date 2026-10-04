import { api } from "../../lib/api";

// Mirrors gateway/src/intentlatch/reports.py.
export type FiredPolicy = { code: string; kind: string | null; ai: boolean; action: "block" | "edit"; reasoning: string | null };

/** One edited or blocked prompt or response from the decision log, with masked text only. */
export type ReportItem = {
  ts: string;
  request_id: string;
  direction: "prompt" | "response";
  outcome: "edited" | "blocked";
  team: string;
  model: string;
  employee_id: string;
  employee: string;
  control_agent_status: "skipped" | "pass" | "blocked" | "modified" | "error";
  /** The violated policies; empty when a control agent failure blocked the text. */
  policies: FiredPolicy[];
  source_masked: string;
  rewritten_masked: string | null;
  policy_version: number;
  test_run_id: string | null;
};

export type Breakdown = { blocked: number; edited: number; total: number };

export type Report = {
  from: string;
  to: string;
  generated_at: string;
  totals: Breakdown & {
    by_policy: (Breakdown & { code: string })[];
    by_team: (Breakdown & { team: string })[];
    by_model: (Breakdown & { model: string })[];
  };
  /** Oldest first. */
  items: ReportItem[];
};

function range(from: string, to: string) {
  return new URLSearchParams({ from, to }).toString();
}

export const reportsApi = {
  report: (from: string, to: string) => api<Report>(`/admin/reports?${range(from, to)}`),
  itemsCsvUrl: (from: string, to: string) => `/admin/reports/items.csv?${range(from, to)}`,
  totalsCsvUrl: (from: string, to: string) => `/admin/reports/totals.csv?${range(from, to)}`,
};

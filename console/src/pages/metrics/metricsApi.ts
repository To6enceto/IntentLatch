import { api } from "../../lib/api";

export const RANGES = ["1h", "6h", "24h", "7d"] as const;
export type Range = (typeof RANGES)[number];
export const RANGE_LABELS: Record<Range, string> = { "1h": "Last hour", "6h": "Last 6 hours", "24h": "Last 24 hours", "7d": "Last 7 days" };

export type Outcome = "allowed" | "blocked" | "edited" | "error";
export type OutcomeCounts = Record<Outcome, number> & { total: number };

export type Dashboard = {
  range: Range;
  start: string;
  end: string;
  step_seconds: number;
  instances: number;
  policies_active: number | null;
  requests: OutcomeCounts;
  upstream_errors: number;
  series: {
    timestamps: number[];
    requests: Partial<Record<Outcome, number[]>>;
    enforcements: Partial<Record<"block" | "edit", number[]>>;
    latency_p95: (number | null)[];
  };
  top_policies: { policy_code: string; kind: string | null; block: number; edit: number; total: number }[];
  teams: (OutcomeCounts & { team: string })[];
  models: (OutcomeCounts & { model: string })[];
  auth_failures: { reason: string; count: number }[];
  tokens: { team: string; prompt: number; response: number; total: number }[];
  latency: { stage: string; p50: number | null; p95: number | null }[];
  test_cases: Partial<Record<"passed" | "failed" | "error", number>>;
  control_agent: { verdicts: Partial<Record<"pass" | "blocked" | "modified", number>>; errors: number };
};

export const metricsApi = {
  dashboard: (range: Range) => api<Dashboard>(`/admin/metrics?range=${range}`),
};

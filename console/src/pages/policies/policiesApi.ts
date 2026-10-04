import { api } from "../../lib/api";

export type Kind = "authority" | "limit" | "regex";
export type Action = "block" | "edit";
export type AppliesTo = "prompt" | "response" | "both";

export type LimitParams = { max_tokens: number; window_seconds: number; team: string | null };
export type RegexParams = { pattern: string };

export type Policy = {
  code: string;
  ai: boolean;
  text: string | null;
  kind: Kind | null;
  params: Partial<LimitParams & RegexParams>;
  action: Action;
  applies_to: AppliesTo;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type PolicyCreate = Pick<Policy, "code" | "ai" | "action" | "enabled"> & {
  text?: string;
  kind?: Kind;
  params?: LimitParams | RegexParams;
  applies_to?: AppliesTo;
};

export type PolicyUpdate = Partial<Pick<Policy, "text" | "action" | "applies_to" | "enabled">> & {
  params?: LimitParams | RegexParams;
};

/** Every create and edit returns the policy and the new global policy version. */
export type PolicyWrite = { policy: Policy; version: number };

export const policiesApi = {
  list: () => api<{ policies: Policy[]; version: number }>("/admin/policies"),
  create: (body: PolicyCreate) => api<PolicyWrite>("/admin/policies", { method: "POST", body }),
  update: (code: string, body: PolicyUpdate) =>
    api<PolicyWrite>(`/admin/policies/${encodeURIComponent(code)}`, { method: "PATCH", body }),
};

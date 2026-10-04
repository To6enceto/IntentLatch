import type { Kind, Policy } from "./policiesApi";

// Mirrors gateway/src/intentlatch/policies.py.
export const CODE_MAX = 64;
export const TEXT_MAX = 1000;
export const PATTERN_MAX = 1000;
export const INT_MAX = 2_147_483_647;
const CODE_PATTERN = /^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$/;

export const KIND_LABELS: Record<Kind, string> = { authority: "Authority", limit: "Limit", regex: "Regex" };
export const CODE_PREFIXES: Record<Kind | "ai", string> = { ai: "AI-", authority: "AUTH-", limit: "LIM-", regex: "RGX-" };

export function typeLabel(policy: Pick<Policy, "ai" | "kind">) {
  return policy.ai ? "AI" : KIND_LABELS[policy.kind ?? "regex"];
}

/** The code the gateway will store, or an error message. ASCII is checked before uppercasing, as in the gateway. */
export function normalizeCode(value: string): { code: string } | { error: string } {
  const code = value.trim();
  if (!code) return { error: "Enter a code." };
  if (!/^[\x00-\x7F]*$/.test(code) || code.length < 2 || code.length > CODE_MAX || !CODE_PATTERN.test(code.toUpperCase())) {
    return { error: `Use 2 to ${CODE_MAX} letters, digits and single hyphens, starting with a letter.` };
  }
  return { code: code.toUpperCase() };
}

export function parseCount(value: string, label: string): { value: number } | { error: string } {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return { error: `Enter ${label} as a whole number.` };
  const number = Number(trimmed);
  if (number < 1 || number > INT_MAX) return { error: `Use 1 to ${INT_MAX.toLocaleString()}.` };
  return { value: number };
}

export const WINDOW_UNITS = [
  { id: "seconds", label: "seconds", seconds: 1 },
  { id: "minutes", label: "minutes", seconds: 60 },
  { id: "hours", label: "hours", seconds: 3600 },
  { id: "days", label: "days", seconds: 86_400 },
] as const;
export type WindowUnit = (typeof WINDOW_UNITS)[number]["id"];

/** The largest unit that divides the window evenly, so 3600 shows as 1 hour. */
export function splitWindow(seconds: number): { amount: string; unit: WindowUnit } {
  const unit = [...WINDOW_UNITS].reverse().find((candidate) => seconds % candidate.seconds === 0) ?? WINDOW_UNITS[0];
  return { amount: String(seconds / unit.seconds), unit: unit.id };
}

/** "hour" for one unit, otherwise "90 minutes". */
export function formatWindow(seconds: number) {
  const { amount, unit } = splitWindow(seconds);
  const label = WINDOW_UNITS.find((candidate) => candidate.id === unit)!.label;
  return amount === "1" ? label.slice(0, -1) : `${Number(amount).toLocaleString()} ${label}`;
}

/** One line describing what the policy checks, for the list. */
export function ruleSummary(policy: Policy) {
  if (policy.ai) return policy.text ?? "";
  if (policy.kind === "authority") return "The team must be authorized for the requested model.";
  if (policy.kind === "limit") {
    const { max_tokens = 0, window_seconds = 0, team } = policy.params;
    return `${max_tokens.toLocaleString()} tokens per ${formatWindow(window_seconds)}, ${team ? `team ${team}` : "every team"}`;
  }
  return policy.params.pattern ?? "";
}

import { useState, type FormEvent } from "react";
import { Icon } from "../../components/Icon";
import { ChoiceGroup } from "../../components/ui/ChoiceGroup";
import { Button } from "../../components/ui/Button";
import { Dialog, DialogFooter } from "../../components/ui/Dialog";
import { Input } from "../../components/ui/Input";
import { Switch } from "../../components/ui/Switch";
import { ApiError, errorMessage } from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import { useResource } from "../../lib/useResource";
import { teamsApi } from "../teams/teamsApi";
import {
  policiesApi,
  type Action,
  type AppliesTo,
  type Kind,
  type LimitParams,
  type Policy,
  type PolicyCreate,
  type PolicyUpdate,
  type PolicyWrite,
  type RegexParams,
} from "./policiesApi";
import {
  CODE_MAX,
  CODE_PREFIXES,
  KIND_LABELS,
  PATTERN_MAX,
  TEXT_MAX,
  WINDOW_UNITS,
  normalizeCode,
  parseCount,
  splitWindow,
  typeLabel,
  type WindowUnit,
} from "./policyRules";

type Draft = {
  ai: boolean;
  kind: Kind;
  code: string;
  text: string;
  pattern: string;
  maxTokens: string;
  windowAmount: string;
  windowUnit: WindowUnit;
  team: string;
  action: Action;
  appliesTo: AppliesTo;
  enabled: boolean;
};

type Field = "code" | "text" | "pattern" | "maxTokens" | "windowAmount" | "team" | "form";
type Errors = Partial<Record<Field, string>>;

const NEW_DRAFT: Draft = {
  ai: false, kind: "regex", code: "", text: "", pattern: "", maxTokens: "", windowAmount: "1", windowUnit: "hours",
  team: "", action: "block", appliesTo: "both", enabled: true,
};

// Server messages look like "params.pattern: Value error, invalid regular expression: ...".
const SERVER_FIELDS: Record<string, Field> = {
  code: "code", text: "text", "params.pattern": "pattern", "params.max_tokens": "maxTokens",
  "params.window_seconds": "windowAmount", "params.team": "team",
};

function draftFrom(policy: Policy): Draft {
  const window = splitWindow(policy.params.window_seconds ?? 3600);
  return {
    ai: policy.ai,
    kind: policy.kind ?? "regex",
    code: policy.code,
    text: policy.text ?? "",
    pattern: policy.params.pattern ?? "",
    maxTokens: policy.params.max_tokens ? String(policy.params.max_tokens) : "",
    windowAmount: window.amount,
    windowUnit: window.unit,
    team: policy.params.team ?? "",
    action: policy.action,
    appliesTo: policy.applies_to,
    enabled: policy.enabled,
  };
}

// The gateway stores params as jsonb, which does not keep key order.
function sameParams(a: object, b: object) {
  const entries = (value: object) => JSON.stringify(Object.entries(value).sort(([x], [y]) => (x < y ? -1 : 1)));
  return entries(a) === entries(b);
}

/** Authority and limit policies always block and check prompts only. */
function isFixed(draft: Draft) {
  return !draft.ai && (draft.kind === "authority" || draft.kind === "limit");
}

function serverErrors(error: unknown): Errors {
  if (error instanceof ApiError && error.code === "policy_exists") return { code: "A policy with this code already exists." };
  if (error instanceof ApiError && error.code === "invalid_request") {
    const match = /^([a-z_.]+): (?:Value error, )?(.*)$/s.exec(error.message);
    const field = match && SERVER_FIELDS[match[1]!];
    if (field) return { [field]: `${match[2]!.charAt(0).toUpperCase()}${match[2]!.slice(1)}` };
  }
  return { form: errorMessage(error) };
}

type PolicyFormProps = {
  /** The policy to edit, or undefined to create one. */
  policy?: Policy;
  existingCodes: string[];
  readOnly: boolean;
  onClose: () => void;
  onSaved: (result: PolicyWrite) => void;
};

export function PolicyForm({ policy, existingCodes, readOnly, onClose, onSaved }: PolicyFormProps) {
  const creating = !policy;
  const [draft, setDraft] = useState<Draft>(() => (policy ? draftFrom(policy) : NEW_DRAFT));
  const [errors, setErrors] = useState<Errors>({});
  const [codeTouched, setCodeTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const teams = useResource(teamsApi.list, []);
  const fixed = isFixed(draft);
  const action = fixed ? "block" : draft.action;
  const appliesTo = fixed ? "prompt" : draft.appliesTo;

  const normalized = normalizeCode(draft.code);
  const duplicate = creating && "code" in normalized && existingCodes.includes(normalized.code);
  const codeError = errors.code
    ?? (duplicate ? `${"code" in normalized ? normalized.code : ""} is already used by another policy.` : undefined)
    ?? (codeTouched && "error" in normalized ? normalized.error : undefined);

  function change<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((previous) => ({ ...previous, [key]: value }));
    const field = ({ windowUnit: "windowAmount" } as Partial<Record<keyof Draft, Field>>)[key] ?? key;
    setErrors((previous) => ({ ...previous, [field]: undefined, form: undefined }));
  }

  function params(): { params?: LimitParams | RegexParams; errors: Errors } {
    if (draft.ai || draft.kind === "authority") return { errors: {} };
    if (draft.kind === "regex") {
      if (!draft.pattern) return { errors: { pattern: "Enter a pattern." } };
      if (draft.pattern.includes("\0")) return { errors: { pattern: "The pattern must not contain NUL characters." } };
      return { params: { pattern: draft.pattern }, errors: {} };
    }
    const maxTokens = parseCount(draft.maxTokens, "the token budget");
    const amount = parseCount(draft.windowAmount, "the window");
    const unit = WINDOW_UNITS.find((candidate) => candidate.id === draft.windowUnit)!;
    const windowSeconds = "value" in amount ? amount.value * unit.seconds : 0;
    const found: Errors = {
      maxTokens: "error" in maxTokens ? maxTokens.error : undefined,
      windowAmount: "error" in amount ? amount.error : windowSeconds > 2_147_483_647 ? "The window is too long." : undefined,
    };
    if (found.maxTokens || found.windowAmount) return { errors: found };
    return { params: { max_tokens: (maxTokens as { value: number }).value, window_seconds: windowSeconds, team: draft.team || null }, errors: {} };
  }

  function validate(): { body?: PolicyCreate | PolicyUpdate; errors: Errors } {
    const found: Errors = {};
    if (creating) {
      if ("error" in normalized) found.code = normalized.error;
      else if (duplicate) found.code = `${normalized.code} is already used by another policy.`;
    }
    const text = draft.text.trim();
    if (draft.ai && !text) found.text = "Describe the rule in plain language.";
    const built = params();
    Object.assign(found, built.errors);
    if (Object.values(found).some(Boolean)) return { errors: found };

    if (creating) {
      const body: PolicyCreate = { code: (normalized as { code: string }).code, ai: draft.ai, action, applies_to: appliesTo, enabled: draft.enabled };
      if (draft.ai) body.text = text;
      else body.kind = draft.kind;
      if (built.params) body.params = built.params;
      return { body, errors: {} };
    }
    // Only changed fields; the gateway re-checks the merged policy.
    const body: PolicyUpdate = {};
    if (draft.ai && text !== policy.text) body.text = text;
    if (built.params && !sameParams(built.params, policy.params)) body.params = built.params;
    if (action !== policy.action) body.action = action;
    if (appliesTo !== policy.applies_to) body.applies_to = appliesTo;
    if (draft.enabled !== policy.enabled) body.enabled = draft.enabled;
    return { body, errors: {} };
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (readOnly || pending) return;
    setCodeTouched(true);
    const { body, errors: found } = validate();
    setErrors(found);
    if (!body) {
      const first = (["code", "text", "pattern", "maxTokens", "windowAmount"] as const).find((field) => found[field]);
      if (first) document.getElementById(`policy-${first}`)?.focus();
      return;
    }
    if (!creating && !Object.keys(body).length) {
      onClose();
      return;
    }
    setPending(true);
    try {
      onSaved(creating ? await policiesApi.create(body as PolicyCreate) : await policiesApi.update(policy.code, body));
    } catch (error) {
      setPending(false);
      setErrors(serverErrors(error));
    }
  }

  const title = creating ? "New policy" : `${readOnly ? "" : "Edit "}${policy.code}`;
  const description = creating
    ? "Non-AI policies run first and are deterministic. AI policies are checked by the control agent afterwards."
    : <>{typeLabel(policy)} policy · updated <time dateTime={policy.updated_at}>{formatDateTime(policy.updated_at)}</time></>;

  return (
    <Dialog title={title} description={description} wide onClose={() => { if (!pending) onClose(); }}>
      <form onSubmit={(event) => void submit(event)} noValidate>
        <fieldset className="dialog-body policy-form" disabled={readOnly || pending}>
          {creating ? (
            <>
              <ChoiceGroup legend="Type" name="policy-ai" value={draft.ai ? "ai" : "non-ai"} onChange={(value) => change("ai", value === "ai")} options={[
                { value: "non-ai", label: "Non-AI policy", hint: "A deterministic check: authority, limit or regex." },
                { value: "ai", label: "AI policy", hint: "A plain-language rule the control agent judges." },
              ]} />
              {!draft.ai && (
                <ChoiceGroup legend="Kind" name="policy-kind" value={draft.kind} onChange={(value) => change("kind", value as Kind)} columns={3} options={[
                  { value: "authority", label: "Authority", hint: "The team must be authorized for the model." },
                  { value: "limit", label: "Limit", hint: "A token budget over a time window." },
                  { value: "regex", label: "Regex", hint: "A pattern in prompts or responses." },
                ]} />
              )}
              <div className="field">
                <label htmlFor="policy-code">Code</label>
                <Input id="policy-code" className="font-mono uppercase" value={draft.code} maxLength={CODE_MAX + 8} autoComplete="off" spellCheck={false}
                  placeholder={`${CODE_PREFIXES[draft.ai ? "ai" : draft.kind]}EXAMPLE`} data-autofocus
                  aria-invalid={!!codeError} aria-describedby="policy-code-hint"
                  onChange={(event) => change("code", event.target.value)} onBlur={() => setCodeTouched(true)} />
                {codeError
                  ? <p id="policy-code-hint" className="field-error">{codeError}</p>
                  : <p id="policy-code-hint" className="field-hint">Unique and permanent. Letters, digits and single hyphens; stored in upper case{"code" in normalized && normalized.code !== draft.code.trim() ? ` as ${normalized.code}` : ""}.</p>}
              </div>
            </>
          ) : (
            <dl className="policy-facts">
              <div><dt>Code</dt><dd className="mono">{policy.code}</dd></div>
              <div><dt>Type</dt><dd>{policy.ai ? "AI policy" : `Non-AI · ${KIND_LABELS[policy.kind!]}`}</dd></div>
              <div><dt>Created</dt><dd><time dateTime={policy.created_at}>{formatDateTime(policy.created_at)}</time></dd></div>
            </dl>
          )}

          {draft.ai && (
            <div className="field">
              <label htmlFor="policy-text">Rule</label>
              <textarea id="policy-text" className="textarea" rows={4} value={draft.text} maxLength={TEXT_MAX}
                placeholder="For example: Do not share customer credentials, API keys or passwords."
                aria-invalid={!!errors.text} aria-describedby="policy-text-hint" onChange={(event) => change("text", event.target.value)} />
              {errors.text
                ? <p id="policy-text-hint" className="field-error">{errors.text}</p>
                : <p id="policy-text-hint" className="field-hint">Plain language the control agent can judge. {draft.text.trim().length} / {TEXT_MAX}</p>}
            </div>
          )}

          {!draft.ai && draft.kind === "authority" && (
            <p className="notice"><Icon name="shield" />Blocks a request when the employee's team is not authorized for the requested model. It has no settings.</p>
          )}

          {!draft.ai && draft.kind === "regex" && (
            <div className="field">
              <label htmlFor="policy-pattern">Pattern</label>
              <textarea id="policy-pattern" className="textarea font-mono" rows={3} value={draft.pattern} maxLength={PATTERN_MAX} spellCheck={false}
                placeholder="(?i)\bconfidential\b" aria-invalid={!!errors.pattern} aria-describedby="policy-pattern-hint"
                onChange={(event) => change("pattern", event.target.value)} />
              {errors.pattern && <p className="field-error" role="alert">{errors.pattern}</p>}
              <p id="policy-pattern-hint" className="field-hint">
                Python <code className="mono">re</code> syntax, checked by the gateway when you save. Case-sensitive unless it starts with <code className="mono">(?i)</code>.
                A group named <code className="mono">luhn</code> counts a match only when its digits pass the Luhn checksum. Avoid nested repeats such as <code className="mono">(a+)+</code>.
              </p>
            </div>
          )}

          {!draft.ai && draft.kind === "limit" && (
            <div className="limit-grid">
              <div className="field">
                <label htmlFor="policy-maxTokens">Token budget</label>
                <Input id="policy-maxTokens" inputMode="numeric" value={draft.maxTokens} placeholder="50000" aria-invalid={!!errors.maxTokens}
                  aria-describedby={errors.maxTokens ? "policy-maxTokens-error" : undefined} onChange={(event) => change("maxTokens", event.target.value)} />
                {errors.maxTokens && <p id="policy-maxTokens-error" className="field-error">{errors.maxTokens}</p>}
              </div>
              <div className="field">
                <label htmlFor="policy-windowAmount">Per window of</label>
                <div className="inline-form">
                  <Input id="policy-windowAmount" inputMode="numeric" className="w-24" value={draft.windowAmount} aria-invalid={!!errors.windowAmount}
                    aria-describedby={errors.windowAmount ? "policy-window-error" : undefined} onChange={(event) => change("windowAmount", event.target.value)} />
                  <select className="select" aria-label="Window unit" value={draft.windowUnit} onChange={(event) => change("windowUnit", event.target.value as WindowUnit)}>
                    {WINDOW_UNITS.map((unit) => <option key={unit.id} value={unit.id}>{unit.label}</option>)}
                  </select>
                </div>
                {errors.windowAmount && <p id="policy-window-error" className="field-error">{errors.windowAmount}</p>}
              </div>
              <div className="field limit-team">
                <label htmlFor="policy-team">Team</label>
                <select id="policy-team" className="select" value={draft.team} aria-invalid={!!errors.team} onChange={(event) => change("team", event.target.value)}>
                  <option value="">Every team</option>
                  {/* Keep a stored team selectable even before the list loads. */}
                  {draft.team && !teams.data?.some((team) => team.name === draft.team) && <option value={draft.team}>{draft.team}</option>}
                  {teams.data?.map((team) => <option key={team.id} value={team.name}>{team.name}</option>)}
                </select>
                {errors.team ? <p className="field-error">{errors.team}</p>
                  : teams.error ? <p className="field-error">Teams could not be loaded: {teams.error}</p>
                    : <p className="field-hint">Limit one team, or apply the budget to every team.</p>}
              </div>
            </div>
          )}

          <ChoiceGroup legend="Action" name="policy-action" value={action} disabled={fixed} onChange={(value) => change("action", value as Action)} columns={2} options={[
            { value: "block", label: "Block", hint: "Reject the prompt, or discard the response." },
            { value: "edit", label: "Edit", hint: "Remove the violating content, then continue." },
          ]} />
          <ChoiceGroup legend="Applies to" name="policy-applies" value={appliesTo} disabled={fixed} onChange={(value) => change("appliesTo", value as AppliesTo)} columns={3} compact options={[
            { value: "prompt", label: "Prompt" },
            { value: "response", label: "Response" },
            { value: "both", label: "Both" },
          ]} />
          {fixed && <p className="field-hint -mt-2">{KIND_LABELS[draft.kind]} policies always block and check prompts only.</p>}

          <div className="switch-field">
            <Switch id="policy-enabled" checked={draft.enabled} onCheckedChange={(checked) => change("enabled", checked)} aria-describedby="policy-enabled-hint" />
            <div>
              <label htmlFor="policy-enabled">Enabled</label>
              <p id="policy-enabled-hint" className="field-hint">The gateway applies a change from the next request. Policies cannot be deleted; disable them instead.</p>
            </div>
          </div>

          {errors.form && <p className="notice is-error" role="alert"><Icon name="alert" />{errors.form}</p>}
        </fieldset>
        <DialogFooter>
          {readOnly ? <Button onClick={onClose}>Close</Button> : (
            <>
              <Button variant="outline" onClick={onClose} disabled={pending}>Cancel</Button>
              <Button type="submit" disabled={pending}>{pending ? "Saving…" : creating ? "Create policy" : "Save changes"}</Button>
            </>
          )}
        </DialogFooter>
      </form>
    </Dialog>
  );
}

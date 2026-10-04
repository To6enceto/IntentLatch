import { useState, type FormEvent, type KeyboardEvent } from "react";
import { Icon } from "../../components/Icon";
import { Button } from "../../components/ui/Button";
import { ChoiceGroup } from "../../components/ui/ChoiceGroup";
import { Dialog, DialogFooter } from "../../components/ui/Dialog";
import { Input } from "../../components/ui/Input";
import { ApiError, errorMessage } from "../../lib/api";
import { useResource } from "../../lib/useResource";
import { policiesApi } from "../policies/policiesApi";
import { normalizeCode } from "../policies/policyRules";
import { MODEL_IDS, teamsApi } from "../teams/teamsApi";
import {
  ENTRY_MAX, MUST_NOT_CONTAIN_MAX, NAME_MAX, PROMPT_MAX, TEST_EMPLOYEE_ID, testsApi,
  type CaseInput, type Expected, type TestCase,
} from "./testsApi";

type Field = "code" | "name" | "prompt" | "must_not_contain" | "form";
type Errors = Partial<Record<Field, string>>;

const NEW_CASE: CaseInput = {
  code: "", name: "", prompt: "", model: MODEL_IDS[0], run_as_employee_id: TEST_EMPLOYEE_ID,
  expected: "BLOCK", expected_policy_code: null, must_not_contain: [],
};
const SERVER_FIELDS: Record<string, Field> = { code: "code", name: "name", prompt: "prompt", must_not_contain: "must_not_contain" };

function serverErrors(error: unknown): Errors {
  if (error instanceof ApiError && error.status === 409) return { code: "A test case with this code already exists." };
  if (error instanceof ApiError && error.code === "invalid_request") {
    const match = /^([a-z_]+)(?:\.\d+)?: (?:Value error, )?(.*)$/s.exec(error.message);
    const field = match ? SERVER_FIELDS[match[1]!] : undefined;
    if (match && field) return { [field]: `${match[2]!.charAt(0).toUpperCase()}${match[2]!.slice(1)}` };
  }
  return { form: errorMessage(error) };
}

/** Active employees to run as, grouped by team; the seeded test runner is among them. */
function useEmployees() {
  return useResource(async () => {
    const teams = await teamsApi.list();
    const lists = await Promise.all(teams.map((team) => teamsApi.employees(team.id)));
    return teams.map((team, index) => ({ team, employees: lists[index]!.filter((employee) => !employee.revoked_at) }));
  }, []);
}

export type CaseFormMode =
  | { kind: "new" }
  | { kind: "edit"; testCase: TestCase }
  | { kind: "view"; testCase: TestCase }
  | { kind: "duplicate"; testCase: TestCase };

type CaseFormProps = {
  mode: CaseFormMode;
  canWrite: boolean;
  existingCodes: string[];
  onClose: () => void;
  onSaved: (testCase: TestCase) => void;
  onDuplicate: (testCase: TestCase) => void;
};

export function CaseForm({ mode, canWrite, existingCodes, onClose, onSaved, onDuplicate }: CaseFormProps) {
  const source = mode.kind === "new" ? undefined : mode.testCase;
  const readOnly = mode.kind === "view";
  const creating = mode.kind === "new" || mode.kind === "duplicate";
  const [values, setValues] = useState<CaseInput>(() => {
    if (!source) return NEW_CASE;
    const copy = mode.kind === "duplicate";
    return {
      code: copy ? `${source.code}-COPY`.slice(0, 64) : source.code,
      name: copy ? `Copy of ${source.name}`.slice(0, NAME_MAX) : source.name,
      prompt: source.prompt, model: source.model, run_as_employee_id: source.run_as_employee_id,
      expected: source.expected, expected_policy_code: source.expected_policy_code, must_not_contain: source.must_not_contain,
    };
  });
  const [entry, setEntry] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [codeTouched, setCodeTouched] = useState(mode.kind === "duplicate");
  const [pending, setPending] = useState(false);
  const policies = useResource(policiesApi.list, []);
  const employees = useEmployees();
  const allow = values.expected === "ALLOW";
  const edit = values.expected === "EDIT";

  const normalized = normalizeCode(values.code);
  const duplicate = creating && "code" in normalized && existingCodes.includes(normalized.code);
  const codeError = errors.code
    ?? (duplicate && "code" in normalized ? `${normalized.code} is already used by another case.` : undefined)
    ?? (codeTouched && "error" in normalized ? normalized.error : undefined);

  function change<K extends keyof CaseInput>(key: K, value: CaseInput[K]) {
    setValues((previous) => ({ ...previous, [key]: value }));
    setErrors((previous) => ({ ...previous, [key]: undefined, form: undefined }));
  }

  function addEntry() {
    const value = entry.trim();
    if (!value) return;
    if (values.must_not_contain.length >= MUST_NOT_CONTAIN_MAX) {
      setErrors((previous) => ({ ...previous, must_not_contain: `Use at most ${MUST_NOT_CONTAIN_MAX} strings.` }));
      return;
    }
    if (!values.must_not_contain.includes(value)) change("must_not_contain", [...values.must_not_contain, value]);
    setEntry("");
  }

  /** What the gateway stores: no policy on ALLOW cases, and strings only on EDIT cases. */
  function shaped(): Omit<CaseInput, "code"> {
    return {
      name: values.name.trim(),
      prompt: values.prompt,
      model: values.model,
      run_as_employee_id: values.run_as_employee_id,
      expected: values.expected,
      expected_policy_code: allow ? null : values.expected_policy_code,
      must_not_contain: edit ? values.must_not_contain : [],
    };
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (readOnly || pending) return;
    setCodeTouched(true);
    const found: Errors = {
      code: creating ? ("error" in normalized ? normalized.error : duplicate ? "This code is already used by another case." : undefined) : undefined,
      name: values.name.trim() ? undefined : "Name the case.",
      prompt: values.prompt.trim() ? undefined : "Enter the prompt to check.",
    };
    setErrors(found);
    const first = (["code", "name", "prompt"] as const).find((field) => found[field]);
    if (first) {
      document.getElementById(`case-${first}`)?.focus();
      return;
    }
    const body = shaped();
    setPending(true);
    try {
      if (creating) {
        onSaved(await testsApi.createCase({ code: (normalized as { code: string }).code, ...body }));
        return;
      }
      // Only what changed; the gateway re-checks the merged case.
      const original = source!;
      const changes = Object.fromEntries(Object.entries(body).filter(([key, value]) =>
        JSON.stringify(value) !== JSON.stringify(original[key as keyof TestCase])));
      if (!Object.keys(changes).length) {
        onClose();
        return;
      }
      onSaved(await testsApi.updateCase(original.code, changes));
    } catch (error) {
      setPending(false);
      setErrors(serverErrors(error));
    }
  }

  const title = mode.kind === "new" ? "New test case" : mode.kind === "duplicate" ? "Duplicate test case" : mode.kind === "edit" ? `Edit ${source!.code}` : source!.name;
  const description = source?.predefined && mode.kind === "edit"
    ? "A predefined case. Your changes are kept; the gateway only adds predefined cases that are missing."
    : "The case runs through the gateway's /check as the chosen employee. Nothing is sent to a model or written to the decision log.";

  return (
    <Dialog title={title} description={description} wide onClose={() => { if (!pending) onClose(); }}>
      <form onSubmit={(event) => void submit(event)} noValidate>
        <fieldset className="dialog-body policy-form" disabled={readOnly || pending}>
          {creating ? (
            <div className="field">
              <label htmlFor="case-code">Code</label>
              <Input id="case-code" className="font-mono uppercase" value={values.code} maxLength={72} autoComplete="off" spellCheck={false}
                placeholder="TC-EXAMPLE" data-autofocus aria-invalid={!!codeError} aria-describedby="case-code-hint"
                onChange={(event) => change("code", event.target.value)} onBlur={() => setCodeTouched(true)} />
              {codeError
                ? <p id="case-code-hint" className="field-error">{codeError}</p>
                : <p id="case-code-hint" className="field-hint">Unique and permanent; stored in upper case{"code" in normalized && normalized.code !== values.code.trim() ? ` as ${normalized.code}` : ""}.</p>}
            </div>
          ) : (
            <dl className="policy-facts">
              <div><dt>Code</dt><dd className="mono">{source!.code}</dd></div>
              <div><dt>Origin</dt><dd>{source!.predefined ? "Predefined" : "Custom"}</dd></div>
              <div><dt>Model</dt><dd className="mono">{source!.model}</dd></div>
            </dl>
          )}

          <div className="field">
            <label htmlFor="case-name">Name</label>
            <Input id="case-name" value={values.name} maxLength={NAME_MAX} autoComplete="off" data-autofocus={creating || readOnly ? undefined : true}
              aria-invalid={!!errors.name} aria-describedby={errors.name ? "case-name-error" : undefined} onChange={(event) => change("name", event.target.value)} />
            {errors.name && <p id="case-name-error" className="field-error">{errors.name}</p>}
          </div>

          <div className="field">
            <label htmlFor="case-prompt">Prompt</label>
            <textarea id="case-prompt" className="textarea" rows={4} value={values.prompt} maxLength={PROMPT_MAX} spellCheck={false}
              aria-invalid={!!errors.prompt} aria-describedby="case-prompt-hint" onChange={(event) => change("prompt", event.target.value)} />
            {errors.prompt
              ? <p id="case-prompt-hint" className="field-error">{errors.prompt}</p>
              : <p id="case-prompt-hint" className="field-hint">Kept exactly as written, invisible characters included. {values.prompt.length.toLocaleString()} / {PROMPT_MAX.toLocaleString()}</p>}
          </div>

          <div className="limit-grid">
            <div className="field">
              <label htmlFor="case-model">Requested model</label>
              <select id="case-model" className="select" value={values.model} onChange={(event) => change("model", event.target.value)}>
                {MODEL_IDS.map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="case-employee">Runs as</label>
              <select id="case-employee" className="select" value={values.run_as_employee_id} onChange={(event) => change("run_as_employee_id", event.target.value)}>
                {!employees.data && <option value={values.run_as_employee_id}>{values.run_as_employee_id === TEST_EMPLOYEE_ID ? "test-runner" : "Loading employees…"}</option>}
                {employees.data?.map(({ team, employees: list }) => (
                  <optgroup key={team.id} label={`${team.name} (${team.authorized_models.join(", ")})`}>
                    {list.map((employee) => <option key={employee.id} value={employee.id}>{employee.name}</option>)}
                  </optgroup>
                ))}
              </select>
              <p className="field-hint">The team's authorized models apply, so authority cases depend on it.</p>
            </div>
          </div>

          <ChoiceGroup legend="Expected outcome" name="case-expected" value={values.expected} columns={3} compact onChange={(value) => change("expected", value as Expected)} options={[
            { value: "ALLOW", label: "Allow" },
            { value: "EDIT", label: "Edit" },
            { value: "BLOCK", label: "Block" },
          ]} />

          {!allow && (
            <div className="field">
              <label htmlFor="case-policy">Policy that must fire</label>
              <select id="case-policy" className="select" value={values.expected_policy_code ?? ""} onChange={(event) => change("expected_policy_code", event.target.value || null)}>
                <option value="">Any policy</option>
                {values.expected_policy_code && !policies.data?.policies.some((policy) => policy.code === values.expected_policy_code) && (
                  <option value={values.expected_policy_code}>{values.expected_policy_code}</option>
                )}
                {policies.data?.policies.map((policy) => <option key={policy.code} value={policy.code}>{policy.code}{policy.enabled ? "" : " (disabled)"}</option>)}
              </select>
              <p className="field-hint">The case passes only if this policy is among those responsible for the outcome.</p>
            </div>
          )}

          {edit && (
            <fieldset className="field">
              <legend>The rewrite must not contain</legend>
              <div className="chip-row">
                {values.must_not_contain.map((value) => (
                  <span key={value} className="chip">
                    <span className="mono">{value}</span>
                    {!readOnly && (
                      <button type="button" className="chip-remove" aria-label={`Remove ${value}`} onClick={() => change("must_not_contain", values.must_not_contain.filter((item) => item !== value))}>
                        <Icon name="close" className="size-3" />
                      </button>
                    )}
                  </span>
                ))}
                {readOnly && !values.must_not_contain.length && <span className="field-hint">Nothing listed</span>}
              </div>
              {!readOnly && (
                <div className="inline-form">
                  <Input aria-label="A string the rewrite must not contain" value={entry} maxLength={ENTRY_MAX} placeholder="For example the email address in the prompt"
                    onChange={(event) => setEntry(event.target.value)}
                    onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => { if (event.key === "Enter") { event.preventDefault(); addEntry(); } }} />
                  <Button variant="outline" onClick={addEntry}><Icon name="plus" />Add</Button>
                </div>
              )}
              {errors.must_not_contain
                ? <p className="field-error">{errors.must_not_contain}</p>
                : <p className="field-hint">Checked against the control agent's rewrite, ignoring case.</p>}
            </fieldset>
          )}

          {errors.form && <p className="notice is-error" role="alert"><Icon name="alert" />{errors.form}</p>}
        </fieldset>
        <DialogFooter>
          {readOnly ? (
            <>
              {canWrite && source && <Button variant="outline" onClick={() => onDuplicate(source)}><Icon name="copy" />Duplicate</Button>}
              <Button onClick={onClose}>Close</Button>
            </>
          ) : (
            <>
              <Button variant="outline" onClick={onClose} disabled={pending}>Cancel</Button>
              <Button type="submit" disabled={pending}>{pending ? "Saving…" : creating ? "Create case" : "Save changes"}</Button>
            </>
          )}
        </DialogFooter>
      </form>
    </Dialog>
  );
}

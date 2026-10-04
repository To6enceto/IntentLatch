import { useState, type FormEvent } from "react";
import { Icon } from "../../components/Icon";
import { Button } from "../../components/ui/Button";
import { Dialog, DialogFooter } from "../../components/ui/Dialog";
import { Input } from "../../components/ui/Input";
import { ApiError, errorMessage } from "../../lib/api";
import { MODEL_IDS, TEAM_NAME_MAX, teamsApi, type Employee, type Team } from "./teamsApi";

type ModelChoiceProps = { value: string[]; onChange: (models: string[]) => void; disabled?: boolean; invalid?: boolean };

export function ModelChoice({ value, onChange, disabled, invalid }: ModelChoiceProps) {
  function toggle(model: string, checked: boolean) {
    onChange(MODEL_IDS.filter((id) => (id === model ? checked : value.includes(id))));
  }
  return (
    <div className="check-row">
      {MODEL_IDS.map((model) => (
        <label className="check-option" key={model}>
          <input type="checkbox" checked={value.includes(model)} disabled={disabled} aria-invalid={invalid || undefined} onChange={(event) => toggle(model, event.target.checked)} />
          {model}
        </label>
      ))}
    </div>
  );
}

type CreateTeamDialogProps = { onClose: () => void; onCreated: (team: Team) => void };

export function CreateTeamDialog({ onClose, onCreated }: CreateTeamDialogProps) {
  const [name, setName] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [errors, setErrors] = useState<{ name?: string; models?: string; form?: string }>({});
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    const nextErrors = {
      name: trimmed ? undefined : "Enter a team name.",
      models: models.length ? undefined : "Select at least one model.",
    };
    setErrors(nextErrors);
    if (nextErrors.name || nextErrors.models) return;
    setPending(true);
    try {
      onCreated(await teamsApi.create(trimmed, models));
    } catch (error) {
      setPending(false);
      setErrors(error instanceof ApiError && error.code === "team_exists"
        ? { name: "A team with this name already exists." }
        : { form: errorMessage(error) });
    }
  }

  return (
    <Dialog title="New team" description="A team groups employees and decides which corporate models they may call." onClose={() => { if (!pending) onClose(); }}>
      <form onSubmit={(event) => void submit(event)} noValidate>
        <div className="dialog-body">
          <div className="field">
            <label htmlFor="team-name">Name</label>
            <Input id="team-name" value={name} maxLength={TEAM_NAME_MAX} autoComplete="off" data-autofocus aria-invalid={!!errors.name} aria-describedby={errors.name ? "team-name-error" : undefined} onChange={(event) => { setName(event.target.value); setErrors((previous) => ({ ...previous, name: undefined })); }} />
            {errors.name && <p id="team-name-error" className="field-error">{errors.name}</p>}
          </div>
          <fieldset className="field" aria-describedby={errors.models ? "team-models-error" : undefined}>
            <legend>Authorized models</legend>
            <ModelChoice value={models} invalid={!!errors.models} onChange={(next) => { setModels(next); setErrors((previous) => ({ ...previous, models: undefined })); }} />
            {errors.models && <p id="team-models-error" className="field-error">{errors.models}</p>}
          </fieldset>
          {errors.form && <p className="notice is-error" role="alert"><Icon name="alert" />{errors.form}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={pending}>Cancel</Button>
          <Button type="submit" disabled={pending}>{pending ? "Creating…" : "Create team"}</Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}

type TokenDialogProps = { employee: Employee; token: string; reissued: boolean; onClose: () => void };

export function TokenDialog({ employee, token, reissued, onClose }: TokenDialogProps) {
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");

  async function copyToken() {
    try {
      await navigator.clipboard.writeText(token);
      setCopy("copied");
    } catch {
      setCopy("failed");
    }
  }

  return (
    <Dialog
      title={reissued ? `New token for ${employee.name}` : `${employee.name} was added`}
      description="Copy this identity token now. It is shown only once and cannot be retrieved later. If it is lost, reissue it."
      onClose={onClose}
    >
      <div className="dialog-body">
        <code className="token-value" aria-label="Employee token">{token}</code>
        <p className="field-hint">
          The employee sends it as <code className="mono">Authorization: Bearer &lt;token&gt;</code>, or uses it as the API key in an OpenAI client.
          {reissued && " The previous token has stopped working."}
        </p>
        <p role="status" className={copy === "failed" ? "field-error" : "field-hint"}>
          {copy === "copied" && "Copied to the clipboard."}
          {copy === "failed" && "Copying failed. Select the token and copy it manually."}
        </p>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={() => void copyToken()} data-autofocus>
          <Icon name={copy === "copied" ? "check" : "copy"} />{copy === "copied" ? "Copied" : "Copy token"}
        </Button>
        <Button onClick={onClose}>Done</Button>
      </DialogFooter>
    </Dialog>
  );
}

import { useState, type FormEvent } from "react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Icon } from "../../components/Icon";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { errorMessage } from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import { useResource } from "../../lib/useResource";
import { ModelChoice, TokenDialog } from "./dialogs";
import { EMPLOYEE_NAME_MAX, sameModels, teamsApi, type Employee, type IssuedToken, type Team } from "./teamsApi";

type TeamDetailProps = { team: Team; canEdit: boolean; onTeamChange: (team: Team) => void };

export function TeamDetail({ team, canEdit, onTeamChange }: TeamDetailProps) {
  const employees = useResource(() => teamsApi.employees(team.id), [team.id]);
  const [issued, setIssued] = useState<IssuedToken & { reissued: boolean }>();
  const [pendingAction, setPendingAction] = useState<{ kind: "revoke" | "reissue"; employee: Employee }>();
  const activeCount = employees.data?.filter((employee) => !employee.revoked_at).length ?? 0;

  function replaceEmployee(next: Employee) {
    employees.update((list) => list.map((employee) => (employee.id === next.id ? next : employee)));
  }

  async function runAction() {
    if (!pendingAction) return;
    const { kind, employee } = pendingAction;
    if (kind === "revoke") {
      replaceEmployee(await teamsApi.revoke(employee.id));
    } else {
      const result = await teamsApi.reissue(employee.id);
      replaceEmployee(result.employee);
      setIssued({ ...result, reissued: true });
    }
    setPendingAction(undefined);
  }

  return (
    <section className="panel team-detail" aria-labelledby="team-title">
      <header className="panel-header">
        <div>
          <h2 id="team-title">{team.name}</h2>
          <p className="field-hint">Created <time dateTime={team.created_at}>{formatDateTime(team.created_at)}</time></p>
        </div>
      </header>

      <div className="panel-section">
        <ModelsEditor key={team.authorized_models.join()} team={team} canEdit={canEdit} onSaved={onTeamChange} />
      </div>

      <div className="panel-section">
        <h3 className="section-title">
          Employees
          {employees.data && <span className="count">{activeCount} active{employees.data.length > activeCount && `, ${employees.data.length - activeCount} revoked`}</span>}
        </h3>
        <p className="section-hint">Each employee has one identity token. The gateway checks it on every request and reads this team's models from the database.</p>
        {canEdit && <AddEmployeeForm teamId={team.id} onAdded={(result) => {
          employees.update((list) => [...list, result.employee]);
          setIssued({ ...result, reissued: false });
        }} />}
      </div>

      {employees.error && (
        <div className="panel-section">
          <div className="notice is-error" role="alert">
            <Icon name="alert" /><span>{employees.error}</span>
            <Button variant="outline" size="sm" className="ml-auto" onClick={employees.reload}>Retry</Button>
          </div>
        </div>
      )}
      {employees.loading && !employees.data && <p className="empty-state" role="status">Loading employees…</p>}
      {employees.data?.length === 0 && (
        <div className="empty-state">
          <h3>No employees yet</h3>
          <p>{canEdit ? "Add an employee above to issue their first token." : "An administrator can add employees to this team."}</p>
        </div>
      )}
      {!!employees.data?.length && (
        <div className="table-wrap team-employees">
          <table className="data-table">
            <caption className="sr-only">Employees of {team.name}</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Status</th>
                <th scope="col">Token issued</th>
                <th scope="col">Added</th>
                {canEdit && <th scope="col"><span className="sr-only">Actions</span></th>}
              </tr>
            </thead>
            <tbody>
              {employees.data.map((employee) => (
                <tr key={employee.id} className={employee.revoked_at ? "is-revoked" : undefined}>
                  <td className="font-medium">{employee.name}</td>
                  <td>
                    {employee.revoked_at ? (
                      <span className="status-cell">
                        <span className="badge is-danger"><span className="badge-dot" />Revoked</span>
                        <time className="field-hint" dateTime={employee.revoked_at}>{formatDateTime(employee.revoked_at)}</time>
                      </span>
                    ) : <span className="badge is-success"><span className="badge-dot" />Active</span>}
                  </td>
                  <td><time dateTime={employee.token_issued_at}>{formatDateTime(employee.token_issued_at)}</time></td>
                  <td><time dateTime={employee.created_at}>{formatDateTime(employee.created_at)}</time></td>
                  {canEdit && (
                    <td className="actions">
                      {!employee.revoked_at && (
                        <div className="btn-row">
                          <Button variant="outline" size="sm" aria-label={`Reissue token for ${employee.name}`} onClick={() => setPendingAction({ kind: "reissue", employee })}>
                            <Icon name="key" className="size-3.5" />Reissue token
                          </Button>
                          <Button variant="destructive-ghost" size="sm" aria-label={`Revoke ${employee.name}`} onClick={() => setPendingAction({ kind: "revoke", employee })}>
                            <Icon name="ban" className="size-3.5" />Revoke
                          </Button>
                        </div>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pendingAction?.kind === "reissue" && (
        <ConfirmDialog
          title={`Reissue the token for ${pendingAction.employee.name}?`}
          description="A new token is created and shown once. The current token stops working immediately."
          confirmLabel="Reissue token"
          onConfirm={runAction}
          onClose={() => setPendingAction(undefined)}
        />
      )}
      {pendingAction?.kind === "revoke" && (
        <ConfirmDialog
          title={`Revoke ${pendingAction.employee.name}?`}
          description="Their token stops working immediately. A revoked employee cannot get a new token; add them again to restore access."
          confirmLabel="Revoke"
          destructive
          onConfirm={runAction}
          onClose={() => setPendingAction(undefined)}
        />
      )}
      {issued && <TokenDialog employee={issued.employee} token={issued.token} reissued={issued.reissued} onClose={() => setIssued(undefined)} />}
    </section>
  );
}

type ModelsEditorProps = { team: Team; canEdit: boolean; onSaved: (team: Team) => void };

function ModelsEditor({ team, canEdit, onSaved }: ModelsEditorProps) {
  const [models, setModels] = useState(team.authorized_models);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = !sameModels(models, team.authorized_models);

  if (!canEdit) {
    return (
      <>
        <h3 className="section-title">Authorized models</h3>
        <p className="section-hint">Employees of this team may call only these models.</p>
        <div className="check-row">{team.authorized_models.map((model) => <span key={model} className="badge is-model">{model}</span>)}</div>
      </>
    );
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!dirty || !models.length) return;
    setPending(true);
    setError(null);
    try {
      // The parent remounts this editor with the saved models.
      onSaved(await teamsApi.setModels(team.id, models));
    } catch (reason) {
      setError(errorMessage(reason));
      setPending(false);
    }
  }

  return (
    <form onSubmit={(event) => void save(event)}>
      <fieldset className="field" aria-describedby="models-hint">
        <legend className="section-title">Authorized models</legend>
        <p id="models-hint" className="section-hint">Employees of this team may call only these models. A change applies from their next request.</p>
        <ModelChoice value={models} onChange={setModels} disabled={pending} invalid={!models.length} />
      </fieldset>
      {!models.length && <p className="field-error">Select at least one model.</p>}
      {error && <p className="field-error" role="alert">{error}</p>}
      {dirty && (
        <div className="btn-row mt-3">
          <Button type="submit" size="sm" disabled={pending || !models.length}>{pending ? "Saving…" : "Save models"}</Button>
          <Button variant="ghost" size="sm" disabled={pending} onClick={() => { setModels(team.authorized_models); setError(null); }}>Discard</Button>
        </div>
      )}
    </form>
  );
}

function AddEmployeeForm({ teamId, onAdded }: { teamId: string; onAdded: (result: IssuedToken) => void }) {
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Enter the employee's name.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      onAdded(await teamsApi.addEmployee(teamId, trimmed));
      setName("");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="add-employee" onSubmit={(event) => void submit(event)} noValidate>
      <label htmlFor="employee-name" className="field-label">Add an employee</label>
      <div className="inline-form">
        <Input id="employee-name" value={name} maxLength={EMPLOYEE_NAME_MAX} autoComplete="off" placeholder="Full name" aria-invalid={!!error} aria-describedby={error ? "employee-name-error" : undefined} onChange={(event) => { setName(event.target.value); setError(null); }} />
        <Button type="submit" disabled={pending}><Icon name="plus" />{pending ? "Adding…" : "Add employee"}</Button>
      </div>
      {error && <p id="employee-name-error" className="field-error">{error}</p>}
    </form>
  );
}

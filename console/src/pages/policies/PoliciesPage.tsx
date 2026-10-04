import { useMemo, useState } from "react";
import { hasRole, useUser } from "../../auth";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Icon } from "../../components/Icon";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Switch } from "../../components/ui/Switch";
import { errorMessage } from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import { useResource } from "../../lib/useResource";
import { policiesApi, type Policy, type PolicyWrite } from "./policiesApi";
import { PolicyForm } from "./PolicyForm";
import { ruleSummary, typeLabel } from "./policyRules";

const TYPE_FILTERS = ["all", "ai", "authority", "limit", "regex"] as const;
type TypeFilter = (typeof TYPE_FILTERS)[number];
const TYPE_FILTER_LABELS: Record<TypeFilter, string> = { all: "All types", ai: "AI", authority: "Authority", limit: "Limit", regex: "Regex" };
const APPLIES_LABELS = { prompt: "Prompt", response: "Response", both: "Both" } as const;

export function PoliciesPage() {
  const canEdit = hasRole(useUser(), "admin");
  const list = useResource(policiesApi.list, []);
  const [query, setQuery] = useState("");
  const [type, setType] = useState<TypeFilter>("all");
  const [status, setStatus] = useState<"all" | "enabled" | "disabled">("all");
  const [editing, setEditing] = useState<Policy | "new">();
  const [disabling, setDisabling] = useState<Policy>();
  const [toggling, setToggling] = useState<string>();
  const [toggleError, setToggleError] = useState<string | null>(null);

  const policies = list.data?.policies;
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (policies ?? []).filter((policy) =>
      (type === "all" || (type === "ai" ? policy.ai : policy.kind === type))
      && (status === "all" || policy.enabled === (status === "enabled"))
      && (!needle || policy.code.toLowerCase().includes(needle) || ruleSummary(policy).toLowerCase().includes(needle)));
  }, [policies, query, type, status]);
  const enabledCount = policies?.filter((policy) => policy.enabled).length ?? 0;

  function applyWrite({ policy, version }: PolicyWrite) {
    list.update(({ policies: current }) => {
      const exists = current.some((item) => item.code === policy.code);
      const next = exists ? current.map((item) => (item.code === policy.code ? policy : item)) : [...current, policy];
      // Same order as the gateway: by code, byte order.
      return { version, policies: next.sort((a, b) => (a.code < b.code ? -1 : a.code > b.code ? 1 : 0)) };
    });
  }

  async function setEnabled(policy: Policy, enabled: boolean) {
    setToggling(policy.code);
    setToggleError(null);
    try {
      applyWrite(await policiesApi.update(policy.code, { enabled }));
    } finally {
      setToggling(undefined);
    }
  }

  function onToggle(policy: Policy, enabled: boolean) {
    if (!enabled) {
      setDisabling(policy);
      return;
    }
    setEnabled(policy, true).catch((error: unknown) => setToggleError(`${policy.code} was not enabled: ${errorMessage(error)}`));
  }

  return (
    <>
      <div className="page-heading page-heading-row">
        <div>
          <h1>Policies</h1>
          <p>Authority, limit, regex and AI policies. The gateway applies every change from the next request.</p>
        </div>
        <div className="btn-row">
          {list.data && <span className="badge" title="Every create and edit bumps one global version">Version {list.data.version}</span>}
          {canEdit && <Button onClick={() => setEditing("new")}><Icon name="plus" />New policy</Button>}
        </div>
      </div>

      {!canEdit && <p className="notice"><Icon name="eye" />You have read-only access. An administrator creates and edits policies.</p>}
      {list.error && (
        <div className="notice is-error" role="alert">
          <Icon name="alert" /><span>{list.error}</span>
          <Button variant="outline" size="sm" className="ml-auto" onClick={list.reload}>Retry</Button>
        </div>
      )}
      {toggleError && <p className="notice is-error" role="alert"><Icon name="alert" />{toggleError}</p>}

      {list.loading && !list.data && <p className="panel empty-state" role="status">Loading policies…</p>}
      {policies && (
        <section className="panel" aria-labelledby="policies-title">
          <header className="panel-header filter-bar">
            <h2 id="policies-title">
              All policies<span className="count">{policies.length} total, {enabledCount} enabled</span>
            </h2>
            <div className="filters">
              <Input type="search" aria-label="Search policies" placeholder="Search code or rule" className="filter-search" value={query} onChange={(event) => setQuery(event.target.value)} />
              <select className="select" aria-label="Type" value={type} onChange={(event) => setType(event.target.value as TypeFilter)}>
                {TYPE_FILTERS.map((filter) => <option key={filter} value={filter}>{TYPE_FILTER_LABELS[filter]}</option>)}
              </select>
              <select className="select" aria-label="Status" value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
                <option value="all">Any status</option>
                <option value="enabled">Enabled</option>
                <option value="disabled">Disabled</option>
              </select>
            </div>
          </header>
          {shown.length === 0 ? (
            <div className="empty-state">
              <h3>{policies.length ? "No policies match" : "No policies yet"}</h3>
              <p>{policies.length ? "Change the search or filters." : "The gateway seeds its default policies at startup."}</p>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="data-table policies-table">
                <caption className="sr-only">Policies{shown.length < policies.length ? `, ${shown.length} of ${policies.length} shown` : ""}</caption>
                <thead>
                  <tr>
                    <th scope="col">Code</th>
                    <th scope="col">Type</th>
                    <th scope="col">Rule</th>
                    <th scope="col">Action</th>
                    <th scope="col">Applies to</th>
                    <th scope="col">Enabled</th>
                    <th scope="col">Updated</th>
                    <th scope="col"><span className="sr-only">Details</span></th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((policy) => (
                    <tr key={policy.code} className={policy.enabled ? undefined : "is-disabled"}>
                      <td className="mono font-medium whitespace-nowrap">{policy.code}</td>
                      <td><span className={`badge${policy.ai ? " is-accent" : ""}`}>{typeLabel(policy)}</span></td>
                      <td className="rule-cell">
                        <span className={policy.kind === "regex" ? "mono" : undefined} title={ruleSummary(policy)}>{ruleSummary(policy)}</span>
                      </td>
                      <td><span className={`badge ${policy.action === "block" ? "is-danger" : "is-warning"}`}>{policy.action === "block" ? "Block" : "Edit"}</span></td>
                      <td>{APPLIES_LABELS[policy.applies_to]}</td>
                      <td>
                        {canEdit
                          ? <Switch checked={policy.enabled} disabled={toggling === policy.code} aria-label={`${policy.code} enabled`} onCheckedChange={(enabled) => onToggle(policy, enabled)} />
                          : <span className={`badge ${policy.enabled ? "is-success" : ""}`}>{policy.enabled ? "On" : "Off"}</span>}
                      </td>
                      <td><time dateTime={policy.updated_at}>{formatDateTime(policy.updated_at)}</time></td>
                      <td className="actions">
                        <Button variant="outline" size="sm" aria-label={`${canEdit ? "Edit" : "View"} ${policy.code}`} onClick={() => setEditing(policy)}>
                          {canEdit ? "Edit" : "View"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {editing && (
        <PolicyForm
          policy={editing === "new" ? undefined : editing}
          existingCodes={policies?.map((policy) => policy.code) ?? []}
          readOnly={!canEdit}
          onClose={() => setEditing(undefined)}
          onSaved={(result) => { applyWrite(result); setEditing(undefined); }}
        />
      )}
      {disabling && (
        <ConfirmDialog
          title={`Disable ${disabling.code}?`}
          description={disabling.kind === "authority"
            ? "While it is disabled, every valid employee token can call every model, whatever its team's authorized models."
            : "The gateway stops applying it from the next request. You can enable it again at any time."}
          confirmLabel="Disable"
          destructive
          onConfirm={async () => { await setEnabled(disabling, false); setDisabling(undefined); }}
          onClose={() => setDisabling(undefined)}
        />
      )}
    </>
  );
}

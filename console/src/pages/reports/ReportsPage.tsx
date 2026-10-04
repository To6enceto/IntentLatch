import { useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { hasRole, useUser } from "../../auth";
import { Icon } from "../../components/Icon";
import { Button, buttonVariants } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { formatDateTime } from "../../lib/format";
import { useResource } from "../../lib/useResource";
import { OutcomeBadge } from "../tests/RunView";
import { MaskedText, RecordDialog } from "./RecordDialog";
import { reportsApi, type Report, type ReportItem } from "./reportsApi";

const PRESETS = { "1h": 3600, "24h": 86_400, "7d": 604_800, "30d": 2_592_000 } as const;
type Preset = keyof typeof PRESETS;
const PRESET_LABELS: Record<Preset | "custom", string> = { "1h": "Last hour", "24h": "Last 24 hours", "7d": "Last 7 days", "30d": "Last 30 days", custom: "Custom" };
const PAGE = 50;

/** A Date as the value a datetime-local input shows, in the viewer's time zone. */
function localInput(date: Date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

export function ReportsPage() {
  const canRead = hasRole(useUser(), "analyst");
  if (!canRead) {
    return (
      <>
        <div className="page-heading">
          <h1>Reports</h1>
          <p>Blocked and edited prompts and responses over a time range, with each policy's reasoning.</p>
        </div>
        <p className="notice"><Icon name="eye" />Reports contain prompt and response text, so they need the analyst or administrator role.</p>
      </>
    );
  }
  return <ReportView />;
}

function ReportView() {
  const [params, setParams] = useSearchParams();
  const preset = (params.get("range") ?? "24h") as Preset | "custom";
  const [now, setNow] = useState(() => Date.now());

  // Relative ranges end at the moment they were applied.
  const { from, to } = useMemo(() => {
    const start = params.get("from");
    const end = params.get("to");
    if (preset === "custom" && start && end) return { from: new Date(start).toISOString(), to: new Date(end).toISOString() };
    const seconds = PRESETS[(preset in PRESETS ? preset : "24h") as Preset];
    return { from: new Date(now - seconds * 1000).toISOString(), to: new Date(now).toISOString() };
  }, [params, preset, now]);

  const report = useResource(() => reportsApi.report(from, to), [from, to]);
  const filters = {
    outcome: params.get("outcome") ?? "",
    direction: params.get("direction") ?? "",
    team: params.get("team") ?? "",
    policy: params.get("policy") ?? "",
    q: params.get("q") ?? "",
  };
  const [search, setSearch] = useState(filters.q);
  const [shown, setShown] = useState(PAGE);
  const [open, setOpen] = useState<ReportItem>();
  const [customFrom, setCustomFrom] = useState(() => localInput(new Date(from)));
  const [customTo, setCustomTo] = useState(() => localInput(new Date(to)));

  const items = useMemo(() => {
    const needle = filters.q.toLowerCase();
    return (report.data?.items ?? [])
      .filter((item) => (!filters.outcome || item.outcome === filters.outcome)
        && (!filters.direction || item.direction === filters.direction)
        && (!filters.team || item.team === filters.team)
        && (!filters.policy || item.policies.some((policy) => policy.code === filters.policy))
        && (!needle || item.source_masked.toLowerCase().includes(needle) || (item.rewritten_masked ?? "").toLowerCase().includes(needle)))
      .reverse();
  }, [report.data, filters.outcome, filters.direction, filters.team, filters.policy, filters.q]);

  function update(changes: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [name, value] of Object.entries(changes)) {
      if (value) next.set(name, value);
      else next.delete(name);
    }
    setShown(PAGE);
    setParams(next);
  }

  function choosePreset(next: Preset | "custom") {
    if (next === "custom") {
      update({ range: "custom", from: customFrom, to: customTo });
      return;
    }
    setNow(Date.now());
    update({ range: next === "24h" ? null : next, from: null, to: null });
  }

  function applyCustom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    update({ range: "custom", from: customFrom, to: customTo });
  }

  function applySearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    update({ q: search.trim() || null });
  }

  function downloadJson(data: Report) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const link = Object.assign(document.createElement("a"), { href: url, download: `intentlatch-report-${data.from}-${data.to}.json` });
    link.click();
    URL.revokeObjectURL(url);
  }

  const data = report.data;
  const totals = data?.totals;
  const flagged = (direction: "prompt" | "response") => data?.items.filter((item) => item.direction === direction).length ?? 0;
  const customInvalid = preset === "custom" && customFrom >= customTo;
  const filtered = Boolean(filters.outcome || filters.direction || filters.team || filters.policy || filters.q);

  return (
    <>
      <div className="page-heading page-heading-row">
        <div>
          <h1>Reports</h1>
          <p>Blocked and edited prompts and responses over a time range, with each policy's reasoning. Sensitive values are masked before they are stored.</p>
        </div>
        <div className="btn-row">
          <a className={buttonVariants({ variant: "outline" })} href={reportsApi.itemsCsvUrl(from, to)} download><Icon name="reports" />Items CSV</a>
          <a className={buttonVariants({ variant: "outline" })} href={reportsApi.totalsCsvUrl(from, to)} download>Totals CSV</a>
          <Button variant="outline" disabled={!data} onClick={() => data && downloadJson(data)}>JSON</Button>
        </div>
      </div>

      <div className="report-filters" role="search" aria-label="Report filters">
        <div className="filter-row">
          <div className="segmented" role="group" aria-label="Time range">
            {(["1h", "24h", "7d", "30d", "custom"] as const).map((option) => (
              <button key={option} type="button" aria-pressed={option === preset} onClick={() => choosePreset(option)}>{PRESET_LABELS[option]}</button>
            ))}
          </div>
          {preset !== "custom" && (
            <Button variant="ghost" size="sm" onClick={() => setNow(Date.now())}><Icon name="refresh" className="size-3.5" />Refresh</Button>
          )}
          <p className="field-hint">
            <time dateTime={from}>{formatDateTime(from)}</time> to <time dateTime={to}>{formatDateTime(to)}</time>
            {" "}· exports cover the whole range
          </p>
        </div>
        {preset === "custom" && (
          <form className="filter-row" onSubmit={applyCustom}>
            <label className="inline-field">From <Input type="datetime-local" value={customFrom} max={customTo} onChange={(event) => setCustomFrom(event.target.value)} /></label>
            <label className="inline-field">To <Input type="datetime-local" value={customTo} min={customFrom} onChange={(event) => setCustomTo(event.target.value)} /></label>
            <Button type="submit" size="sm" disabled={customInvalid}>Apply range</Button>
            {customInvalid && <p className="field-error">The end must be after the start.</p>}
          </form>
        )}
        <div className="filter-row">
          <select className="select" aria-label="Outcome" value={filters.outcome} onChange={(event) => update({ outcome: event.target.value || null })}>
            <option value="">Blocked and edited</option>
            <option value="blocked">Blocked</option>
            <option value="edited">Edited</option>
          </select>
          <select className="select" aria-label="Checked" value={filters.direction} onChange={(event) => update({ direction: event.target.value || null })}>
            <option value="">Prompts and responses</option>
            <option value="prompt">Prompts</option>
            <option value="response">Responses</option>
          </select>
          <select className="select" aria-label="Team" value={filters.team} onChange={(event) => update({ team: event.target.value || null })}>
            <option value="">Every team</option>
            {totals?.by_team.map((row) => <option key={row.team} value={row.team}>{row.team}</option>)}
          </select>
          <select className="select" aria-label="Policy" value={filters.policy} onChange={(event) => update({ policy: event.target.value || null })}>
            <option value="">Any policy</option>
            {totals?.by_policy.map((row) => <option key={row.code} value={row.code}>{row.code}</option>)}
          </select>
          <form className="search-form" onSubmit={applySearch}>
            <Input type="search" aria-label="Search the stored text" placeholder="Search the stored text" value={search} onChange={(event) => setSearch(event.target.value)} />
            <Button type="submit" variant="outline" size="sm">Search</Button>
          </form>
        </div>
      </div>

      {report.error && (
        <div className="notice is-error" role="alert">
          <Icon name="alert" /><span>{report.error}</span>
          <Button variant="outline" size="sm" className="ml-auto" onClick={report.reload}>Retry</Button>
        </div>
      )}
      {!data && report.loading && <p className="panel empty-state" role="status">Loading the report…</p>}
      {data && totals && (
        <div className={report.loading ? "metrics-body is-refreshing" : "metrics-body"}>
          <div className="stat-row">
            <div className="stat"><p className="stat-label">Flagged</p><p className="stat-value">{totals.total.toLocaleString()}</p><p className="stat-detail">blocked or edited</p></div>
            <div className="stat"><p className="stat-label">Blocked</p><p className="stat-value">{totals.blocked.toLocaleString()}</p><p className="stat-detail">prompts and responses</p></div>
            <div className="stat"><p className="stat-label">Edited</p><p className="stat-value">{totals.edited.toLocaleString()}</p><p className="stat-detail">rewritten by the control agent</p></div>
            <div className="stat"><p className="stat-label">Flagged prompts</p><p className="stat-value">{flagged("prompt").toLocaleString()}</p><p className="stat-detail">before the model</p></div>
            <div className="stat"><p className="stat-label">Flagged responses</p><p className="stat-value">{flagged("response").toLocaleString()}</p><p className="stat-detail">after the model</p></div>
          </div>
          {totals.total > 0 && (
            <div className="report-tops">
              <section className="panel" aria-labelledby="top-policies-title">
                <header className="chart-card-header"><h2 id="top-policies-title">Policies that fired</h2></header>
                <ul className="top-list">
                  {totals.by_policy.slice(0, 10).map((row) => (
                    <li key={row.code}>
                      <button type="button" className="link-button mono" onClick={() => update({ policy: row.code })} aria-label={`Show items for ${row.code}`}>{row.code}</button>
                      <span>{row.blocked.toLocaleString()} blocked, {row.edited.toLocaleString()} edited</span>
                    </li>
                  ))}
                </ul>
              </section>
              <section className="panel" aria-labelledby="top-teams-title">
                <header className="chart-card-header"><h2 id="top-teams-title">Teams</h2></header>
                <ul className="top-list">
                  {totals.by_team.slice(0, 10).map((row) => (
                    <li key={row.team}>
                      <button type="button" className="link-button" onClick={() => update({ team: row.team })} aria-label={`Show items for team ${row.team}`}>{row.team}</button>
                      <span>{row.blocked.toLocaleString()} blocked, {row.edited.toLocaleString()} edited</span>
                    </li>
                  ))}
                </ul>
              </section>
            </div>
          )}
        </div>
      )}

      {data && (
        <section className="panel records-panel" aria-labelledby="records-title" aria-busy={report.loading}>
          <header className="panel-header">
            <h2 id="records-title">Items<span className="count">{items.length.toLocaleString()}{filtered ? ` of ${data.items.length.toLocaleString()}` : ""}</span></h2>
            {filtered && <Button variant="ghost" size="sm" onClick={() => { setSearch(""); update({ outcome: null, team: null, policy: null, q: null, direction: null }); }}>Clear filters</Button>}
          </header>
          {items.length === 0 ? (
            <div className="empty-state">
              <h3>Nothing to report</h3>
              <p>{data.items.length ? "No item matches these filters." : "Nothing was blocked or edited in this range."}</p>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="data-table records-table">
                <caption className="sr-only">Blocked and edited prompts and responses</caption>
                <thead>
                  <tr>
                    <th scope="col">Time</th>
                    <th scope="col">Who</th>
                    <th scope="col">Checked</th>
                    <th scope="col">Outcome</th>
                    <th scope="col">Policies</th>
                    <th scope="col">Stored text</th>
                  </tr>
                </thead>
                <tbody>
                  {items.slice(0, shown).map((item) => (
                    <tr key={`${item.request_id}-${item.direction}`}>
                      <td className="whitespace-nowrap">
                        <button type="button" className="link-button" onClick={() => setOpen(item)} aria-label={`Open the ${item.direction} from ${formatDateTime(item.ts)}`}>
                          <time dateTime={item.ts}>{formatDateTime(item.ts)}</time>
                        </button>
                      </td>
                      <td>
                        <span className="font-medium">{item.employee}</span>
                        <span className="cell-sub">{item.team} · <span className="mono">{item.model}</span></span>
                      </td>
                      <td>{item.direction === "prompt" ? "Prompt" : "Response"}</td>
                      <td><OutcomeBadge outcome={item.outcome} /></td>
                      <td>
                        <span className="stack-inline">
                          {item.policies.length ? item.policies.map((policy) => <code key={policy.code} className="code-tag">{policy.code}</code>)
                            : <span className="field-hint">Control agent failed</span>}
                        </span>
                      </td>
                      <td className="excerpt-cell"><span className="excerpt"><MaskedText text={item.source_masked} /></span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {items.length > shown && (
            <div className="panel-section load-more">
              <Button variant="outline" onClick={() => setShown(shown + PAGE)}>Show more</Button>
            </div>
          )}
        </section>
      )}

      {open && <RecordDialog item={open} onClose={() => setOpen(undefined)} />}
    </>
  );
}

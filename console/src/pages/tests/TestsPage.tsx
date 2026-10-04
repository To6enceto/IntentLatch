import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { hasRole, useUser } from "../../auth";
import { Icon } from "../../components/Icon";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { errorMessage } from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import { useResource, type Resource } from "../../lib/useResource";
import { CaseForm, type CaseFormMode } from "./CaseForm";
import { OutcomeBadge, ResultBadge, RunResults, RunSummary } from "./RunView";
import { findStartedRun, testsApi, useRunDetail, type CaseResult, type TestCase, type TestRun } from "./testsApi";

const RECENT_RUNS = 5;
type LastResult = CaseResult & { at: string };

/** Each case's latest result, from the newest runs; the gateway keeps results per run only. */
function useLastResults(runs: TestRun[] | undefined) {
  const [byCase, setByCase] = useState(new Map<string, LastResult>());
  const finished = runs?.filter((run) => run.status !== "running").slice(0, RECENT_RUNS) ?? [];
  const ids = finished.map((run) => run.id).join();
  useEffect(() => {
    if (!ids) return;
    let active = true;
    Promise.all(ids.split(",").map((id) => testsApi.getRun(id))).then((details) => {
      if (!active) return;
      const next = new Map<string, LastResult>();
      // Newest first, so the first result seen for a case is its latest.
      for (const run of details) for (const result of run.results ?? []) {
        if (!next.has(result.case_code)) next.set(result.case_code, { ...result, at: run.started_at });
      }
      setByCase(next);
    }, () => undefined);
    return () => { active = false; };
  }, [ids]);
  return byCase;
}

export function TestsPage() {
  const user = useUser();
  const canWrite = hasRole(user, "analyst");
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") === "history" ? "history" : "cases";
  const cases = useResource(testsApi.listCases, []);
  const runs = useResource(testsApi.listRuns, []);
  const lastResults = useLastResults(runs.data);
  const [watchedId, setWatchedId] = useState<string>();
  const [scope, setScope] = useState<Set<string> | "all">();
  const [starting, setStarting] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const watched = useRunDetail(watchedId);
  const [form, setForm] = useState<CaseFormMode>();
  const running = starting || watched.run?.status === "running";

  // Show the latest run when the page opens; it may still be running, started by anyone.
  const latest = runs.data?.[0];
  useEffect(() => {
    if (!watchedId && latest) setWatchedId(latest.id);
  }, [latest, watchedId]);

  // Keep the history entry in step with each poll, and refresh history when a run this page saw running ends.
  const watchedRun = watched.run;
  const sawRunning = useRef<string | undefined>(undefined);
  const runsUpdate = runs.update;
  const reloadRuns = runs.reload;
  useEffect(() => {
    if (!watchedRun) return;
    const { results: _results, ...summary } = watchedRun;
    runsUpdate((list) => (list.some((item) => item.id === summary.id)
      ? list.map((item) => (item.id === summary.id ? summary : item))
      : [summary, ...list]));
    if (watchedRun.status === "running") sawRunning.current = watchedRun.id;
    else if (sawRunning.current === watchedRun.id) {
      sawRunning.current = undefined;
      reloadRuns();
    }
  }, [watchedRun, runsUpdate, reloadRuns]);

  const live = useMemo(() => new Map((watched.run?.results ?? []).map((result) => [result.case_code, result])), [watched.run]);
  const names = useMemo(() => new Map((cases.data ?? []).map((testCase) => [testCase.code, testCase.name])), [cases.data]);

  async function run(codes?: string[]) {
    setStarting(true);
    setRunError(null);
    setScope(codes ? new Set(codes) : "all");
    const since = Date.now();
    // The POST answers when the run ends, so find the run meanwhile and poll it for live results.
    let settled = false;
    const finished = testsApi.run(user.username, codes).finally(() => { settled = true; });
    try {
      while (!settled) {
        const started = await findStartedRun(user.username, since);
        if (started) {
          setWatchedId(started.id);
          setStarting(false);
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 400));
      }
      const final = await finished;
      setWatchedId(final.id);
      reloadRuns();
    } catch (error) {
      setRunError(errorMessage(error));
    } finally {
      setStarting(false);
    }
  }

  function selectTab(next: "cases" | "history", runId?: string) {
    setSearchParams(next === "history" ? { tab: "history", ...(runId ? { run: runId } : {}) } : {});
  }

  function saved(testCase: TestCase) {
    cases.update((list) => list.some((item) => item.code === testCase.code)
      ? list.map((item) => (item.code === testCase.code ? testCase : item))
      : [...list, testCase].sort((a, b) => (a.code < b.code ? -1 : 1)));
    setForm(undefined);
  }

  return (
    <>
      <div className="page-heading page-heading-row">
        <div>
          <h1>Test cases</h1>
          <p>Prompts with the decision the policies should reach. Runs go through the gateway's checks, the control agent included, and never call a corporate model.</p>
        </div>
        {canWrite && (
          <div className="btn-row">
            <Button variant="outline" onClick={() => setForm({ kind: "new" })}><Icon name="plus" />New test case</Button>
            <Button onClick={() => void run()} disabled={running || !cases.data?.length}><Icon name="tests" />{running ? "Running…" : "Run all"}</Button>
          </div>
        )}
      </div>

      {!canWrite && <p className="notice"><Icon name="eye" />You have read-only access. An analyst or administrator adds and runs test cases.</p>}
      {runError && <p className="notice is-error" role="alert"><Icon name="alert" />The run failed: {runError}</p>}

      {watched.run && (
        <section className={`panel live-run${watched.run.status === "running" ? " is-running" : ""}`} aria-labelledby="live-run-title" aria-live="polite">
          <header className="panel-header">
            <h2 id="live-run-title">{watched.run.status === "running" ? "Run in progress" : "Latest run"}</h2>
            <Button variant="ghost" size="sm" onClick={() => selectTab("history", watched.run!.id)}>View results</Button>
          </header>
          <div className="panel-section"><RunSummary run={watched.run} /></div>
        </section>
      )}

      <div className="tabs" role="tablist" aria-label="Test case views">
        <button type="button" role="tab" id="tab-cases" aria-selected={tab === "cases"} aria-controls="panel-cases" onClick={() => selectTab("cases")}>
          Cases{cases.data && <span className="count">{cases.data.length}</span>}
        </button>
        <button type="button" role="tab" id="tab-history" aria-selected={tab === "history"} aria-controls="panel-history" onClick={() => selectTab("history")}>
          Run history{runs.data && <span className="count">{runs.data.length}</span>}
        </button>
      </div>

      {tab === "cases" ? (
        <div id="panel-cases" role="tabpanel" aria-labelledby="tab-cases">
          <CasesPanel
            cases={cases}
            canWrite={canWrite}
            running={running}
            live={live}
            lastResults={lastResults}
            pending={(code) => watched.run?.status === "running" && !live.has(code) && (scope === "all" || !!scope?.has(code))}
            onRun={(code) => void run([code])}
            onOpen={setForm}
          />
        </div>
      ) : (
        <div id="panel-history" role="tabpanel" aria-labelledby="tab-history">
          <HistoryPanel runs={runs} names={names} selectedId={searchParams.get("run") ?? undefined} />
        </div>
      )}

      {form && (
        <CaseForm
          key={`${form.kind}-${"testCase" in form ? form.testCase.code : ""}`}
          mode={form}
          canWrite={canWrite}
          existingCodes={cases.data?.map((testCase) => testCase.code) ?? []}
          onClose={() => setForm(undefined)}
          onSaved={saved}
          onDuplicate={(testCase) => setForm({ kind: "duplicate", testCase })}
        />
      )}
    </>
  );
}

type CasesPanelProps = {
  cases: Resource<TestCase[]>;
  canWrite: boolean;
  running: boolean;
  live: Map<string, CaseResult>;
  lastResults: Map<string, LastResult>;
  pending: (code: string) => boolean;
  onRun: (code: string) => void;
  onOpen: (mode: CaseFormMode) => void;
};

function CasesPanel({ cases, canWrite, running, live, lastResults, pending, onRun, onOpen }: CasesPanelProps) {
  const [query, setQuery] = useState("");
  const [origin, setOrigin] = useState<"all" | "predefined" | "custom">("all");
  const [result, setResult] = useState<"all" | "passed" | "failing" | "never">("all");

  const latestFor = (code: string): (CaseResult & { at?: string }) | undefined => live.get(code) ?? lastResults.get(code);
  const shown = (cases.data ?? []).filter((testCase) => {
    const needle = query.trim().toLowerCase();
    const last = latestFor(testCase.code);
    return (origin === "all" || testCase.predefined === (origin === "predefined"))
      && (result === "all" || (result === "never" ? !last : result === "passed" ? !!last?.passed : !!last && !last.passed))
      && (!needle || [testCase.name, testCase.code, testCase.prompt, testCase.expected_policy_code ?? ""].some((text) => text.toLowerCase().includes(needle)));
  });

  if (cases.error) {
    return (
      <div className="notice is-error" role="alert">
        <Icon name="alert" /><span>{cases.error}</span>
        <Button variant="outline" size="sm" className="ml-auto" onClick={cases.reload}>Retry</Button>
      </div>
    );
  }
  if (!cases.data) return <p className="panel empty-state" role="status">Loading test cases…</p>;

  return (
    <section className="panel" aria-label="Test cases">
      <header className="panel-header filter-bar">
        <p className="field-hint">{cases.data.filter((item) => item.predefined).length} predefined, {cases.data.filter((item) => !item.predefined).length} custom</p>
        <div className="filters">
          <Input type="search" aria-label="Search test cases" placeholder="Search name, code, prompt or policy" className="filter-search" value={query} onChange={(event) => setQuery(event.target.value)} />
          <select className="select" aria-label="Origin" value={origin} onChange={(event) => setOrigin(event.target.value as typeof origin)}>
            <option value="all">All cases</option>
            <option value="predefined">Predefined</option>
            <option value="custom">Custom</option>
          </select>
          <select className="select" aria-label="Last result" value={result} onChange={(event) => setResult(event.target.value as typeof result)}>
            <option value="all">Any result</option>
            <option value="passed">Passed</option>
            <option value="failing">Failed or error</option>
            <option value="never">No recent run</option>
          </select>
        </div>
      </header>
      {shown.length === 0 ? (
        <div className="empty-state">
          <h3>{cases.data.length ? "No cases match" : "No test cases yet"}</h3>
          <p>{cases.data.length ? "Change the search or filters." : "The gateway adds its predefined cases at startup."}</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="data-table cases-table">
            <caption className="sr-only">Test cases</caption>
            <thead>
              <tr>
                <th scope="col">Case</th>
                <th scope="col">Expects</th>
                <th scope="col">Last result</th>
                <th scope="col"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {shown.map((testCase) => {
                const last = latestFor(testCase.code);
                return (
                  <tr key={testCase.code}>
                    <td className="case-cell">
                      <button type="button" className="link-button" onClick={() => onOpen({ kind: canWrite ? "edit" : "view", testCase })}>{testCase.name}</button>
                      <span className="cell-sub">
                        <span className={`badge ${testCase.predefined ? "" : "is-accent"}`}>{testCase.predefined ? "Predefined" : "Custom"}</span>
                        <span className="mono">{testCase.code}</span> · {testCase.model}
                      </span>
                    </td>
                    <td>
                      <span className="stack-inline">
                        <OutcomeBadge outcome={testCase.expected} />
                        {testCase.expected_policy_code && <code className="code-tag">{testCase.expected_policy_code}</code>}
                      </span>
                    </td>
                    <td>
                      {pending(testCase.code) ? <span className="badge is-accent"><span className="badge-dot pulse" />Queued</span>
                        : last ? (
                          <>
                            <span className="stack-inline"><ResultBadge result={last} /><span className="field-hint">got</span><OutcomeBadge outcome={last.actual} /></span>
                            {last.failure && <span className="cell-sub">{last.failure}</span>}
                            {last.at && <span className="cell-sub"><time dateTime={last.at}>{formatDateTime(last.at)}</time></span>}
                          </>
                        ) : <span className="field-hint">No recent run</span>}
                    </td>
                    <td className="actions">
                      {canWrite && (
                        <div className="btn-row">
                          <Button variant="outline" size="sm" disabled={running} aria-label={`Run ${testCase.name}`} onClick={() => onRun(testCase.code)}>
                            <Icon name="tests" className="size-3.5" />Run
                          </Button>
                          <Button variant="ghost" size="sm" aria-label={`Duplicate ${testCase.name}`} onClick={() => onOpen({ kind: "duplicate", testCase })}>
                            <Icon name="copy" className="size-3.5" />Duplicate
                          </Button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function HistoryPanel({ runs, names, selectedId }: { runs: Resource<TestRun[]>; names: Map<string, string>; selectedId?: string }) {
  const selected = selectedId ?? runs.data?.[0]?.id;
  const { run, error } = useRunDetail(selected);

  if (runs.error) {
    return (
      <div className="notice is-error" role="alert">
        <Icon name="alert" /><span>{runs.error}</span>
        <Button variant="outline" size="sm" className="ml-auto" onClick={runs.reload}>Retry</Button>
      </div>
    );
  }
  if (!runs.data) return <p className="panel empty-state" role="status">Loading run history…</p>;
  if (!runs.data.length) {
    return (
      <div className="panel empty-state">
        <h3>No runs yet</h3>
        <p>Run all cases, or a single one, to see its results here.</p>
      </div>
    );
  }

  return (
    <div className="teams-layout">
      <nav className="panel teams-list" aria-labelledby="runs-title">
        <header className="panel-header"><h2 id="runs-title">Runs<span className="count">latest {runs.data.length}</span></h2></header>
        <ul>
          {runs.data.map((item) => (
            <li key={item.id}>
              <Link to={{ search: `?tab=history&run=${encodeURIComponent(item.id)}` }} className="team-item" aria-current={item.id === selected ? "page" : undefined}>
                <span className="team-name"><time dateTime={item.started_at}>{formatDateTime(item.started_at)}</time></span>
                <span className="team-models">
                  {item.status === "running" ? <span className="badge is-accent">Running</span>
                    : item.status === "failed" ? <span className="badge is-danger">{item.totals.failed} of {item.totals.total} failing</span>
                      : <span className="badge is-success">{item.totals.total} passed</span>}
                  <span className="field-hint">{item.started_by}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <section className="panel" aria-label="Run results">
        {error && <div className="panel-section"><p className="notice is-error" role="alert"><Icon name="alert" />{error}</p></div>}
        {!run ? <p className="empty-state" role="status">Loading results…</p> : (
          <>
            <header className="panel-header"><h2>Run of <time dateTime={run.started_at}>{formatDateTime(run.started_at)}</time></h2></header>
            <div className="panel-section"><RunSummary run={run} /></div>
            <RunResults run={run} names={names} />
          </>
        )}
      </section>
    </div>
  );
}

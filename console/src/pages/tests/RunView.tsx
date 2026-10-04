import { formatDateTime } from "../../lib/format";
import { progress, type Actual, type CaseResult, type TestRun } from "./testsApi";

type AnyOutcome = Actual | "allowed" | "edited" | "blocked";
const OUTCOME: Record<AnyOutcome, { label: string; tone: string }> = {
  ALLOW: { label: "Allow", tone: "is-success" }, allowed: { label: "Allowed", tone: "is-success" },
  EDIT: { label: "Edit", tone: "is-warning" }, edited: { label: "Edited", tone: "is-warning" },
  BLOCK: { label: "Block", tone: "is-danger" }, blocked: { label: "Blocked", tone: "is-danger" },
  ERROR: { label: "Error", tone: "" },
};

/** A runner outcome (ALLOW, EDIT, BLOCK, ERROR) or a decision log outcome (allowed, edited, blocked). */
export function OutcomeBadge({ outcome }: { outcome: AnyOutcome }) {
  const { label, tone } = OUTCOME[outcome];
  return <span className={`badge ${tone}`}>{label}</span>;
}

export function ResultBadge({ result }: { result: Pick<CaseResult, "passed" | "actual"> }) {
  if (result.actual === "ERROR") return <span className="badge is-warning"><span className="badge-dot" />Error</span>;
  return result.passed
    ? <span className="badge is-success"><span className="badge-dot" />Passed</span>
    : <span className="badge is-danger"><span className="badge-dot" />Failed</span>;
}

function duration(run: TestRun) {
  if (!run.finished_at) return null;
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime();
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** Progress, counts and who started a run; used for the live banner and the history detail. */
export function RunSummary({ run }: { run: TestRun }) {
  const { done, passed, failed, total } = progress(run);
  return (
    <div className="run-summary">
      <div className="run-counts">
        {run.status === "running" && <span className="badge is-accent"><span className="badge-dot pulse" />Running {done} of {total}</span>}
        <span className="badge is-success">{passed} passed</span>
        <span className={`badge ${failed ? "is-danger" : ""}`}>{failed} failed</span>
      </div>
      <div className="progress" role="progressbar" aria-label="Run progress" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
        <span className="progress-pass" style={{ width: `${total ? (passed / total) * 100 : 0}%` }} />
        <span className="progress-fail" style={{ width: `${total ? (failed / total) * 100 : 0}%` }} />
      </div>
      <p className="field-hint">
        {total} {total === 1 ? "case" : "cases"} · policy version {run.policy_version} · started by {run.started_by}{" "}
        <time dateTime={run.started_at}>{formatDateTime(run.started_at)}</time>
        {duration(run) && ` · took ${duration(run)}`}
      </p>
    </div>
  );
}

export function RunResults({ run, names }: { run: TestRun; names: Map<string, string> }) {
  const results = run.results ?? [];
  const waiting = run.status === "running" ? run.totals.total - results.length : 0;
  return (
    <div className="table-wrap">
      <table className="data-table results-table">
        <caption className="sr-only">Results of the run started {formatDateTime(run.started_at)}</caption>
        <thead>
          <tr>
            <th scope="col">Case</th>
            <th scope="col">Expected</th>
            <th scope="col">Actual</th>
            <th scope="col">Policies</th>
            <th scope="col">Result</th>
            <th scope="col">Time</th>
          </tr>
        </thead>
        <tbody>
          {results.map((result) => (
            <tr key={result.case_code}>
              <td>
                <span className="font-medium">{names.get(result.case_code) ?? result.case_code}</span>
                <span className="cell-sub mono">{result.case_code}</span>
              </td>
              <td><OutcomeBadge outcome={result.expected} /></td>
              <td><OutcomeBadge outcome={result.actual} /></td>
              <td>
                <span className="stack-inline">
                  {result.fired_policy_codes.length ? result.fired_policy_codes.map((code) => <code key={code} className="code-tag">{code}</code>) : <span className="field-hint">None</span>}
                </span>
              </td>
              <td>
                <ResultBadge result={result} />
                {result.failure && <span className="cell-sub">{result.failure}</span>}
              </td>
              <td className="whitespace-nowrap">{result.duration_ms.toFixed(0)} ms</td>
            </tr>
          ))}
          {waiting > 0 && (
            <tr><td colSpan={6} className="field-hint" role="status">{waiting} more {waiting === 1 ? "case is" : "cases are"} running…</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

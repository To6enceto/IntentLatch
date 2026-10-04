import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { Icon } from "../../components/Icon";
import { Button } from "../../components/ui/Button";
import { useResource } from "../../lib/useResource";
import { BarList, ChartCard, Legend, LineChart, StackedColumns, formatCount, formatSeconds } from "./charts";
import { RANGES, RANGE_LABELS, metricsApi, type Dashboard, type Outcome, type Range } from "./metricsApi";

const REFRESH_MS = 30_000;

// Each entity keeps one color everywhere: a blocked request and a block action share slot 2.
const OUTCOMES: { key: Outcome; label: string; color: string }[] = [
  { key: "allowed", label: "Allowed", color: "var(--series-1)" },
  { key: "blocked", label: "Blocked", color: "var(--series-2)" },
  { key: "edited", label: "Edited", color: "var(--series-3)" },
  { key: "error", label: "Error", color: "var(--series-4)" },
];
const ACTIONS = [
  { key: "block", label: "Block", color: "var(--series-2)" },
  { key: "edit", label: "Edit", color: "var(--series-3)" },
] as const;
const STAGE_LABELS: Record<string, string> = {
  total: "Whole request", auth: "Identity check", non_ai: "Non-AI policies", control_agent: "Control agent", upstream: "Model",
};
const REASON_LABELS: Record<string, string> = {
  missing: "No employee token", invalid: "Invalid employee token", revoked: "Revoked employee token",
  model_not_authorized: "Model not authorized for the team", admin_key_invalid: "Wrong admin API key",
  login_failed: "Failed console sign-in",
};
const VERDICT_LABELS: Record<string, string> = { pass: "Passed", blocked: "Found a violation", modified: "Rewrote the text" };

function timeFormats(range: Range) {
  const long = new Intl.DateTimeFormat(undefined, { weekday: range === "7d" ? "short" : undefined, hour: "2-digit", minute: "2-digit" });
  const tick = range === "7d"
    ? new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric" })
    : new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
  return {
    formatTime: (seconds: number) => long.format(new Date(seconds * 1000)),
    formatTick: (seconds: number) => tick.format(new Date(seconds * 1000)),
  };
}

function percent(part: number, whole: number) {
  return whole ? `${Math.round((part / whole) * 100)}%` : "0%";
}

function Stat({ label, value, detail }: { label: string; value: string; detail?: ReactNode }) {
  return (
    <div className="stat">
      <p className="stat-label">{label}</p>
      <p className="stat-value">{value}</p>
      {detail && <p className="stat-detail">{detail}</p>}
    </div>
  );
}

function SimpleTable({ caption, head, rows, empty }: { caption: string; head: string[]; rows: ReactNode[][]; empty: string }) {
  if (!rows.length) return <p className="empty-state compact">{empty}</p>;
  return (
    <div className="table-wrap">
      <table className="data-table numeric-table">
        <caption className="sr-only">{caption}</caption>
        <thead><tr>{head.map((cell) => <th key={cell} scope="col">{cell}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>{row.map((cell, column) => <td key={column}>{cell}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

export function MetricsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const range = (RANGES as readonly string[]).includes(searchParams.get("range") ?? "") ? searchParams.get("range") as Range : "1h";
  const dashboard = useResource(() => metricsApi.dashboard(range), [range]);
  const [updatedAt, setUpdatedAt] = useState<Date>();

  // Only a successful load is an update; a failed refresh keeps the previous time.
  useEffect(() => {
    if (dashboard.data && !dashboard.loading && !dashboard.error) setUpdatedAt(new Date());
  }, [dashboard.data, dashboard.loading, dashboard.error]);

  useEffect(() => {
    const timer = setInterval(dashboard.reload, REFRESH_MS);
    return () => clearInterval(timer);
  }, [dashboard.reload]);

  const data = dashboard.data;
  const notConfigured = !data && dashboard.error && dashboard.error.includes("INTENTLATCH_PROMETHEUS_URL");

  return (
    <>
      <div className="page-heading">
        <h1>Metrics</h1>
        <p>Gateway requests, policy decisions and response times, read from Prometheus through the gateway.</p>
      </div>

      <div className="filter-row" role="group" aria-label="Time range">
        <div className="segmented">
          {RANGES.map((option) => (
            <button key={option} type="button" aria-pressed={option === range} onClick={() => setSearchParams(option === "1h" ? {} : { range: option })}>
              {RANGE_LABELS[option]}
            </button>
          ))}
        </div>
        <Button variant="ghost" size="sm" onClick={dashboard.reload} disabled={dashboard.loading}><Icon name="refresh" className="size-3.5" />Refresh</Button>
        <p className="field-hint" aria-live="polite">
          {dashboard.loading ? "Updating…" : updatedAt ? `Updated ${updatedAt.toLocaleTimeString()} · refreshes every 30 s` : ""}
        </p>
      </div>

      {dashboard.error && (
        <div className={`notice${notConfigured ? "" : " is-error"}`} role="alert">
          <Icon name="alert" />
          <span>
            {notConfigured
              ? <>Metrics need Prometheus. Set <code className="mono">INTENTLATCH_PROMETHEUS_URL</code> on the gateway to the Prometheus that scrapes its <code className="mono">/metrics</code>.</>
              : <>{dashboard.error}{data ? " Showing the last numbers that loaded." : ""}</>}
          </span>
          <Button variant="outline" size="sm" className="ml-auto" onClick={dashboard.reload}>Retry</Button>
        </div>
      )}
      {!data && dashboard.loading && <p className="panel empty-state" role="status">Loading metrics…</p>}
      {data && <MetricsBody data={data} range={range} refreshing={dashboard.loading} />}
    </>
  );
}

function MetricsBody({ data, range, refreshing }: { data: Dashboard; range: Range; refreshing: boolean }) {
  const { formatTime, formatTick } = timeFormats(range);
  const requests = data.requests;
  const stepLabel = data.step_seconds >= 3600 ? `${data.step_seconds / 3600} h` : `${data.step_seconds / 60} min`;
  const outcomes = OUTCOMES.filter((outcome) => outcome.key !== "edited" || requests.edited > 0 || data.series.requests.edited);
  const requestSeries = outcomes.map((outcome) => ({ ...outcome, values: data.series.requests[outcome.key] ?? data.series.timestamps.map(() => 0) }));
  const enforcementsTotal = data.top_policies.reduce((sum, row) => sum + row.total, 0);
  const total = data.latency.find((row) => row.stage === "total");
  const tokensByTeam = new Map(data.tokens.map((row) => [row.team, row]));
  const tests = data.test_cases;

  return (
    <div className={refreshing ? "metrics-body is-refreshing" : "metrics-body"}>
      <p className="field-hint metrics-meta">
        {data.instances === 0
          ? "No gateway instance is reporting to Prometheus right now."
          : `${data.instances} gateway ${data.instances === 1 ? "instance" : "instances"} reporting · ${data.policies_active ?? "?"} enabled policies`}
      </p>
      <div className="stat-row">
        <Stat label="Model requests" value={formatCount(requests.total)} detail={RANGE_LABELS[range].toLowerCase()} />
        <Stat label="Blocked" value={formatCount(requests.blocked)} detail={`${percent(requests.blocked, requests.total)} of requests`} />
        <Stat label="Edited" value={formatCount(requests.edited)} detail={`${percent(requests.edited, requests.total)} of requests`} />
        <Stat label="Errors" value={formatCount(data.upstream_errors + data.control_agent.errors)} detail={`${formatCount(data.upstream_errors)} model, ${formatCount(data.control_agent.errors)} control agent`} />
        <Stat label="Response time, p95" value={formatSeconds(total?.p95)} detail={`median ${formatSeconds(total?.p50)}`} />
        <Stat label="Rejected credentials" value={formatCount(data.auth_failures.reduce((sum, row) => sum + row.count, 0))} detail="tokens, keys and sign-ins" />
      </div>

      <div className="metrics-grid">
        <ChartCard
          id="requests"
          className="span-2"
          title="Requests by outcome"
          subtitle={`Model requests per ${stepLabel}`}
          legend={<Legend items={outcomes} />}
          table={
            <SimpleTable
              caption="Requests by outcome per interval"
              head={["Time", ...outcomes.map((outcome) => outcome.label)]}
              rows={data.series.timestamps.map((time, index) => [formatTime(time), ...requestSeries.map((item) => formatCount(item.values[index] ?? 0))]).reverse()}
              empty="No requests in this range."
            />
          }
        >
          <StackedColumns timestamps={data.series.timestamps} series={requestSeries} formatTime={formatTime} formatTick={formatTick} label="Requests by outcome" />
        </ChartCard>

        <ChartCard
          id="policies"
          title="Policies that block and edit most"
          subtitle={enforcementsTotal ? `${formatCount(enforcementsTotal)} violations by the top ${data.top_policies.length}` : "Violated policies on model requests"}
          legend={<Legend items={[...ACTIONS]} />}
          table={
            <SimpleTable
              caption="Violations per policy"
              head={["Policy", "Kind", "Block", "Edit", "Total"]}
              rows={data.top_policies.map((row) => [<span className="mono">{row.policy_code}</span>, row.kind ?? "", formatCount(row.block), formatCount(row.edit), formatCount(row.total)])}
              empty="No policy was violated in this range."
            />
          }
        >
          {data.top_policies.length ? (
            <BarList
              label="Violations per policy"
              rows={data.top_policies.map((row) => ({
                key: row.policy_code,
                label: row.policy_code,
                segments: ACTIONS.map((action) => ({ ...action, value: row[action.key] })),
              }))}
            />
          ) : <p className="empty-state compact">No policy was violated in this range.</p>}
        </ChartCard>

        <ChartCard
          id="latency"
          title="Response time, p95"
          subtitle="Whole request, including the model"
          table={
            <SimpleTable
              caption="p95 response time per interval"
              head={["Time", "p95"]}
              rows={data.series.timestamps.map((time, index) => [formatTime(time), formatSeconds(data.series.latency_p95[index])]).reverse()}
              empty="No timed requests in this range."
            />
          }
        >
          <LineChart
            timestamps={data.series.timestamps}
            values={data.series.latency_p95}
            color="var(--series-1)"
            formatValue={(value) => formatSeconds(value)}
            formatTime={formatTime}
            formatTick={formatTick}
            label="p95 response time"
            valueLabel="p95"
          />
        </ChartCard>

        <section className="panel" aria-labelledby="teams-title">
          <header className="chart-card-header"><h2 id="teams-title">Teams</h2></header>
          <SimpleTable
            caption="Requests and tokens by team"
            head={["Team", "Requests", "Blocked", "Errors", "Tokens"]}
            rows={data.teams.map((row) => [row.team, formatCount(row.total), `${formatCount(row.blocked)} (${percent(row.blocked, row.total)})`, formatCount(row.error), formatCount(tokensByTeam.get(row.team)?.total ?? 0)])}
            empty="No requests in this range."
          />
        </section>

        <section className="panel" aria-labelledby="models-title">
          <header className="chart-card-header"><h2 id="models-title">Models</h2></header>
          <SimpleTable
            caption="Requests by model"
            head={["Model", "Requests", "Allowed", "Blocked", "Errors"]}
            rows={data.models.map((row) => [<span className="mono">{row.model}</span>, formatCount(row.total), formatCount(row.allowed), formatCount(row.blocked), formatCount(row.error)])}
            empty="No requests in this range."
          />
        </section>

        <section className="panel" aria-labelledby="stages-title">
          <header className="chart-card-header"><h2 id="stages-title">Time per stage</h2></header>
          <SimpleTable
            caption="Response time by stage"
            head={["Stage", "Median", "p95"]}
            rows={data.latency.map((row) => [STAGE_LABELS[row.stage] ?? row.stage, formatSeconds(row.p50), formatSeconds(row.p95)])}
            empty="No timed requests in this range."
          />
        </section>

        <section className="panel" aria-labelledby="agent-title">
          <header className="chart-card-header"><h2 id="agent-title">Control agent</h2></header>
          <SimpleTable
            caption="Control agent verdicts"
            head={["Verdict", "Count"]}
            rows={[
              ...Object.entries(data.control_agent.verdicts).map(([status, count]) => [VERDICT_LABELS[status] ?? status, formatCount(count ?? 0)]),
              ...(data.control_agent.errors ? [["Failed", formatCount(data.control_agent.errors)]] : []),
            ]}
            empty="The control agent was not needed in this range."
          />
        </section>

        <section className="panel" aria-labelledby="auth-title">
          <header className="chart-card-header"><h2 id="auth-title">Rejected credentials</h2></header>
          <SimpleTable
            caption="Rejected credentials by reason"
            head={["Reason", "Count"]}
            rows={data.auth_failures.map((row) => [REASON_LABELS[row.reason] ?? row.reason, formatCount(row.count)])}
            empty="No rejected credentials in this range."
          />
          {(tests.passed || tests.failed || tests.error) ? (
            <p className="field-hint metrics-tests">
              Policy test cases in this range: {formatCount(tests.passed ?? 0)} passed, {formatCount(tests.failed ?? 0)} failed, {formatCount(tests.error ?? 0)} errors.
            </p>
          ) : null}
        </section>
      </div>
    </div>
  );
}

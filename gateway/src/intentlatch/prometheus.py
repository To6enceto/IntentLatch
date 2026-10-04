"""Reads the gateway's metrics back from Prometheus for the console's Metrics page.

The console never sends PromQL: every query is defined here, so a console user cannot
read the rest of the cluster's metrics through the gateway."""

import asyncio
import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from .errors import GatewayError

Range = Literal["1h", "6h", "24h", "7d"]
# Range length and chart step, in seconds.
RANGES: dict[str, tuple[int, int]] = {"1h": (3600, 60), "6h": (21600, 300), "24h": (86400, 900), "7d": (604800, 7200)}
TOP_POLICIES = 10
QUERY_TIMEOUT_SECONDS = 10
Sample = tuple[dict[str, str], float]
Series = tuple[dict[str, str], list[tuple[float, float]]]


def unavailable(message: str) -> GatewayError:
    return GatewayError(503, "metrics_unavailable", message)


def number(value: str) -> float | None:
    parsed = float(value)
    return None if math.isnan(parsed) or math.isinf(parsed) else parsed


async def api(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        response = await client.get(path, params=params, timeout=QUERY_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise unavailable("Prometheus is unreachable.") from exc
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("status") != "success":
        error = body.get("error") if isinstance(body, dict) else None
        raise unavailable(f"Prometheus rejected a query: {error or response.status_code}.")
    return body["data"]["result"]


async def instant(client: httpx.AsyncClient, promql: str, at: float) -> list[Sample]:
    result = await api(client, "/api/v1/query", {"query": promql, "time": at})
    samples = []
    for entry in result:
        value = number(entry["value"][1])
        if value is not None:
            samples.append((entry["metric"], value))
    return samples


async def ranged(client: httpx.AsyncClient, promql: str, start: float, end: float, step: int) -> list[Series]:
    result = await api(client, "/api/v1/query_range", {"query": promql, "start": start, "end": end, "step": step})
    return [
        (entry["metric"], [(float(t), v) for t, raw in entry["values"] if (v := number(raw)) is not None])
        for entry in result
    ]


def count(value: float) -> int:
    # increase() extrapolates, so counts arrive as fractions.
    return max(round(value), 0)


def grid(series: list[Series], label: str, timestamps: list[float]) -> dict[str, list[float | None]]:
    """One aligned list per label value; a missing point is 0 for counts."""
    lines: dict[str, list[float | None]] = {}
    for metric, points in series:
        by_time = dict(points)
        lines[metric.get(label, "")] = [round(by_time.get(t, 0.0), 2) for t in timestamps]
    return lines


async def dashboard(client: httpx.AsyncClient, range_key: str, now: float) -> dict[str, Any]:
    seconds, step = RANGES[range_key]
    end = math.floor(now / step) * step
    start = end - seconds
    window = f"{seconds}s"
    rate_window = f"{max(step, 120)}s"
    timestamps = [float(start + step * index) for index in range(seconds // step + 1)]

    def at_end(promql: str):
        return instant(client, promql, end)

    def over_time(promql: str):
        return ranged(client, promql, start, end, step)

    (
        outcomes, policies, teams, models, auth, tokens, p50, p95, upstream_errors, instances,
        active, tests, verdicts, agent_errors, requests_series, enforcement_series, latency_series,
    ) = await asyncio.gather(
        at_end(f"sum by (outcome) (increase(intentlatch_requests_total[{window}]))"),
        at_end(f"sum by (policy_code, kind, action) (increase(intentlatch_policy_enforcements_total[{window}]))"),
        at_end(f"sum by (team, outcome) (increase(intentlatch_requests_total[{window}]))"),
        at_end(f"sum by (model, outcome) (increase(intentlatch_requests_total[{window}]))"),
        at_end(f"sum by (reason) (increase(intentlatch_auth_failures_total[{window}]))"),
        at_end(f"sum by (team, direction) (increase(intentlatch_tokens_total[{window}]))"),
        at_end(f"histogram_quantile(0.5, sum by (le, stage) (rate(intentlatch_request_duration_seconds_bucket[{window}])))"),
        at_end(f"histogram_quantile(0.95, sum by (le, stage) (rate(intentlatch_request_duration_seconds_bucket[{window}])))"),
        at_end(f"sum(increase(intentlatch_upstream_errors_total[{window}]))"),
        # The gauge has one series per kind on each instance.
        at_end("count(count by (instance) (intentlatch_policies_active))"),
        at_end("sum(max by (kind) (intentlatch_policies_active))"),
        at_end(f"sum by (result) (increase(intentlatch_test_cases_total[{window}]))"),
        at_end(f"sum by (status) (increase(intentlatch_control_agent_verdicts_total[{window}]))"),
        at_end(f"sum(increase(intentlatch_control_agent_errors_total[{window}]))"),
        over_time(f"sum by (outcome) (increase(intentlatch_requests_total[{step}s]))"),
        over_time(f"sum by (action) (increase(intentlatch_policy_enforcements_total[{step}s]))"),
        over_time(
            f'histogram_quantile(0.95, sum by (le) (rate(intentlatch_request_duration_seconds_bucket{{stage="total"}}[{rate_window}])))'
        ),
    )

    requests = {"allowed": 0, "blocked": 0, "edited": 0, "error": 0}
    for metric, value in outcomes:
        requests[metric.get("outcome", "error")] = requests.get(metric.get("outcome", "error"), 0) + count(value)

    by_policy: dict[str, dict[str, Any]] = {}
    for metric, value in policies:
        entry = by_policy.setdefault(
            metric.get("policy_code", ""), {"policy_code": metric.get("policy_code", ""), "kind": metric.get("kind"), "block": 0, "edit": 0}
        )
        entry[metric.get("action", "block")] = entry.get(metric.get("action", "block"), 0) + count(value)
    top_policies = sorted(
        ({**entry, "total": entry["block"] + entry["edit"]} for entry in by_policy.values()),
        key=lambda entry: (-entry["total"], entry["policy_code"]),
    )
    top_policies = [entry for entry in top_policies if entry["total"] > 0][:TOP_POLICIES]

    def breakdown(samples: list[Sample], label: str) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = defaultdict(lambda: {"allowed": 0, "blocked": 0, "edited": 0, "error": 0})
        for metric, value in samples:
            rows[metric.get(label, "unknown")][metric.get("outcome", "error")] += count(value)
        result = [{label: name, **values, "total": sum(values.values())} for name, values in rows.items()]
        return sorted((row for row in result if row["total"]), key=lambda row: (-row["total"], row[label]))

    token_rows: dict[str, dict[str, int]] = defaultdict(lambda: {"prompt": 0, "response": 0})
    for metric, value in tokens:
        direction = "response" if metric.get("direction") == "response" else "prompt"
        token_rows[metric.get("team", "unknown")][direction] += count(value)

    latency: dict[str, dict[str, float | None]] = defaultdict(lambda: {"p50": None, "p95": None})
    for key, samples in (("p50", p50), ("p95", p95)):
        for metric, value in samples:
            latency[metric.get("stage", "total")][key] = value

    latency_points = dict(latency_series[0][1]) if latency_series else {}
    return {
        "range": range_key,
        "start": datetime.fromtimestamp(start, UTC).isoformat(),
        "end": datetime.fromtimestamp(end, UTC).isoformat(),
        "step_seconds": step,
        "instances": count(instances[0][1]) if instances else 0,
        "policies_active": count(active[0][1]) if active else None,
        "requests": {**requests, "total": sum(requests.values())},
        "upstream_errors": count(upstream_errors[0][1]) if upstream_errors else 0,
        "series": {
            "timestamps": timestamps,
            "requests": grid(requests_series, "outcome", timestamps),
            "enforcements": grid(enforcement_series, "action", timestamps),
            "latency_p95": [latency_points.get(t) for t in timestamps],
        },
        "top_policies": top_policies,
        "teams": breakdown(teams, "team"),
        "models": breakdown(models, "model"),
        "auth_failures": sorted(
            ({"reason": metric.get("reason", "unknown"), "count": count(value)} for metric, value in auth if count(value)),
            key=lambda row: -row["count"],
        ),
        "tokens": sorted(
            ({"team": team, **values, "total": values["prompt"] + values["response"]} for team, values in token_rows.items()),
            key=lambda row: -row["total"],
        ),
        "latency": [{"stage": stage, **values} for stage, values in sorted(latency.items())],
        "test_cases": {metric.get("result", "error"): count(value) for metric, value in tests},
        "control_agent": {
            "verdicts": {metric.get("status", "unknown"): count(value) for metric, value in verdicts if count(value)},
            "errors": count(agent_errors[0][1]) if agent_errors else 0,
        },
    }

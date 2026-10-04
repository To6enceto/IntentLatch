"""The console's Metrics proxy: fixed PromQL against Prometheus, shaped into panels."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from intentlatch import prometheus
from intentlatch.errors import GatewayError


# The proxy, against a fake Prometheus.

NOW = 1_800_000_030.0  # floors to 1_800_000_000 on a 60 s step


def fake_prometheus(answers: dict[str, list], status: str = "success") -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        query = req.url.params["query"]
        result = next((rows for fragment, rows in answers.items() if fragment in query), [])
        if status != "success":
            return httpx.Response(400, json={"status": "error", "error": "parse error"})
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    return httpx.AsyncClient(base_url="http://prometheus", transport=httpx.MockTransport(handler))


def sample(labels: dict, number: str) -> dict:
    return {"metric": labels, "value": [NOW, number]}


def test_dashboard_shapes_counts_top_policies_and_series():
    end = 1_800_000_000.0
    answers = {
        "sum by (outcome) (increase(intentlatch_requests_total[3600s]))": [
            sample({"outcome": "allowed"}, "10.4"), sample({"outcome": "blocked"}, "3.6")],
        "sum by (policy_code, kind, action)": [
            sample({"policy_code": "RGX-EMAIL", "kind": "regex", "action": "edit"}, "5"),
            sample({"policy_code": "RGX-CARD", "kind": "regex", "action": "edit"}, "0.2"),
            sample({"policy_code": "AUTH-MODEL", "kind": "authority", "action": "block"}, "7")],
        "sum by (team, outcome)": [sample({"team": "payments", "outcome": "blocked"}, "2"), sample({"team": "payments", "outcome": "allowed"}, "8")],
        "histogram_quantile(0.95, sum by (le, stage)": [sample({"stage": "total"}, "NaN"), sample({"stage": "upstream"}, "1.5")],
        "count(count by (instance) (intentlatch_policies_active))": [sample({}, "2")],
        "sum(max by (kind) (intentlatch_policies_active))": [sample({}, "13")],
        "sum by (team, direction) (increase(intentlatch_tokens_total": [
            sample({"team": "payments", "direction": "prompt"}, "120"), sample({"team": "payments", "direction": "response"}, "30")],
        "intentlatch_test_cases_total": [sample({"result": "passed"}, "16"), sample({"result": "failed"}, "1")],
        "intentlatch_control_agent_verdicts_total": [sample({"status": "pass"}, "9"), sample({"status": "modified"}, "3")],
        "sum by (outcome) (increase(intentlatch_requests_total[60s]))": [
            {"metric": {"outcome": "allowed"}, "values": [[end - 60, "1.5"], [end, "2"]]}],
    }
    client = fake_prometheus(answers)
    body = asyncio.run(prometheus.dashboard(client, "1h", NOW))
    assert body["requests"] == {"allowed": 10, "blocked": 4, "edited": 0, "error": 0, "total": 14}
    assert [row["policy_code"] for row in body["top_policies"]] == ["AUTH-MODEL", "RGX-EMAIL"]
    assert body["top_policies"][1] == {"policy_code": "RGX-EMAIL", "kind": "regex", "block": 0, "edit": 5, "total": 5}
    assert body["teams"] == [{"team": "payments", "allowed": 8, "blocked": 2, "edited": 0, "error": 0, "total": 10}]
    # A NaN quantile (no traffic for that stage) is dropped, not reported as a number.
    assert {row["stage"]: row["p95"] for row in body["latency"]} == {"upstream": 1.5}
    assert (body["instances"], body["policies_active"]) == (2, 13)
    assert body["tokens"] == [{"team": "payments", "prompt": 120, "response": 30, "total": 150}]
    assert body["test_cases"] == {"passed": 16, "failed": 1}
    assert body["control_agent"] == {"verdicts": {"pass": 9, "modified": 3}, "errors": 0}
    series = body["series"]
    assert len(series["timestamps"]) == 61 and series["timestamps"][-1] == end
    assert series["requests"]["allowed"][-2:] == [1.5, 2.0] and series["requests"]["allowed"][0] == 0


def test_dashboard_reports_prometheus_errors_as_unavailable():
    with pytest.raises(GatewayError) as raised:
        asyncio.run(prometheus.dashboard(fake_prometheus({}, status="error"), "6h", NOW))
    assert (raised.value.status_code, raised.value.code) == (503, "metrics_unavailable")
    assert "parse error" in raised.value.message


def test_dashboard_reports_an_unreachable_prometheus():
    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(base_url="http://prometheus", transport=httpx.MockTransport(refuse))
    with pytest.raises(GatewayError, match="unreachable"):
        asyncio.run(prometheus.dashboard(client, "1h", NOW))


def test_dashboard_route_explains_missing_prometheus_setting():
    from intentlatch.routes.metrics import dashboard

    fake = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(prometheus=None)))
    with pytest.raises(GatewayError) as raised:
        asyncio.run(dashboard(fake, "1h"))
    assert raised.value.status_code == 503
    assert "INTENTLATCH_PROMETHEUS_URL" in raised.value.message

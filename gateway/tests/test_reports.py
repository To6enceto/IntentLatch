import csv
import io
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone

import pytest

from intentlatch.errors import GatewayError
from intentlatch.reports import (
    ITEM_COLUMNS,
    ROW_FIELDS,
    build_report,
    filename,
    fired,
    item_json,
    items_csv,
    load_items,
    report_range,
    safe_cell,
    totals,
    totals_csv,
)

START = datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
END = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)
REQUEST = uuid.UUID("2f6a3c1e-0000-4000-8000-000000000001")
EMPLOYEE = uuid.UUID("11111111-1111-4111-8111-111111111111")


def result(code: str, outcome: str = "violated", kind: str | None = "regex", action: str = "block", reasoning: str | None = "Matches.") -> dict:
    return {"code": code, "kind": kind, "ai": kind is None, "action": action, "result": outcome, "reasoning": reasoning, "latency_ms": 0.4}


def row(outcome: str = "blocked", results=(), team: str = "payments", model: str = "corporate-a", **changes) -> tuple:
    values = {
        "ts": datetime(2026, 10, 4, 3, 2, 1, 123456, tzinfo=UTC),
        "request_id": REQUEST,
        "direction": "prompt",
        "outcome": outcome,
        "team": team,
        "model": model,
        "employee_id": EMPLOYEE,
        "employee": "Ana",
        "control_agent_status": "skipped",
        "policy_results": list(results),
        "source_masked": "My key is [RGX-CLOUD-KEY]",
        "rewritten_masked": None,
        "policy_version": 4,
        "test_run_id": None,
    } | changes
    return tuple(values.values())


def item(outcome: str = "blocked", codes=(), team: str = "payments", model: str = "corporate-a") -> dict:
    return item_json(row(outcome, [result(code) for code in codes], team, model))


def test_a_range_needs_a_timezone_and_order():
    assert report_range(START, END) == (START, END)
    warsaw = timezone(timedelta(hours=2))
    assert report_range(START.astimezone(warsaw), END.astimezone(warsaw)) == (START, END)
    for start, end, message in [
        (START, START, "from must be before to"),
        (END, START, "from must be before to"),
        (START.replace(tzinfo=None), END, "from and to need a timezone"),
        (START, END.replace(tzinfo=None), "from and to need a timezone"),
    ]:
        with pytest.raises(GatewayError) as caught:
            report_range(start, end)
        assert (caught.value.status_code, caught.value.code) == (400, "invalid_request")
        assert caught.value.message.startswith(message)


def test_fired_keeps_violated_policies_in_order_with_their_reasoning():
    results = [
        result("AUTH-MODEL", "pass", kind="authority", reasoning=None),
        result("RGX-EMAIL", action="edit", reasoning="The prompt matches this policy's pattern."),
        result("AI-NO-CREDENTIALS", "error", kind=None, reasoning="The control agent failed: it timed out."),
        result("RGX-CLOUD-KEY"),
    ]
    assert fired(results) == [
        {"code": "RGX-EMAIL", "kind": "regex", "ai": False, "action": "edit", "reasoning": "The prompt matches this policy's pattern."},
        {"code": "RGX-CLOUD-KEY", "kind": "regex", "ai": False, "action": "block", "reasoning": "Matches."},
    ]
    assert fired(None) == [] and fired(["x", {"result": "pass"}]) == []


def test_record_rows_become_items():
    test_run = uuid.uuid4()
    edited = row(
        "edited", [result("RGX-EMAIL", action="edit")], control_agent_status="modified",
        source_masked="Write to [RGX-EMAIL]", rewritten_masked="Write to [removed]", employee=None, test_run_id=test_run,
    )
    assert item_json(edited) == {
        "ts": "2026-10-04T03:02:01.123456+00:00",
        "request_id": str(REQUEST),
        "direction": "prompt",
        "outcome": "edited",
        "team": "payments",
        "model": "corporate-a",
        "employee_id": str(EMPLOYEE),
        "employee": None,
        "control_agent_status": "modified",
        "policies": [{"code": "RGX-EMAIL", "kind": "regex", "ai": False, "action": "edit", "reasoning": "Matches."}],
        "source_masked": "Write to [RGX-EMAIL]",
        "rewritten_masked": "Write to [removed]",
        "policy_version": 4,
        "test_run_id": str(test_run),
    }
    assert item_json(row())["test_run_id"] is None


def test_totals_count_items_by_outcome_policy_team_and_model():
    items = [
        item("blocked", ["RGX-CLOUD-KEY"], "payments", "corporate-a"),
        item("blocked", ["RGX-CLOUD-KEY", "RGX-EMAIL"], "hr", "corporate-a"),
        item("edited", ["RGX-EMAIL"], "hr", "corporate-b"),
        item("edited", ["RGX-EMAIL", "RGX-EMAIL"], "payments", "corporate-a"),
        item("blocked", [], "payments", "corporate-b"),
    ]
    assert totals(items) == {
        "blocked": 3,
        "edited": 2,
        "total": 5,
        "by_policy": [
            {"code": "RGX-EMAIL", "blocked": 1, "edited": 2, "total": 3},
            {"code": "RGX-CLOUD-KEY", "blocked": 2, "edited": 0, "total": 2},
        ],
        "by_team": [
            {"team": "payments", "blocked": 2, "edited": 1, "total": 3},
            {"team": "hr", "blocked": 1, "edited": 1, "total": 2},
        ],
        "by_model": [
            {"model": "corporate-a", "blocked": 2, "edited": 1, "total": 3},
            {"model": "corporate-b", "blocked": 1, "edited": 1, "total": 2},
        ],
    }


def test_ties_sort_by_name_and_an_empty_report_has_zero_totals():
    ordered = totals([item("blocked", ["RGX-B"], "zeta"), item("blocked", ["RGX-A"], "alpha")])
    assert [entry["code"] for entry in ordered["by_policy"]] == ["RGX-A", "RGX-B"]
    assert [entry["team"] for entry in ordered["by_team"]] == ["alpha", "zeta"]
    assert totals([]) == {"blocked": 0, "edited": 0, "total": 0, "by_policy": [], "by_team": [], "by_model": []}


def test_the_report_carries_its_range_totals_and_items():
    items = [item("blocked", ["RGX-CLOUD-KEY"])]
    now = datetime(2026, 10, 4, 6, 5, tzinfo=UTC)
    assert build_report(START, END, items, now) == {
        "from": "2026-10-04T00:00:00+00:00",
        "to": "2026-10-04T06:00:00+00:00",
        "generated_at": "2026-10-04T06:05:00+00:00",
        "totals": totals(items),
        "items": items,
    }


def parse(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_items_csv_has_one_row_per_item():
    blocked = item_json(row(results=[result("RGX-CLOUD-KEY"), result("AI-NO-CREDENTIALS", kind=None, reasoning="Asks for a key.")]))
    edited = item_json(row("edited", [result("RGX-EMAIL", action="edit", reasoning=None)], rewritten_masked="Hi [removed]", employee=None))
    header, first, second = parse(items_csv([blocked, edited]))
    assert tuple(header) == ITEM_COLUMNS
    assert dict(zip(header, first)) == {
        "ts": "2026-10-04T03:02:01.123456+00:00",
        "request_id": str(REQUEST),
        "direction": "prompt",
        "outcome": "blocked",
        "team": "payments",
        "model": "corporate-a",
        "employee_id": str(EMPLOYEE),
        "employee": "Ana",
        "control_agent_status": "skipped",
        "policy_version": "4",
        "policy_codes": "RGX-CLOUD-KEY AI-NO-CREDENTIALS",
        "reasoning": "RGX-CLOUD-KEY: Matches.\nAI-NO-CREDENTIALS: Asks for a key.",
        "source_masked": "My key is [RGX-CLOUD-KEY]",
        "rewritten_masked": "",
        "test_run_id": "",
    }
    assert (second[7], second[10], second[11], second[13]) == ("", "RGX-EMAIL", "RGX-EMAIL", "Hi [removed]")


@pytest.mark.parametrize("text", ["=HYPERLINK(\"http://evil\")", "+1", "-2+3", "@SUM(A1)", "\tx", "\rx"])
def test_cells_a_spreadsheet_would_run_are_neutralized(text):
    assert safe_cell(text) == "'" + text
    hostile = item_json(row(source_masked=text, employee=text))
    _, values = parse(items_csv([hostile]))
    assert values[ITEM_COLUMNS.index("source_masked")] == "'" + text
    assert values[ITEM_COLUMNS.index("employee")] == "'" + text


def test_plain_cells_are_left_alone():
    assert [safe_cell(value) for value in ["Hello, \"quoted\"\nline", 4, None, "", "a=b"]] == [
        "Hello, \"quoted\"\nline", 4, "", "", "a=b",
    ]


def test_totals_csv_is_one_long_table():
    report_totals = totals([item("blocked", ["RGX-CLOUD-KEY"], "payments", "corporate-a"), item("edited", ["RGX-EMAIL"], "hr", "corporate-b")])
    assert parse(totals_csv(report_totals)) == [
        ["dimension", "key", "blocked", "edited", "total"],
        ["all", "all", "1", "1", "2"],
        ["policy", "RGX-CLOUD-KEY", "1", "0", "1"],
        ["policy", "RGX-EMAIL", "0", "1", "1"],
        ["team", "hr", "0", "1", "1"],
        ["team", "payments", "1", "0", "1"],
        ["model", "corporate-a", "1", "0", "1"],
        ["model", "corporate-b", "0", "1", "1"],
    ]


class Recording:
    """A pool whose one connection records each statement and answers with fixed rows."""

    def __init__(self, rows: list[tuple]):
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    @asynccontextmanager
    async def connection(self, timeout: float):
        yield self

    async def execute(self, query: str, params: tuple):
        self.calls.append((query, params))
        return self

    async def fetchall(self) -> list[tuple]:
        return self.rows


@pytest.mark.anyio
async def test_load_items_reads_edits_and_blocks_in_the_range_in_chain_order():
    rows = [row(results=[result("RGX-CLOUD-KEY")]), row("edited", [result("RGX-EMAIL", action="edit")])]
    pool = Recording(rows)
    assert await load_items(pool, START, END) == [item_json(entry) for entry in rows]
    ((query, params),) = pool.calls
    assert params == (START, END)
    selected = [column.strip() for column in query.removeprefix("SELECT ").split(" FROM ")[0].split(",")]
    assert selected == [f"r.{field}" if field != "employee" else "e.name" for field in ROW_FIELDS]
    assert " FROM decision_records r LEFT JOIN employees e ON e.id = r.employee_id" in query
    assert query.endswith(" WHERE r.ts >= %s AND r.ts < %s AND r.outcome IN ('edited', 'blocked') ORDER BY r.seq")


def test_filenames_use_compact_utc_stamps():
    warsaw = timezone(timedelta(hours=2))
    assert filename(START.astimezone(warsaw), END, "items") == "intentlatch-report-20261004T000000Z-20261004T060000Z-items.csv"
    assert filename(START, END, "totals").endswith("-totals.csv")

import csv
import io
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from psycopg_pool import AsyncConnectionPool

from . import db
from .errors import GatewayError
from .teams import iso

ROW_FIELDS = (
    "ts",
    "request_id",
    "direction",
    "outcome",
    "team",
    "model",
    "employee_id",
    "employee",
    "control_agent_status",
    "policy_results",
    "source_masked",
    "rewritten_masked",
    "policy_version",
    "test_run_id",
)
FIRED_FIELDS = ("code", "kind", "ai", "action", "reasoning")
ITEM_COLUMNS = (
    "ts",
    "request_id",
    "direction",
    "outcome",
    "team",
    "model",
    "employee_id",
    "employee",
    "control_agent_status",
    "policy_version",
    "policy_codes",
    "reasoning",
    "source_masked",
    "rewritten_masked",
    "test_run_id",
)
TOTALS_COLUMNS = ("dimension", "key", "blocked", "edited", "total")
BREAKDOWNS = (("policy", "by_policy", "code"), ("team", "by_team", "team"), ("model", "by_model", "model"))
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
STAMP = "%Y%m%dT%H%M%SZ"
ITEMS_QUERY = (
    "SELECT r.ts, r.request_id, r.direction, r.outcome, r.team, r.model, r.employee_id, e.name,"
    " r.control_agent_status, r.policy_results, r.source_masked, r.rewritten_masked, r.policy_version,"
    " r.test_run_id FROM decision_records r LEFT JOIN employees e ON e.id = r.employee_id"
    " WHERE r.ts >= %s AND r.ts < %s AND r.outcome IN ('edited', 'blocked') ORDER BY r.seq"
)


def report_range(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise GatewayError(400, "invalid_request", "from and to need a timezone, for example 2026-10-04T00:00:00Z")
    if start >= end:
        raise GatewayError(400, "invalid_request", "from must be before to")
    return start.astimezone(UTC), end.astimezone(UTC)


def fired(policy_results: Any) -> list[dict[str, Any]]:
    """The violated policies of one record, in pipeline order, with their reasoning."""
    if not isinstance(policy_results, list):
        return []
    return [
        {field: entry.get(field) for field in FIRED_FIELDS}
        for entry in policy_results
        if isinstance(entry, dict) and entry.get("result") == "violated"
    ]


def item_json(row: tuple) -> dict[str, Any]:
    record = dict(zip(ROW_FIELDS, row))
    test_run_id = record["test_run_id"]
    return {
        "ts": iso(record["ts"]),
        "request_id": str(record["request_id"]),
        "direction": record["direction"],
        "outcome": record["outcome"],
        "team": record["team"],
        "model": record["model"],
        "employee_id": str(record["employee_id"]),
        "employee": record["employee"],
        "control_agent_status": record["control_agent_status"],
        "policies": fired(record["policy_results"]),
        "source_masked": record["source_masked"],
        "rewritten_masked": record["rewritten_masked"],
        "policy_version": record["policy_version"],
        "test_run_id": str(test_run_id) if test_run_id is not None else None,
    }


async def load_items(pool: AsyncConnectionPool, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Every edited or blocked record with ts in [start, end), in chain order."""
    async with db.connection(pool) as conn:
        cursor = await conn.execute(ITEMS_QUERY, (start, end))
        rows = await cursor.fetchall()
    return [item_json(row) for row in rows]


def outcome_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    tally = Counter(item["outcome"] for item in items)
    return {"blocked": tally["blocked"], "edited": tally["edited"], "total": tally["blocked"] + tally["edited"]}


def breakdown(
    items: list[dict[str, Any]], name: str, keys: Callable[[dict[str, Any]], Iterable[Any]]
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        # Each item counts once per key, however often the key appears in it.
        for key in dict.fromkeys(value for value in keys(item) if isinstance(value, str)):
            groups.setdefault(key, []).append(item)
    rows = [{name: key, **outcome_counts(group)} for key, group in groups.items()]
    return sorted(rows, key=lambda row: (-row["total"], row[name]))


def totals(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **outcome_counts(items),
        "by_policy": breakdown(items, "code", lambda item: [policy["code"] for policy in item["policies"]]),
        "by_team": breakdown(items, "team", lambda item: [item["team"]]),
        "by_model": breakdown(items, "model", lambda item: [item["model"]]),
    }


def build_report(
    start: datetime, end: datetime, items: list[dict[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    return {
        "from": iso(start),
        "to": iso(end),
        "generated_at": iso(now or datetime.now(UTC)),
        "totals": totals(items),
        "items": items,
    }


def safe_cell(value: Any) -> Any:
    # Masked prompts are attacker-written, so a spreadsheet must never read a cell as a formula.
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return "'" + value
    return "" if value is None else value


def write_csv(columns: tuple[str, ...], rows: Iterable[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows([safe_cell(value) for value in row] for row in rows)
    return buffer.getvalue()


def reasoning_lines(policies: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{policy['code']}: {policy['reasoning']}" if policy["reasoning"] else str(policy["code"]) for policy in policies
    )


def item_row(item: dict[str, Any]) -> list[Any]:
    return [
        *(item[column] for column in ITEM_COLUMNS[:10]),
        " ".join(str(policy["code"]) for policy in item["policies"]),
        reasoning_lines(item["policies"]),
        item["source_masked"],
        item["rewritten_masked"],
        item["test_run_id"],
    ]


def items_csv(items: list[dict[str, Any]]) -> str:
    return write_csv(ITEM_COLUMNS, (item_row(item) for item in items))


def totals_csv(report_totals: dict[str, Any]) -> str:
    rows = [["all", "all", report_totals["blocked"], report_totals["edited"], report_totals["total"]]]
    for dimension, field, key in BREAKDOWNS:
        rows += [[dimension, row[key], row["blocked"], row["edited"], row["total"]] for row in report_totals[field]]
    return write_csv(TOTALS_COLUMNS, rows)


def filename(start: datetime, end: datetime, part: str) -> str:
    return f"intentlatch-report-{start.astimezone(UTC):{STAMP}}-{end.astimezone(UTC):{STAMP}}-{part}.csv"

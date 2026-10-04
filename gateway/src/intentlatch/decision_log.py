import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from . import db
from .pipeline import PolicyResult, luhn_counted, pattern_matches, text_views
from .policies import Policy, PolicySnapshot
from .trace import Stage, Trace

GENESIS = "0" * 64
# Fixed key so every replica appends under the same lock and the chain stays linear.
CHAIN_LOCK_KEY = 7311204119
FIELDS = (
    "id",
    "request_id",
    "ts",
    "employee_id",
    "team",
    "model",
    "direction",
    "outcome",
    "control_agent_status",
    "policy_results",
    "source_masked",
    "rewritten_masked",
    "tokens_in",
    "tokens_out",
    "compute_seconds",
    "stage_latency_ms",
    "policy_version",
    "feed_version",
    "test_run_id",
)
JSON_FIELDS = {"policy_results", "stage_latency_ms"}
INSERT_RECORD = (
    f"INSERT INTO decision_records ({', '.join(FIELDS)}, prev_hash, hash)"
    f" VALUES ({', '.join(['%s'] * (len(FIELDS) + 2))})"
)


def start(request: Request, model: str) -> Trace:
    trace = Trace(
        request_id=request.state.request_id,
        identity=request.state.identity,
        model=model,
        auth_ms=getattr(request.state, "auth_ms", 0.0),
    )
    request.state.trace = trace
    return trace


def regex_policies(snapshot: PolicySnapshot) -> list[Policy]:
    return [policy for policy in snapshot.policies if policy.kind == "regex"]


def mask_text(text: str, policies: list[Policy]) -> str:
    masked = text
    for policy in policies:
        compiled = re.compile(policy.params["pattern"])
        counted = "luhn" in compiled.groupindex
        placeholder = f"[{policy.code}]"
        masked = compiled.sub(
            lambda match: placeholder if not counted or luhn_counted(match) else match.group(), masked
        )
    # A value only a normalized view shows (encoded, or split by invisible characters)
    # cannot be cut out of the raw text, so the whole piece is withheld.
    hidden = [policy.code for policy in policies if pattern_matches(policy.params["pattern"], text_views([masked]))]
    return f"[masked: {', '.join(hidden)}]" if hidden else masked


def mask(texts: list[str], policies: list[Policy]) -> str:
    return "\n\n".join(mask_text(text, policies) for text in texts)


def masked_results(results: list[PolicyResult], policies: list[Policy]) -> list[dict[str, Any]]:
    return [
        entry.model_dump() | {"reasoning": mask_text(entry.reasoning, policies) if entry.reasoning else entry.reasoning}
        for entry in results
    ]


def stage_record(
    trace: Trace, stage: Stage, direction: str, total_ms: float, ts: datetime, record_id: uuid.UUID
) -> dict[str, Any]:
    # Request-level fields (tokens, upstream, auth) sit on the prompt record only, so totals never double count.
    policies = regex_policies(trace.snapshot)
    decision = stage.decision
    prompt = direction == "prompt"
    return {
        "id": str(record_id),
        "request_id": trace.request_id,
        "ts": ts.astimezone(UTC).isoformat(),
        "employee_id": str(trace.identity.employee_id),
        "team": trace.identity.team_name,
        "model": trace.model,
        "direction": direction,
        "outcome": decision.outcome,
        "control_agent_status": decision.control_agent_status,
        "policy_results": masked_results(decision.policy_results, policies),
        "source_masked": mask(stage.texts, policies),
        "rewritten_masked": mask(decision.rewrites, policies) if decision.rewrites is not None else None,
        "tokens_in": trace.tokens_in if prompt else 0,
        "tokens_out": trace.tokens_out if prompt else 0,
        "compute_seconds": f"{stage.agent_seconds + (trace.upstream_seconds if prompt else 0.0):.3f}",
        "stage_latency_ms": {
            "auth": trace.auth_ms if prompt else 0.0,
            "non_ai": stage.non_ai_ms,
            "control_agent": stage.agent_ms,
            "upstream": trace.upstream_ms if prompt else 0.0,
            "total": total_ms,
        },
        "policy_version": decision.policy_version,
        "feed_version": None,
        "test_run_id": None,
    }


def records(trace: Trace, total_ms: float, now: datetime | None = None) -> list[dict[str, Any]]:
    ts = now or datetime.now(UTC)
    stages = (("prompt", trace.prompt), ("response", trace.response))
    return [
        stage_record(trace, stage, direction, total_ms, ts, uuid.uuid4())
        for direction, stage in stages
        if stage is not None
    ]


def canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_hash(prev_hash: str, record: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical(record)).encode("utf-8")).hexdigest()


def chain(entries: list[dict[str, Any]], prev_hash: str) -> list[tuple[str, str]]:
    """The (prev_hash, hash) pair of each record appended in order after prev_hash."""
    links = []
    for record in entries:
        digest = record_hash(prev_hash, record)
        links.append((prev_hash, digest))
        prev_hash = digest
    return links


async def append(pool: AsyncConnectionPool, entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    async with db.connection(pool) as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (CHAIN_LOCK_KEY,))
        cursor = await conn.execute("SELECT hash FROM decision_records ORDER BY seq DESC LIMIT 1")
        row = await cursor.fetchone()
        for record, (prev_hash, digest) in zip(entries, chain(entries, row[0] if row else GENESIS)):
            values = [Jsonb(record[name]) if name in JSON_FIELDS else record[name] for name in FIELDS]
            await conn.execute(INSERT_RECORD, (*values, prev_hash, digest))


async def write(pool: AsyncConnectionPool, trace: Trace, total_ms: float) -> None:
    await append(pool, records(trace, total_ms))

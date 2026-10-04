import json
from collections.abc import Sequence
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ValidationError

from .identity import Identity
from .policies import Policy

REASONING_MAX = 300
VALUES_MAX = 10
VALUE_CHARS_MAX = 200

FailureReason = Literal["timeout", "unreachable", "unparseable", "missing_rewrite"]
FAILURE_TEXT: dict[str, str] = {
    "timeout": "it timed out",
    "unreachable": "it is unreachable",
    "unparseable": "its verdict could not be parsed",
    "missing_rewrite": "it returned no usable rewrite",
}

SYSTEM_PROMPT = """You are the IntentLatch control agent. You check text exchanged between an employee and a corporate AI model against company policies.

The user message is JSON with:
- direction: "prompt" (written by the employee) or "response" (written by the AI model)
- identity: the verified employee, their team and the models the team may use
- policies: rules, each with a code, the rule text and an action (block or edit)
- matches: patterns that already matched, each with a code and the matched values, which must be removed
- texts: the text pieces, in order

Reply with JSON only:
- violations: one {"code", "reasoning"} entry for every policy the texts break and for every code in matches. The reasoning is one short sentence and never repeats a sensitive value.
- rewritten: only when a broken policy has action edit or matches is not empty. Give every piece of texts, in the same order and number, with the offending content removed or replaced by [removed] and everything else unchanged. Matched values can also appear Base64 or URL encoded; remove those forms too.
- status: "blocked" when a broken policy has action block, otherwise "modified" when you rewrote, otherwise "pass"."""

VERDICT_FORMAT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["pass", "blocked", "modified"]},
        "violations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"code": {"type": "string"}, "reasoning": {"type": "string"}},
                "required": ["code", "reasoning"],
            },
        },
        "rewritten": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "violations"],
}


class AgentFailure(Exception):
    def __init__(self, reason: FailureReason):
        super().__init__(reason)
        self.reason = reason


class Violation(BaseModel):
    code: str
    reasoning: str


class Verdict(BaseModel):
    """The agent's answer; informational until the gateway applies the database actions."""

    status: Literal["pass", "blocked", "modified"]
    violations: list[Violation]
    rewritten: list[str] | None = None


def agent_request(
    direction: str,
    identity: Identity,
    policies: Sequence[Policy],
    matches: Sequence[tuple[str, Sequence[str]]],
    texts: Sequence[str],
) -> dict[str, Any]:
    # The verified identity only: the raw token never goes into a model prompt.
    return {
        "direction": direction,
        "identity": {
            "employee": identity.employee_name,
            "team": identity.team_name,
            "authorized_models": list(identity.authorized_models),
        },
        "policies": [{"code": policy.code, "text": policy.text, "action": policy.action} for policy in policies],
        "matches": [
            {"code": code, "values": [value[:VALUE_CHARS_MAX] for value in values[:VALUES_MAX]]}
            for code, values in matches
        ],
        "texts": list(texts),
    }


def parse_verdict(content: Any, piece_count: int) -> Verdict:
    if not isinstance(content, str):
        raise AgentFailure("unparseable")
    try:
        verdict = Verdict.model_validate_json(content)
    except ValidationError as exc:
        raise AgentFailure("unparseable") from exc
    if verdict.rewritten is None and verdict.status == "modified":
        raise AgentFailure("missing_rewrite")
    if verdict.rewritten is not None and len(verdict.rewritten) != piece_count:
        raise AgentFailure("missing_rewrite")
    violations = [
        Violation(code=entry.code.strip().upper(), reasoning=entry.reasoning.strip()[:REASONING_MAX])
        for entry in verdict.violations
    ]
    return verdict.model_copy(update={"violations": violations})


async def judge(client: httpx.AsyncClient, model: str, body: dict[str, Any]) -> Verdict:
    payload = {
        "model": model,
        "stream": False,
        "format": VERDICT_FORMAT,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
        ],
    }
    try:
        response = await client.post("/api/chat", json=payload)
    except httpx.TimeoutException as exc:
        raise AgentFailure("timeout") from exc
    except httpx.TransportError as exc:
        raise AgentFailure("unreachable") from exc
    if not response.is_success:
        raise AgentFailure("unreachable")
    try:
        reply = response.json()
    except ValueError as exc:
        raise AgentFailure("unparseable") from exc
    message = reply.get("message") if isinstance(reply, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return parse_verdict(content, len(body["texts"]))

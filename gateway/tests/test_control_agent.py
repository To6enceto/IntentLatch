import json
import uuid

import httpx
import pytest

from intentlatch.control_agent import (
    REASONING_MAX,
    VERDICT_FORMAT,
    AgentFailure,
    agent_request,
    judge,
    parse_verdict,
)
from intentlatch.identity import Identity
from intentlatch.policies import Policy

IDENTITY = Identity(
    employee_id=uuid.uuid4(),
    employee_name="Ana",
    team_id=uuid.uuid4(),
    team_name="Payments",
    authorized_models=["corporate-a"],
)
AI = Policy(
    code="AI-NO-CREDENTIALS",
    ai=True,
    text="No credentials.",
    kind=None,
    params={},
    action="block",
    applies_to="both",
)


def body(texts: list[str]) -> dict:
    return agent_request("prompt", IDENTITY, [AI], [("RGX-EMAIL", ["a@b.pl"])], texts)


def verdict(**fields) -> str:
    return json.dumps({"status": "pass", "violations": [], **fields})


def test_request_carries_identity_policies_matches_and_texts():
    assert body(["one", "two"]) == {
        "direction": "prompt",
        "identity": {"employee": "Ana", "team": "Payments", "authorized_models": ["corporate-a"]},
        "policies": [{"code": "AI-NO-CREDENTIALS", "text": "No credentials.", "action": "block"}],
        "matches": [{"code": "RGX-EMAIL", "values": ["a@b.pl"]}],
        "texts": ["one", "two"],
    }


def test_request_caps_match_values():
    request = agent_request("response", IDENTITY, [], [("RGX-X", ["v" * 300] * 12)], ["t"])
    [match] = request["matches"]
    assert len(match["values"]) == 10
    assert all(len(value) == 200 for value in match["values"])


def test_valid_verdict_normalizes_codes_and_caps_reasoning():
    parsed = parse_verdict(
        verdict(status="blocked", violations=[{"code": " ai-no-credentials ", "reasoning": "  x" * 200}]), 1
    )
    [violation] = parsed.violations
    assert (parsed.status, violation.code) == ("blocked", "AI-NO-CREDENTIALS")
    assert len(violation.reasoning) == REASONING_MAX
    assert parsed.rewritten is None


def test_modified_verdict_keeps_one_rewrite_per_piece():
    parsed = parse_verdict(verdict(status="modified", rewritten=["a", "b"]), 2)
    assert parsed.rewritten == ["a", "b"]


@pytest.mark.parametrize(
    "content",
    [
        None,
        42,
        "",
        "not json",
        "[]",
        '{"status": "maybe", "violations": []}',
        '{"status": "pass"}',
        '{"status": "pass", "violations": [{"code": 1, "reasoning": "x"}]}',
        '{"status": "pass", "violations": [], "rewritten": "text"}',
    ],
)
def test_unparseable_verdicts_fail(content):
    with pytest.raises(AgentFailure) as caught:
        parse_verdict(content, 1)
    assert caught.value.reason == "unparseable"


@pytest.mark.parametrize(
    ("fields", "pieces"),
    [({"status": "modified"}, 1), ({"status": "modified", "rewritten": ["a"]}, 2), ({"rewritten": ["a", "b"]}, 1)],
    ids=["modified-without-rewrite", "too-few", "too-many"],
)
def test_a_missing_or_wrong_length_rewrite_fails(fields, pieces):
    with pytest.raises(AgentFailure) as caught:
        parse_verdict(verdict(**fields), pieces)
    assert caught.value.reason == "missing_rewrite"


async def run(handler, texts: list[str] | None = None):
    async with httpx.AsyncClient(base_url="http://agent", transport=httpx.MockTransport(handler)) as client:
        return await judge(client, "qwen2.5:3b", body(texts or ["Hello"]))


def answer(content: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json={"message": {"role": "assistant", "content": content}, "done": True})


@pytest.mark.anyio
async def test_judge_posts_one_structured_chat_request():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(verdict())

    parsed, seconds = await run(handler, ["one", "two"])
    assert (parsed.status, seconds) == ("pass", 0.0)
    [request] = seen
    payload = json.loads(request.content)
    assert (request.method, request.url.path) == ("POST", "/api/chat")
    assert (payload["model"], payload["stream"], payload["options"]) == ("qwen2.5:3b", False, {"temperature": 0})
    assert payload["format"] == VERDICT_FORMAT
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    assert json.loads(payload["messages"][1]["content"]) == body(["one", "two"])


@pytest.mark.anyio
async def test_judge_reports_the_agents_eval_durations_in_seconds():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": verdict()},
                "prompt_eval_duration": 1_500_000_000,
                "eval_duration": 2_000_000_000,
            },
        )

    _, seconds = await run(handler)
    assert seconds == 3.5


def raise_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


def raise_connect(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (raise_timeout, "timeout"),
        (raise_connect, "unreachable"),
        (lambda request: answer(verdict(), status_code=500), "unreachable"),
        (lambda request: httpx.Response(200, text="not json"), "unparseable"),
        (lambda request: httpx.Response(200, json={"done": True}), "unparseable"),
        (lambda request: answer("I think it is fine."), "unparseable"),
    ],
    ids=["timeout", "unreachable", "http-500", "non-json", "no-message", "prose"],
)
async def test_judge_maps_failures(handler, reason):
    with pytest.raises(AgentFailure) as caught:
        await run(handler)
    assert caught.value.reason == reason

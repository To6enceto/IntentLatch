import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .. import limits, pipeline, upstream
from ..auth import require_identity
from ..chat import ChatRequest, allowlisted, message_texts, prompt_texts

router = APIRouter(dependencies=[Depends(require_identity)])

FORWARDED_FIELDS = (
    "messages",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "stop",
    "seed",
    "frequency_penalty",
    "presence_penalty",
    "response_format",
    "tools",
    "tool_choice",
)


class OpenAIChatRequest(ChatRequest):
    stream: bool = False


@router.get("/v1/models")
async def list_models(request: Request) -> dict[str, Any]:
    created = request.app.state.started_at
    return {
        "object": "list",
        "data": [
            {"id": model_id, "object": "model", "created": created, "owned_by": "intentlatch"}
            for model_id in request.app.state.llms.ids
        ],
    }


@router.post("/v1/chat/completions")
async def chat_completions(body: OpenAIChatRequest, request: Request) -> Response:
    tag = request.app.state.llms.tag_for(body.model)
    data = body.model_dump(exclude_unset=True)
    snapshot = await pipeline.enforce_prompt_policies(
        request, body.model, prompt_texts(data["messages"])
    )
    payload = allowlisted(data, FORWARDED_FIELDS) | {"model": tag, "stream": False}
    completion = await upstream.request_json(
        request.app.state.ollama, "POST", "/v1/chat/completions", json=payload
    )
    # Counted before response policies run: a blocked answer still spent its tokens.
    await limits.record_usage(
        request.app.state.pool, request.state.identity.team_id, completion_usage(completion)
    )
    pipeline.enforce_response_policies(request, snapshot, completion_texts(completion))
    completion["model"] = body.model
    if not body.stream:
        return JSONResponse(completion)
    stream_options = data.get("stream_options")
    include_usage = isinstance(stream_options, dict) and bool(stream_options.get("include_usage"))
    return StreamingResponse(
        iter(replay_as_sse(completion, include_usage)), media_type="text/event-stream"
    )


def completion_texts(completion: dict[str, Any]) -> list[str]:
    choices = completion.get("choices")
    if not isinstance(choices, list):
        return []
    return [
        text
        for choice in choices
        if isinstance(choice, dict)
        for text in message_texts(choice.get("message"))
    ]


def completion_usage(completion: dict[str, Any]) -> int:
    usage = completion.get("usage")
    if not isinstance(usage, dict):
        return 0
    return limits.reported(usage.get("prompt_tokens")) + limits.reported(usage.get("completion_tokens"))


def replay_as_sse(completion: dict[str, Any], include_usage: bool) -> list[str]:
    choice = (completion.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    delta: dict[str, Any] = {
        "role": message.get("role", "assistant"),
        "content": message.get("content"),
    }
    if message.get("tool_calls"):
        delta["tool_calls"] = [
            {"index": index, **call} for index, call in enumerate(message["tool_calls"])
        ]
    base = {
        "id": completion.get("id"),
        "object": "chat.completion.chunk",
        "created": completion.get("created"),
        "model": completion["model"],
    }
    first = base | {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    last = base | {
        "choices": [{"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason")}]
    }
    if include_usage and "usage" in completion:
        last["usage"] = completion["usage"]
    return [f"data: {json.dumps(first)}\n\n", f"data: {json.dumps(last)}\n\n", "data: [DONE]\n\n"]

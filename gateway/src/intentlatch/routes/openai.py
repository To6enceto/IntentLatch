import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import upstream
from ..chat import ChatRequest, allowlisted

router = APIRouter()

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
async def chat_completions(body: OpenAIChatRequest, request: Request):
    tag = request.app.state.llms.tag_for(body.model)
    data = body.model_dump(exclude_unset=True)
    payload = allowlisted(data, FORWARDED_FIELDS) | {"model": tag, "stream": False}
    completion = await upstream.request_json(
        request.app.state.ollama, "POST", "/v1/chat/completions", json=payload
    )
    completion["model"] = body.model
    if not body.stream:
        return JSONResponse(completion)
    stream_options = data.get("stream_options")
    include_usage = isinstance(stream_options, dict) and bool(stream_options.get("include_usage"))
    return StreamingResponse(
        iter(replay_as_sse(completion, include_usage)), media_type="text/event-stream"
    )


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

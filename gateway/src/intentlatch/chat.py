import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str


class ChatRequest(BaseModel):
    """Fields shared by the OpenAI and Ollama chat bodies; everything else rides along as extras."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[Message] = Field(min_length=1)


def allowlisted(data: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: data[key] for key in fields if key in data}


def content_texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
    return []


def arguments_text(call: Any) -> str | None:
    function = call.get("function") if isinstance(call, dict) else None
    arguments = function.get("arguments") if isinstance(function, dict) else None
    if isinstance(arguments, dict):
        # Ollama sends tool-call arguments as an object, OpenAI as a JSON string.
        return json.dumps(arguments, ensure_ascii=False)
    return arguments if isinstance(arguments, str) else None


def message_texts(message: Any) -> list[str]:
    """The pieces of one chat message that policies check: its text and tool-call arguments."""
    if not isinstance(message, dict):
        return []
    calls = message.get("tool_calls")
    arguments = [arguments_text(call) for call in calls] if isinstance(calls, list) else []
    return content_texts(message.get("content")) + [text for text in arguments if text is not None]


def prompt_texts(messages: list[dict[str, Any]]) -> list[str]:
    # Every message counts, whatever its role: the client controls the whole history.
    return [text for message in messages for text in message_texts(message)]

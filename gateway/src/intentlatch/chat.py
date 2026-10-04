import json
from collections.abc import Iterator
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Piece:
    """One text that policies check, and whether a rewrite may replace it."""

    text: str
    rewritable: bool


def message_pieces(message: Any) -> list[Piece]:
    """The pieces of one chat message that policies check: its text, then tool-call arguments."""
    if not isinstance(message, dict):
        return []
    calls = message.get("tool_calls")
    arguments = [arguments_text(call) for call in calls] if isinstance(calls, list) else []
    content = [Piece(text, True) for text in content_texts(message.get("content"))]
    return content + [Piece(text, False) for text in arguments if text is not None]


def message_texts(message: Any) -> list[str]:
    return [piece.text for piece in message_pieces(message)]


def prompt_pieces(messages: list[dict[str, Any]]) -> list[Piece]:
    # Every message counts, whatever its role: the client controls the whole history.
    return [piece for message in messages for piece in message_pieces(message)]


def prompt_texts(messages: list[dict[str, Any]]) -> list[str]:
    return [piece.text for piece in prompt_pieces(messages)]


def rewrite_content(content: Any, texts: Iterator[str]) -> Any:
    if isinstance(content, str):
        return next(texts)
    if isinstance(content, list):
        return [
            {**part, "text": next(texts)} if isinstance(part, dict) and isinstance(part.get("text"), str) else part
            for part in content
        ]
    return content


def rewrite_message(message: Any, texts: Iterator[str]) -> Any:
    """Replaces a message's text pieces in the order message_pieces lists them."""
    if not isinstance(message, dict):
        return message
    pieces = message_pieces(message)
    rewritten = dict(message)
    if any(piece.rewritable for piece in pieces):
        rewritten["content"] = rewrite_content(message.get("content"), texts)
    # Tool-call arguments keep their original text, so their slots are only consumed.
    for _ in range(sum(not piece.rewritable for piece in pieces)):
        next(texts)
    return rewritten


def apply_rewrites(messages: list[Any], texts: list[str]) -> list[Any]:
    remaining = iter(texts)
    return [rewrite_message(message, remaining) for message in messages]

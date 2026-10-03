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

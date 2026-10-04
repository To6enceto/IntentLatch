import json

import pytest
from pydantic import ValidationError

from intentlatch.chat import (
    UNENCODABLE,
    Piece,
    apply_rewrites,
    message_pieces,
    message_texts,
    prompt_pieces,
    prompt_texts,
)
from intentlatch.errors import describe_validation_error
from intentlatch.routes.ollama import OllamaChatRequest
from intentlatch.routes.openai import OpenAIChatRequest

BODIES = (OpenAIChatRequest, OllamaChatRequest)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize(
    "raw",
    [
        '{"model": "corporate-a", "messages": [{"role": "user", "content": "\\ud800"}]}',
        '{"model": "corporate-a", "messages": [{"role": "user", "content": "key \\udfff here"}]}',
        '{"model": "corporate-a", "messages": [{"role": "user", "content": "Hi", "name": "\\ud83d"}]}',
        '{"model": "corporate-a", "messages": [{"role": "user", "content": "Hi"}], "temperature": NaN}',
        '{"model": "corporate-a", "messages": [{"role": "user", "content": "Hi"}], "options": {"temperature": Infinity}}',
        '{"model": "corporate-a", "messages": [{"role": "user", "content": [{"type": "text", "text": "\\udc00"}]}]}',
        '{"model": "corporate-a", "messages": [{"role": "user", "content": "Hi"}], "seed": -Infinity}',
    ],
)
def test_bodies_that_cannot_be_passed_on_are_rejected(body, raw):
    # json.loads reads these the way the server does: NaN, Infinity and lone surrogates all parse.
    with pytest.raises(ValidationError) as caught:
        body.model_validate(json.loads(raw))
    assert describe_validation_error(caught.value) == UNENCODABLE


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize(
    "raw",
    [
        '{"model": "corporate-a", "messages": [{"role": "user", "content": "Zażółć gęślą jaźń \\ud83d\\ude00"}]}',
        '{"model": "corporate-a", "messages": [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}], "temperature": 0.2}',
        '{"model": "corporate-a", "messages": [{"role": "user", "content": "Hi"}], "options": {"temperature": 1e3, "seed": 7}}',
    ],
)
def test_plain_json_bodies_pass(body, raw):
    request = body.model_validate(json.loads(raw))
    assert request.model_dump(exclude_unset=True) == json.loads(raw)


def test_string_content_is_one_piece():
    assert message_texts({"role": "user", "content": "Hello"}) == ["Hello"]


def test_text_parts_are_pieces_and_other_parts_are_skipped():
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "first"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            "junk",
            {"type": "text", "text": 7},
            {"type": "text", "text": "second"},
        ],
    }
    assert message_texts(message) == ["first", "second"]


def test_tool_call_arguments_are_pieces_after_the_content():
    message = {
        "role": "assistant",
        "content": "calling",
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": '{"q": "x"}'}},
            {"function": {"name": "g", "arguments": {"q": "zażółć"}}},
        ],
    }
    assert message_texts(message) == ["calling", '{"q": "x"}', '{"q": "zażółć"}']


@pytest.mark.parametrize(
    "message",
    [
        None,
        "text",
        {"role": "user"},
        {"role": "assistant", "content": None},
        {"role": "user", "content": 42},
        {"role": "user", "content": {"text": "not a list"}},
        {"role": "assistant", "tool_calls": "junk"},
        {
            "role": "assistant",
            "tool_calls": ["junk", {"function": "junk"}, {"function": {"arguments": None}}, {"function": {"arguments": [1]}}],
        },
    ],
)
def test_junk_shapes_give_no_pieces(message):
    assert message_texts(message) == []


def test_content_pieces_are_rewritable_and_tool_call_arguments_are_not():
    message = {
        "role": "assistant",
        "content": [{"type": "text", "text": "a"}, {"type": "image_url", "image_url": {"url": "x"}}, {"type": "text", "text": "b"}],
        "tool_calls": [{"function": {"name": "f", "arguments": '{"q": 1}'}}],
    }
    assert message_pieces(message) == [Piece("a", True), Piece("b", True), Piece('{"q": 1}', False)]


def test_rewrites_replace_content_in_place_and_keep_tool_call_arguments():
    call = {"id": "c", "type": "function", "function": {"name": "f", "arguments": '{"q": "secret"}'}}
    messages = [
        {"role": "system", "content": "s"},
        {
            "role": "user",
            "content": [{"type": "text", "text": "u1"}, {"type": "image_url", "image_url": {"url": "x"}}, {"type": "text", "text": "u2"}],
        },
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "content": "t", "tool_call_id": "c"},
    ]
    assert len(prompt_pieces(messages)) == 5
    rewritten = apply_rewrites(messages, ["S", "U1", "U2", "ignored", "T"])
    assert rewritten == [
        {"role": "system", "content": "S"},
        {
            "role": "user",
            "content": [{"type": "text", "text": "U1"}, {"type": "image_url", "image_url": {"url": "x"}}, {"type": "text", "text": "U2"}],
        },
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "content": "T", "tool_call_id": "c"},
    ]
    assert messages[0]["content"] == "s"
    assert prompt_texts(rewritten) == ["S", "U1", "U2", '{"q": "secret"}', "T"]


def test_prompt_texts_cover_every_role_in_order():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "content": "t"},
    ]
    assert prompt_texts(messages) == ["s", "u", "a", "t"]

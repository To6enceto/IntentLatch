import pytest

from intentlatch.routes.openai import apply_completion_rewrites, completion_pieces, completion_texts, completion_usage


def test_completion_texts_read_every_choice():
    completion = {
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "first"}},
            {
                "index": 1,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}}],
                },
            },
        ]
    }
    assert completion_texts(completion) == ["first", '{"a": 1}']


@pytest.mark.parametrize(
    "completion",
    [
        {},
        {"choices": None},
        {"choices": "junk"},
        {"choices": ["junk"]},
        {"choices": [{"index": 0}]},
        {"choices": [{"message": {"role": "assistant", "content": None}}]},
    ],
)
def test_completion_without_text_gives_no_texts(completion):
    assert completion_texts(completion) == []


def test_completion_rewrites_replace_each_choice_content_and_keep_the_rest():
    call = {"type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}}
    completion = {
        "id": "c1",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "first"}, "finish_reason": "stop"},
            {"index": 1, "message": {"role": "assistant", "content": "second", "tool_calls": [call]}},
            {"index": 2},
        ],
        "usage": {"prompt_tokens": 1},
    }
    assert [piece.rewritable for piece in completion_pieces(completion)] == [True, True, False]
    rewritten = apply_completion_rewrites(completion, ["FIRST", "SECOND", "ignored"])
    assert completion_texts(rewritten) == ["FIRST", "SECOND", '{"a": 1}']
    assert rewritten["choices"][2] == {"index": 2}
    assert (rewritten["id"], rewritten["usage"], rewritten["choices"][0]["finish_reason"]) == ("c1", {"prompt_tokens": 1}, "stop")
    assert completion["choices"][0]["message"]["content"] == "first"


def test_completion_usage_adds_prompt_and_completion_tokens():
    completion = {"usage": {"prompt_tokens": 31, "completion_tokens": 12, "total_tokens": 43}}
    assert completion_usage(completion) == 43


@pytest.mark.parametrize(
    ("completion", "expected"),
    [
        ({}, 0),
        ({"usage": None}, 0),
        ({"usage": "43"}, 0),
        ({"usage": {"prompt_tokens": 31}}, 31),
        ({"usage": {"prompt_tokens": "31", "completion_tokens": 12}}, 12),
        ({"usage": {"prompt_tokens": -5, "completion_tokens": True}}, 0),
    ],
)
def test_completion_usage_counts_only_valid_fields(completion, expected):
    assert completion_usage(completion) == expected

import pytest

from intentlatch.routes.ollama import apply_reply_rewrites, reply_texts, reply_usage


def test_reply_texts_read_the_message():
    reply = {
        "model": "corporate-a",
        "message": {
            "role": "assistant",
            "content": "answer",
            "tool_calls": [{"function": {"name": "f", "arguments": {"city": "Kraków"}}}],
        },
        "done": True,
    }
    assert reply_texts(reply) == ["answer", '{"city": "Kraków"}']


@pytest.mark.parametrize(
    "reply",
    [{}, {"message": None}, {"message": "junk"}, {"message": {"role": "assistant", "content": None}}],
)
def test_reply_without_text_gives_no_texts(reply):
    assert reply_texts(reply) == []


def test_reply_rewrite_replaces_the_answer_and_keeps_tool_calls_and_counts():
    reply = {
        "model": "corporate-a",
        "message": {"role": "assistant", "content": "answer", "tool_calls": [{"function": {"name": "f", "arguments": {"a": 1}}}]},
        "done": True,
        "eval_count": 5,
    }
    rewritten = apply_reply_rewrites(reply, ["ANSWER", "ignored"])
    assert reply_texts(rewritten) == ["ANSWER", '{"a": 1}']
    assert (rewritten["done"], rewritten["eval_count"]) == (True, 5)
    assert reply["message"]["content"] == "answer"
    assert apply_reply_rewrites({"done": True}, []) == {"done": True}


def test_reply_usage_adds_prompt_and_answer_counts():
    reply = {"model": "corporate-a", "done": True, "prompt_eval_count": 26, "eval_count": 298}
    assert reply_usage(reply) == 324


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({}, 0),
        ({"eval_count": 298}, 298),
        ({"prompt_eval_count": 26, "eval_count": None}, 26),
        ({"prompt_eval_count": "26", "eval_count": 2.5}, 0),
    ],
)
def test_reply_usage_counts_only_valid_fields(reply, expected):
    assert reply_usage(reply) == expected

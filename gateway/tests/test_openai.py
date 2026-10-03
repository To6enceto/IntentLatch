import pytest

from intentlatch.routes.openai import completion_texts


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

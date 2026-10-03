import pytest

from intentlatch.chat import message_texts, prompt_texts


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


def test_prompt_texts_cover_every_role_in_order():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "content": "t"},
    ]
    assert prompt_texts(messages) == ["s", "u", "a", "t"]

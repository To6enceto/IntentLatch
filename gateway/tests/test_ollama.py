import pytest

from intentlatch.routes.ollama import reply_texts


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

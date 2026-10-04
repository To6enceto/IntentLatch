import pytest

from intentlatch.upstream import eval_seconds


def test_eval_seconds_add_prompt_and_answer_durations():
    reply = {"total_duration": 9_000_000_000, "prompt_eval_duration": 250_000_000, "eval_duration": 4_000_000_000}
    assert eval_seconds(reply) == 4.25


@pytest.mark.parametrize(
    "reply",
    [None, [], {}, {"eval_duration": -1}, {"eval_duration": "5"}, {"eval_duration": True}, {"eval_duration": 1.5}],
)
def test_missing_or_junk_durations_count_zero(reply):
    assert eval_seconds(reply) == 0.0

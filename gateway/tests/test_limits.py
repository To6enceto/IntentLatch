from datetime import UTC, datetime

import pytest

from intentlatch.limits import reported, window_end, window_start
from intentlatch.policies import INT_MAX


def at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


@pytest.mark.parametrize(
    ("now", "window_seconds", "start"),
    [
        (1020.0, 60, 1020),
        (1050.0, 60, 1020),
        (1079.999, 60, 1020),
        (7199.5, 3600, 3600),
        (12.3, 1, 12),
        (1_790_000_000.0, INT_MAX, 0),
    ],
    ids=["boundary", "mid-window", "just-before-end", "fractional", "one-second", "int-max"],
)
def test_windows_are_fixed_and_aligned_to_the_epoch(now, window_seconds, start):
    assert window_start(now, window_seconds) == at(start)
    assert window_end(now, window_seconds) == at(start + window_seconds)


def test_hour_and_day_windows_follow_the_utc_clock():
    now = datetime(2026, 10, 4, 1, 47, 30, 250000, tzinfo=UTC).timestamp()
    assert window_start(now, 3600) == datetime(2026, 10, 4, 1, 0, tzinfo=UTC)
    assert window_end(now, 3600) == datetime(2026, 10, 4, 2, 0, tzinfo=UTC)
    assert window_start(now, 86400) == datetime(2026, 10, 4, tzinfo=UTC)
    assert window_end(now, 86400) == datetime(2026, 10, 5, tzinfo=UTC)


@pytest.mark.parametrize("value", [0, 1, 1204, INT_MAX])
def test_valid_counts_are_reported(value):
    assert reported(value) == value


@pytest.mark.parametrize("value", [None, -1, INT_MAX + 1, True, False, 12.0, "12", [12], {"tokens": 12}])
def test_anything_else_counts_zero(value):
    assert reported(value) == 0

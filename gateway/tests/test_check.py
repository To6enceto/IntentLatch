import pytest
from pydantic import ValidationError

from intentlatch.routes.check import CheckRequest


def test_request_accepts_a_model_and_prompt():
    request = CheckRequest.model_validate({"model": "corporate-a", "prompt": "Hello"})
    assert (request.model, request.prompt) == ("corporate-a", "Hello")


@pytest.mark.parametrize(
    "body",
    [
        {"prompt": "Hello"},
        {"model": "corporate-a"},
        {"model": "corporate-a", "prompt": ""},
        {"model": "corporate-a", "prompt": 42},
        {"model": 1, "prompt": "Hello"},
        {"model": "corporate-a", "prompt": "Hello", "token": "x"},
    ],
)
def test_request_rejects_missing_empty_non_string_or_unknown_fields(body):
    with pytest.raises(ValidationError):
        CheckRequest.model_validate(body)

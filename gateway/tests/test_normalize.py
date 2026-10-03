import base64
from urllib.parse import quote

import pytest

from intentlatch.normalize import normalized_views

# Its standard Base64 holds "+" and "/", its URL-safe form "-" and "_", and it ends in "==".
SOURCE = "AKIAIOSFODNN7EXAMPLE ???>>>!"


def test_plain_ascii_text_is_its_only_view():
    assert normalized_views("Hello, how are you today?") == ["Hello, how are you today?"]


def test_format_characters_are_stripped_from_the_first_view():
    text = "AKIA​IOSF‍ODNN‮7EXA﻿MPLE­"
    assert normalized_views(text)[0] == "AKIAIOSFODNN7EXAMPLE"


def test_percent_encoding_adds_a_decoded_view():
    assert normalized_views("mail jan.kowalski%40firma.pl") == [
        "mail jan.kowalski%40firma.pl",
        "mail jan.kowalski@firma.pl",
    ]


def test_percent_decoded_view_is_stripped_again():
    assert "AKIAIOSFODNN7EXAMPLE" in normalized_views("AKIA%E2%80%8BIOSFODNN7EXAMPLE")


def test_a_percent_sign_with_nothing_to_decode_adds_no_view():
    assert normalized_views("100% sure") == ["100% sure"]


@pytest.mark.parametrize(
    ("encode", "alphabet"), [(base64.b64encode, "+/"), (base64.urlsafe_b64encode, "-_")]
)
@pytest.mark.parametrize("padded", [True, False])
def test_base64_adds_the_decoded_text(encode, alphabet, padded):
    encoded = encode(SOURCE.encode()).decode()
    assert set(alphabet) <= set(encoded) and encoded.endswith("==")
    if not padded:
        encoded = encoded.rstrip("=")
    assert SOURCE in normalized_views(f"payload {encoded} end")


def test_percent_encoded_base64_is_decoded_in_two_steps():
    encoded = quote(base64.b64encode(SOURCE.encode()).decode(), safe="")
    assert not set("+/=") & set(encoded)
    assert SOURCE in normalized_views(f"https://example.com/?d={encoded}")


def test_decoded_views_are_stripped_again():
    encoded = base64.b64encode("AKIA​IOSFODNN7EXAMPLE".encode()).decode()
    assert "AKIAIOSFODNN7EXAMPLE" in normalized_views(encoded)


@pytest.mark.parametrize(
    "text",
    [
        base64.b64encode(bytes(range(256))).decode(),
        base64.b64encode(b"hi there").decode(),
        "QUtJQUlPU0ZPR",
    ],
    ids=["binary", "shorter-than-12", "impossible-length"],
)
def test_undecodable_or_short_runs_add_no_view(text):
    assert normalized_views(text) == [text]

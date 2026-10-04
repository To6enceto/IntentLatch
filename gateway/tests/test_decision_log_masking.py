"""Masking before anything reaches the decision log: no value any regex policy looks for, and no key material."""

import pytest

from intentlatch import decision_log, pipeline, policies
from intentlatch.policies import Policy, PolicySnapshot
from intentlatch.testcases import load_seeds

SNAPSHOT = PolicySnapshot(
    version=1,
    policies=[Policy(**seed.model_dump(exclude={"enabled"})) for seed in policies.load_seeds() if seed.enabled],
)
REGEX = decision_log.regex_policies(SNAPSHOT)

PEM = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun
VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK
LxoS2tFczGkPLPgizskuemMghRniWaoLcyehkd3qqGElvW/VDL5AaWTg0nLVkjRo
-----END RSA PRIVATE KEY-----"""


def test_private_key_material_is_masked_not_just_its_header():
    masked = decision_log.mask_text(PEM, REGEX)
    for line in PEM.splitlines()[1:-1]:
        assert line not in masked
    assert masked.count(decision_log.SECRET_PLACEHOLDER) == 3


def test_long_words_and_plain_numbers_are_not_secret_shaped():
    text = "internationalizationinternationalization 12345678901234567890123456789012345"
    assert decision_log.mask_text(text, REGEX) == text


@pytest.mark.parametrize("seed", [seed for seed in load_seeds() if seed.expected != "ALLOW"], ids=lambda seed: seed.code)
def test_masked_text_no_longer_violates_any_regex_policy(seed):
    masked = decision_log.mask_text(seed.prompt, REGEX)
    results = pipeline.regex_results(SNAPSHOT, [masked], "prompt") + pipeline.regex_results(SNAPSHOT, [masked], "response")
    assert [entry.code for entry in results if entry.result == "violated"] == []

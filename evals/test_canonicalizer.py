"""Canonicaliser evals.

The negatives are the release gate. A matcher that scores perfectly on
positives and silently collapses a 300 gsm carton into a 350 gsm spec is
worse than no system at all, because it launders a bad decision as an
optimised one and the error surfaces on a receiving dock two months later.
"""
import json
import pathlib

import pytest

from sangam.engine.canonicalize import canonicalize

CASES = json.loads((pathlib.Path(__file__).parent / "golden" /
                    "canonicalizer_cases.json").read_text())


@pytest.mark.parametrize("case", CASES["positives"], ids=lambda c: c["text"][:40])
def test_positive_matches(case):
    result = canonicalize(case["text"])
    assert result.spec_id == case["spec_id"], (
        f"{case['text']!r} should resolve to {case['spec_id']}, got {result.spec_id}")
    assert result.evidence, "a match must carry the attributes it matched on"


@pytest.mark.parametrize("case", CASES["negatives"], ids=lambda c: c["text"][:40])
def test_negative_never_matches(case):
    result = canonicalize(case["text"])
    assert result.spec_id is None, (
        f"{case['text']!r} ({case['why']}) wrongly matched {result.spec_id}. "
        f"This class of false positive is a five-figure wrong PO.")
    assert result.adjacency_note, "a near-miss must say what it was near and why"


def test_adjacent_note_names_the_conflicting_attribute():
    result = canonicalize("Mono carton kraft 300gsm 4c")
    assert "gsm=300" in result.adjacency_note
    assert "SPEC-CTN-KFT-350-ML" in result.adjacency_note


def test_underspecification_is_not_conflict():
    """'white' where the spec says 'white_opaque' has under-described the same
    item. 'amber' has described a different one."""
    assert canonicalize("200ml PET jar, white, 70 mm neck").spec_id == "SPEC-JAR-PET-200-N70"
    assert canonicalize("200ml PET jar, amber, 70 mm neck").spec_id is None

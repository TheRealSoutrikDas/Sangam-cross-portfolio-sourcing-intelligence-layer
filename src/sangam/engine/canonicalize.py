"""Free text to canonical spec id.

Deliberately not fuzzy matching and deliberately not an embedding lookup. Both
will happily tell you a 300 gsm carton is a 350 gsm carton, and a procurement
system that does that once loses more than the whole project saves.

The rule: parse attributes, require agreement on every attribute present on
both sides. One conflicting attribute means "not this spec". If the coarse
family still matches, it surfaces as an ADJACENT spec, which is a
harmonisation proposal for a human and never an automatic substitution.
"""
from __future__ import annotations

import re
from typing import NamedTuple, Optional

from ..domain.registry import SPECS

FAMILY_HINTS = [
    (r"\bcarton\b", "secondary_carton"),
    (r"\bpouch\b", "flexible"),
    (r"\b(cap|closure)\b", "closure"),
    (r"\bjar\b", "primary_plastic"),
    (r"\b(bottle|btl)\b", "primary_glass"),
]

MATERIALS = [
    (r"\bglass\b", "glass"),
    (r"\bpet\b", "pet"),
    (r"\bkraft\b", "kraft"),
    (r"\bsbs\b", "sbs"),
    (r"\b(alu|aluminium|aluminum)\b", "aluminium"),
    (r"\b(laminated|laminate)\b", "laminate"),
]

# neck diameter is only inferable from a bare "NN mm" for these families
NECK_FAMILIES = {"primary_glass", "primary_plastic", "closure"}


class MatchResult(NamedTuple):
    spec_id: Optional[str]
    evidence: str
    adjacency_note: str


def parse_attrs(text: str) -> dict:
    """Description to attribute dict. The `_family` key is a coarse gate, not
    a matched attribute."""
    t = text.lower()
    a: dict = {}

    for pat, fam in FAMILY_HINTS:
        if re.search(pat, t):
            a["_family"] = fam
            break

    for pat, mat in MATERIALS:
        if re.search(pat, t):
            # "matt laminated" on a carton describes the finish, not the substrate
            if mat == "laminate" and a.get("_family") == "secondary_carton":
                continue
            a["material"] = mat
            break

    if m := re.search(r"(\d+)\s*gsm", t):
        a["gsm"] = int(m.group(1))
    if m := re.search(r"(\d+)\s*ml\b", t):
        a["volume_ml"] = int(m.group(1))
    if m := re.search(r"(?:neck|nk)\s*(\d+)", t):
        a["neck_mm"] = int(m.group(1))
    elif (m := re.search(r"(\d+)\s*mm\s*(?:neck)?", t)) and a.get("_family") in NECK_FAMILIES:
        a["neck_mm"] = int(m.group(1))
    if m := re.search(r"(\d+)\s*g(?:m|ms|ram)?\b", t):
        a["fill_g"] = int(m.group(1))
    if m := re.search(r"\b(\d)\s*(?:c|col|colour|color)\b", t):
        a["colours"] = int(m.group(1))
    if re.search(r"\bmatt\b", t):
        a["finish"] = "matt_lam"
    if re.search(r"stand\s*-?\s*up|standup", t):
        a["format"] = "standup_pouch"
    if re.search(r"\bliner\b", t):
        a["liner"] = True
    if re.search(r"screw", t):
        a["type"] = "screw_cap"

    # Colour is a discriminating attribute, not decoration. Different families
    # key it under different names (glass has a shade, plastic has a colour),
    # so a detected colour is written to both and the spec decides which it
    # cares about. Without this, "clear glass bottle 50ml 18mm" matches the
    # amber spec on everything else and nobody notices until 160,000 wrong
    # bottles reach the dock. Caught by evals, not by code review.
    colour = None
    if re.search(r"\bamber\b", t):
        colour = "amber"
    elif re.search(r"\b(clear|flint|transparent)\b", t):
        colour = "clear"
    elif re.search(r"white", t) and re.search(r"opaque", t):
        colour = "white_opaque"
    elif re.search(r"\bwhite\b", t):
        colour = "white"
    elif re.search(r"\bblack\b", t):
        colour = "black"
    if colour:
        a["shade"] = a["colour"] = colour

    return a


def _compatible(spec_value, parsed_value) -> bool:
    """A vaguer description is not a conflicting one.

    A brand writing "white" where the spec says "white_opaque" has
    under-described the same item. A brand writing "amber" has described a
    different one. Treating under-specification as a hit is what keeps recall
    usable, because it is exactly how buyers type.
    """
    if spec_value == parsed_value:
        return True
    if isinstance(spec_value, str) and isinstance(parsed_value, str):
        return spec_value.startswith(parsed_value + "_")
    return False


def canonicalize(text: str) -> MatchResult:
    attrs = parse_attrs(text)
    family = attrs.get("_family")
    if not family:
        return MatchResult(None, "", "no recognisable item family in description")

    best: Optional[str] = None
    best_hits = 0
    best_evidence = ""
    adjacents: list[str] = []

    for spec in SPECS:
        if spec.family != family:
            continue
        hits, conflicts = [], []
        for key, want in spec.attrs.items():
            if key in attrs:
                target = hits if _compatible(want, attrs[key]) else conflicts
                target.append(f"{key}={attrs[key]}")
        if conflicts:
            adjacents.append(f"{spec.spec_id} (differs on {', '.join(conflicts)})")
        elif len(hits) >= 2 and len(hits) > best_hits:
            best, best_hits, best_evidence = spec.spec_id, len(hits), ", ".join(hits)

    if best:
        return MatchResult(best, f"matched on {best_evidence}", "")

    note = "; ".join(adjacents) if adjacents else f"no spec in family {family}"
    return MatchResult(None, "", f"adjacent to {note}")

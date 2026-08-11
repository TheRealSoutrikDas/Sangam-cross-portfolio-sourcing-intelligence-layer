"""Deterministic sourcing engine.

Pure functions. No ADK, no model, no I/O. Everything the system decides is
decided here, which is what makes it testable in milliseconds and defensible
in a review.
"""
from .bundling import counterfactual_if_reconfirmed, is_tradeable, optimise_spec
from .canonicalize import MatchResult, canonicalize, parse_attrs
from .costing import applicable_tier, landed_unit_cost, quote_landed
from .risk import assess, harmonisation_candidates

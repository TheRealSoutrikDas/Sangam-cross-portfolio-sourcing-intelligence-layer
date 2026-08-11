"""Bundling evals.

These pin the behaviours that are easy to regress and expensive to get wrong:
that splitting an order is re-priced at the split, that an expired quote
cannot be awarded, and that policy actually binds rather than decorating the
output.
"""
from collections import defaultdict
from datetime import date

import pytest

from sangam.config import SourcingPolicy
from sangam.domain.registry import VENDOR_BY_ID
from sangam.engine.bundling import counterfactual_if_reconfirmed, is_tradeable, optimise_spec


@pytest.fixture
def by_spec(quotes, demand):
    q, d = defaultdict(list), defaultdict(list)
    from sangam.engine.canonicalize import canonicalize
    for quote in quotes:
        quote.spec_id = canonicalize(quote.raw_description).spec_id
        if quote.spec_id:
            q[quote.spec_id].append(quote)
    for line in demand:
        line.spec_id = canonicalize(line.raw_description).spec_id
        if line.spec_id:
            d[line.spec_id].append(line)
    return q, d


def _rec(spec_id, by_spec, policy, run_date, **kw):
    q, d = by_spec
    return optimise_spec(spec_id, d[spec_id], q[spec_id], policy, run_date, **kw)


def test_pooling_beats_every_brand_buying_alone(by_spec, policy, run_date):
    rec = _rec("SPEC-GLS-AMB-050-N18", by_spec, policy, run_date)
    assert rec.saving > 0
    assert rec.award_unit < rec.baseline_unit
    assert len(rec.brands) == 3, "the whole point is that this is one order, not three"


def test_horizon_pooling_unlocks_a_tier_no_month_reaches(by_spec, policy, run_date):
    """66,000/month never reaches Sunrise's 80,000 tier. 198,000 a quarter
    clears it comfortably. Pooling across time is the axis people forget."""
    rec = _rec("SPEC-JAR-PET-200-N70", by_spec, policy, run_date)
    assert rec.monthly_qty < 80_000 < rec.horizon_qty


def test_expired_quote_cannot_be_awarded(by_spec, policy, run_date):
    rec = _rec("SPEC-CTN-KFT-350-ML", by_spec, policy, run_date)
    assert any("expired" in b["reason"] for b in rec.blocked)
    awarded_vendors = {a.vendor_id for a in rec.award}
    assert "V-SHAKTI" not in awarded_vendors, "a lapsed price is a lead, not a price"


def test_low_confidence_extraction_is_held_back(quotes, policy, run_date):
    low = next(q for q in quotes
               if q.confidence < policy.hitl_confidence_floor
               and not q.is_expired(run_date))
    ok, why = is_tradeable(low, run_date, policy)
    assert not ok and "confidence" in why


def test_import_share_is_capped(by_spec, policy, run_date):
    """Sunrise is 17% cheaper landed. It still cannot carry the whole spec:
    47-day lead times with no domestic fallback is an outage waiting on a
    container delay, not a saving."""
    rec = _rec("SPEC-JAR-PET-200-N70", by_spec, policy, run_date)
    total = sum(a.qty for a in rec.award)
    imported = sum(a.qty for a in rec.award
                   if VENDOR_BY_ID[a.vendor_id].origin == "import")
    assert imported / total <= policy.max_import_share + 1e-9
    assert len(rec.award) == 2, "a capped import share forces a real second source"


def test_split_is_repriced_at_the_split_volume(by_spec, policy, run_date):
    """The commonest way these models overstate savings is to split an order
    and keep quoting the pooled-volume price. Each half must be re-priced on
    its own quantity."""
    rec = _rec("SPEC-JAR-PET-200-N70", by_spec, policy, run_date)
    q, _ = by_spec
    for line in rec.award:
        quote = next(x for x in q["SPEC-JAR-PET-200-N70"] if x.quote_id == line.quote_id)
        from sangam.engine.costing import quote_landed
        assert line.landed_unit_cost == pytest.approx(
            quote_landed(quote, line.qty, policy))


def test_policy_threshold_actually_binds(by_spec, run_date):
    """Drop the dual-source threshold to zero and a spec that was single-source
    must restructure. Policy that does not change the output is decoration."""
    strict = SourcingPolicy(dual_source_threshold_inr=0)
    loose = SourcingPolicy(dual_source_threshold_inr=10**12)
    q, d = by_spec
    spec = "SPEC-CAP-ALU-018"
    under_loose = optimise_spec(spec, d[spec], q[spec], loose, run_date)
    under_strict = optimise_spec(spec, d[spec], q[spec], strict, run_date)
    assert "below dual-source threshold" in under_loose.mode
    assert "below dual-source threshold" not in under_strict.mode


def test_resilience_premium_is_reported_not_hidden(by_spec, policy, run_date):
    rec = _rec("SPEC-JAR-PET-200-N70", by_spec, policy, run_date)
    assert len(rec.award) > 1
    assert rec.resilience_premium is not None and rec.resilience_premium > 0, (
        "the dual award is more expensive than resting the whole spec on the "
        "cheapest import, and that difference is what resilience costs. It "
        "must be shown, not absorbed into the headline saving.")


def test_counterfactual_prices_a_phone_call(by_spec, policy, run_date):
    """Re-confirming a lapsed quote is either worth money or worth resilience.
    Either way the buyer gets a number instead of a nag."""
    q, d = by_spec
    spec = "SPEC-CTN-KFT-350-ML"
    rec = _rec(spec, by_spec, policy, run_date)
    note = counterfactual_if_reconfirmed(rec, d[spec], q[spec], policy, run_date)
    assert note and ("Rs" in note)


def test_baseline_and_award_use_the_same_cost_basis(by_spec, policy, run_date):
    """Comparing a landed proposal against a naked ex-works baseline is how
    procurement decks manufacture savings that never arrive. The baseline must
    move when the cost of capital does."""
    q, d = by_spec
    spec = "SPEC-GLS-AMB-050-N18"
    cheap = SourcingPolicy(cost_of_capital=0.01)
    dear = SourcingPolicy(cost_of_capital=0.30)
    assert (optimise_spec(spec, d[spec], q[spec], dear, run_date).baseline_cost
            < optimise_spec(spec, d[spec], q[spec], cheap, run_date).baseline_cost)


def test_no_award_when_nothing_is_tradeable(by_spec, policy):
    """Far enough into the future, every quote has lapsed. The system must
    return no award rather than silently awarding on a stale price."""
    q, d = by_spec
    spec = "SPEC-CTN-KFT-350-ML"
    rec = optimise_spec(spec, d[spec], q[spec], policy, date(2030, 1, 1))
    assert not rec.award
    assert "NO TRADEABLE PRICE" in rec.mode

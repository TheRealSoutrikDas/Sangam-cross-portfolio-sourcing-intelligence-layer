"""Landed cost evals.

Every one of these is a way a procurement model can quietly overstate a
saving. They are cheap to write and each one corresponds to a real mistake
someone has shipped.
"""
import pytest

from sangam.config import SourcingPolicy
from sangam.domain.models import PriceTier, Quote
from sangam.domain.registry import VENDOR_BY_ID
from sangam.engine.costing import applicable_tier, landed_unit_cost, quote_landed


def _quote(**kw):
    base = dict(
        quote_id="Q-T", vendor_id="V-OMPRINT", raw_description="test item",
        tiers=[PriceTier(min_qty=10_000, price=10.0),
               PriceTier(min_qty=50_000, price=9.0),
               PriceTier(min_qty=100_000, price=8.0)],
        moq=10_000, lead_time_days=15, payment_terms_days=0,
        incoterm="FOR", source_uri="test", confidence=1.0,
    )
    base.update(kw)
    return Quote(**base)


def test_below_moq_is_not_a_price(policy):
    assert applicable_tier(_quote(), 5_000) is None
    assert quote_landed(_quote(), 5_000, policy) is None


def test_tier_is_the_deepest_one_reached(policy):
    q = _quote()
    assert applicable_tier(q, 49_999).price == 10.0
    assert applicable_tier(q, 50_000).price == 9.0
    assert applicable_tier(q, 999_999).price == 8.0


def test_credit_terms_reduce_landed_cost(policy):
    vendor = VENDOR_BY_ID["V-OMPRINT"]
    on_credit = landed_unit_cost(10.0, "INR", vendor, 45, 0.0, policy)
    on_advance = landed_unit_cost(10.0, "INR", vendor, -10, 0.0, policy)
    assert on_credit < 10.0 < on_advance
    # 45 days of credit at 12% is worth roughly 1.5%
    assert 0.010 < (10.0 - on_credit) / 10.0 < 0.020


def test_import_carries_duty_freight_and_inland(policy):
    vendor = VENDOR_BY_ID["V-SUNRISE"]
    landed = landed_unit_cost(0.051, "USD", vendor, 0, 0.675, policy)
    fob_only = 0.051 * policy.fx_usd_inr
    assert landed > fob_only * 1.20, "duty and freight must not be dropped on the floor"


def test_fx_moves_only_the_foreign_component(policy):
    """Doubling the FX rate must double the FOB component and leave the
    rupee-denominated inland leg alone. A model that scales the whole landed
    cost by FX is double-counting domestic freight."""
    vendor = VENDOR_BY_ID["V-SUNRISE"]
    cheap = SourcingPolicy(fx_usd_inr=50.0, cost_of_capital=0.0)
    dear = SourcingPolicy(fx_usd_inr=100.0, cost_of_capital=0.0)
    a = landed_unit_cost(1.0, "USD", vendor, 0, 0.0, cheap)
    b = landed_unit_cost(1.0, "USD", vendor, 0, 0.0, dear)
    inland = vendor.inland_freight_inr_per_pc
    assert (b - inland) == pytest.approx(2 * (a - inland))
    assert landed_unit_cost(1.0, "INR", vendor, 0, 0.0, cheap) == pytest.approx(
        landed_unit_cost(1.0, "INR", vendor, 0, 0.0, dear)), "FX must not touch INR quotes"


def test_policy_is_injected_not_global():
    """A test must be able to run the engine under a different policy without
    patching a module global. If this ever fails, the policy has leaked into
    the code and stopped being something the sourcing lead can edit."""
    vendor = VENDOR_BY_ID["V-SUNRISE"]
    cheap_fx = SourcingPolicy(fx_usd_inr=50.0)
    dear_fx = SourcingPolicy(fx_usd_inr=100.0)
    assert (landed_unit_cost(1.0, "USD", vendor, 0, 0.0, dear_fx)
            > landed_unit_cost(1.0, "USD", vendor, 0, 0.0, cheap_fx))


def test_price_rising_with_volume_is_rejected():
    """A ladder that gets more expensive as you buy more is a misread of a
    document, not a commercial term anyone has ever offered. It must fail at
    the model boundary rather than reach the optimiser."""
    with pytest.raises(ValueError, match="rises with volume"):
        _quote(tiers=[PriceTier(min_qty=10_000, price=8.0),
                      PriceTier(min_qty=50_000, price=9.0)])


def test_quote_with_no_tiers_is_rejected():
    with pytest.raises(ValueError):
        _quote(tiers=[])

"""Landed cost.

Two vendors quoting "6.25" and "5.95" are not comparable until freight, duty,
FX and payment terms are folded in. Everything downstream works on landed
rupees per piece at the Mumbai warehouse, and nothing else.

Om Print's 45-day credit is worth about 1.5% against Shakti's advance
payment. That is the kind of difference that decides an award and never once
appears in a price comparison.
"""
from __future__ import annotations

from typing import Optional

from ..config import SourcingPolicy
from ..domain.models import PriceTier, Quote, Vendor
from ..domain.registry import VENDOR_BY_ID


def applicable_tier(quote: Quote, qty: int) -> Optional[PriceTier]:
    """The tier that applies at qty, or None if the order is below MOQ."""
    if qty < quote.moq:
        return None
    eligible = [t for t in quote.tiers if t.min_qty <= qty]
    if not eligible:
        return None
    return max(eligible, key=lambda t: t.min_qty)


def landed_unit_cost(
    price: float,
    currency: str,
    vendor: Vendor,
    payment_terms_days: int,
    freight_inr_per_pc: float,
    policy: SourcingPolicy,
) -> float:
    base = price * policy.fx_usd_inr if currency == "USD" else price
    cif = base + freight_inr_per_pc
    duty = cif * vendor.duty_pct
    inland = vendor.inland_freight_inr_per_pc if vendor.origin == "import" else 0.0
    delivered = cif + duty + inland
    # Payment terms are a real cash cost, priced at the cost of capital.
    # Positive days = credit received = the portfolio is cheaper.
    carry = delivered * policy.cost_of_capital * (payment_terms_days / 365.0)
    return delivered - carry


def quote_landed(quote: Quote, qty: int, policy: SourcingPolicy) -> Optional[float]:
    tier = applicable_tier(quote, qty)
    if tier is None:
        return None
    return landed_unit_cost(
        tier.price, tier.currency, VENDOR_BY_ID[quote.vendor_id],
        quote.payment_terms_days, quote.freight_inr_per_pc, policy,
    )

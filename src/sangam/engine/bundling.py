"""Cross-brand bundling under policy.

This stage is ordinary deterministic code and that is the point. An award
recommendation has to be reproducible, auditable and identical on every run,
because a buyer will be asked in a review why Om Print got 336,000 pieces and
"the model weighed the tradeoffs" does not survive contact with a finance
team. The LLM's job is to turn mess into structure. Arithmetic under
constraints is what code is for.

Two axes of pooling, and the second matters more than it looks:
  - across brands, so three brands buying 22k/14k/18k become one order
  - across the horizon, so 66,000/month becomes 198,000/quarter and clears a
    tier that no single month ever reaches
"""
from __future__ import annotations

from datetime import date
from itertools import permutations
from typing import Optional

from ..config import SourcingPolicy
from ..domain.models import AwardLine, DemandLine, Quote, Recommendation
from ..domain.registry import SPEC_BY_ID, VENDOR_BY_ID, VENDOR_BY_NAME
from .costing import landed_unit_cost, quote_landed

Candidate = tuple[float, list[tuple[Quote, int, float]]]


def is_tradeable(
    quote: Quote, on: date, policy: SourcingPolicy, ignore_expiry: bool = False
) -> tuple[bool, str]:
    """A price is tradeable if it can be committed today. Anything else is a
    lead, and leads do not get awarded volume."""
    if not ignore_expiry and quote.is_expired(on):
        return False, f"expired {quote.valid_until}"
    if quote.confidence < policy.hitl_confidence_floor:
        return False, f"extraction confidence {quote.confidence:.2f} below floor"
    return True, ""


def _baseline_cost(
    demand: list[DemandLine], quotes: list[Quote], policy: SourcingPolicy
) -> float:
    """What the portfolio pays today, brand by brand, on the same landed basis
    as the proposal. Comparing a landed price against a naked ex-works price
    is how procurement decks manufacture savings that never arrive."""
    total = 0.0
    months = policy.planning_horizon_months
    for line in demand:
        vendor = VENDOR_BY_NAME.get(line.vendor_name_current)
        if vendor is None:
            total += line.current_unit_price * line.monthly_qty * months
            continue
        freight = next(
            (q.freight_inr_per_pc for q in quotes if q.vendor_id == vendor.vendor_id),
            vendor.inland_freight_inr_per_pc,
        )
        unit = landed_unit_cost(
            line.current_unit_price, "INR", vendor,
            line.payment_terms_days, freight, policy,
        )
        total += unit * line.monthly_qty * months
    return total


def _award_lines(combo: list[tuple[Quote, int, float]]) -> list[AwardLine]:
    return [
        AwardLine(
            quote_id=q.quote_id,
            source=(f"{q.source_uri}:{q.source_lines[0]}-{q.source_lines[1]}"
                    if len(q.source_lines) == 2 else q.source_uri),
            vendor_id=q.vendor_id,
            vendor_name=VENDOR_BY_ID[q.vendor_id].name,
            qty=qty, landed_unit_cost=cost,
            lead_time_days=q.lead_time_days,
            payment_terms_days=q.payment_terms_days,
        )
        for q, qty, cost in combo
    ]


def _single_source_options(
    candidates: list[Quote], qty: int, policy: SourcingPolicy,
    respect_import_cap: bool = True
) -> list[Candidate]:
    out: list[Candidate] = []
    for q in candidates:
        # An import vendor may never carry a whole spec alone.
        if (respect_import_cap
                and VENDOR_BY_ID[q.vendor_id].origin == "import"
                and policy.max_import_share < 1.0):
            continue
        cost = quote_landed(q, qty, policy)
        if cost is not None:
            out.append((cost * qty, [(q, qty, cost)]))
    return sorted(out, key=lambda x: x[0])


def _dual_source_options(
    candidates: list[Quote], qty: int, policy: SourcingPolicy
) -> list[Candidate]:
    """Re-priced at the split volumes, because splitting an order moves both
    halves down the price ladder. A splitter that reuses the pooled-volume
    price is the single most common way these models overstate savings."""
    out: list[Candidate] = []
    cap = policy.max_import_share
    for primary, secondary in permutations(candidates, 2):
        share = policy.dual_source_primary_share
        if VENDOR_BY_ID[primary.vendor_id].origin == "import":
            share = min(share, cap)
        qty_p = round(qty * share)
        qty_s = qty - qty_p
        if VENDOR_BY_ID[secondary.vendor_id].origin == "import" and qty_s / qty > cap:
            continue
        cost_p = quote_landed(primary, qty_p, policy)
        cost_s = quote_landed(secondary, qty_s, policy)
        if cost_p is None or cost_s is None:
            continue
        out.append((cost_p * qty_p + cost_s * qty_s,
                    [(primary, qty_p, cost_p), (secondary, qty_s, cost_s)]))
    return sorted(out, key=lambda x: x[0])


def optimise_spec(
    spec_id: str,
    demand: list[DemandLine],
    quotes: list[Quote],
    policy: SourcingPolicy,
    on: date,
    ignore_expiry: bool = False,
) -> Recommendation:
    months = policy.planning_horizon_months
    monthly = sum(d.monthly_qty for d in demand)
    horizon_qty = monthly * months
    spec = SPEC_BY_ID.get(spec_id)

    rec = Recommendation(
        spec_id=spec_id,
        spec_label=spec.label if spec else spec_id,
        brands=sorted({d.brand for d in demand}),
        monthly_qty=monthly,
        horizon_qty=horizon_qty,
        baseline_cost=_baseline_cost(demand, quotes, policy),
    )

    candidates, blocked = [], []
    for q in quotes:
        ok, why = is_tradeable(q, on, policy, ignore_expiry)
        if ok:
            candidates.append(q)
        else:
            blocked.append({"quote_id": q.quote_id,
                            "vendor": VENDOR_BY_ID[q.vendor_id].name, "reason": why})
    rec.blocked = blocked

    if not candidates:
        rec.mode = "NO TRADEABLE PRICE - escalated"
        return rec

    singles = _single_source_options(candidates, horizon_qty, policy)
    duals = _dual_source_options(candidates, horizon_qty, policy)
    best_single = singles[0] if singles else None
    best_dual = duals[0] if duals else None

    # The premium is measured against the cheapest award the portfolio could
    # take if it accepted every risk, INCLUDING resting the whole spec on a
    # 47-day import. Measuring it against the cheapest policy-compliant single
    # award instead produces a negative "premium", which is not a premium at
    # all and means the label is lying. Caught by evals/test_bundling.py.
    unconstrained = _single_source_options(
        candidates, horizon_qty, policy, respect_import_cap=False)
    cheapest_possible = unconstrained[0][0] if unconstrained else None

    if best_single is None and best_dual is None:
        rec.mode = "NO AWARD - every candidate below MOQ at this volume"
        return rec

    reference = (best_single or best_dual)[0]
    over_threshold = reference > policy.dual_source_threshold_inr

    if over_threshold and best_dual:
        chosen, rec.mode = best_dual, "dual-source (policy)"
        if cheapest_possible is not None:
            premium = best_dual[0] - cheapest_possible
            rec.resilience_premium = premium if premium > 0 else None
    elif best_single:
        chosen = best_single
        rec.mode = (
            "single-source (POLICY EXCEPTION - no second qualified source)"
            if over_threshold else "single-source (below dual-source threshold)"
        )
    else:
        chosen, rec.mode = best_dual, "dual-source (only viable structure)"

    rec.award = _award_lines(chosen[1])
    rec.award_cost = chosen[0]
    rec.saving = rec.baseline_cost - chosen[0]
    return rec


def counterfactual_if_reconfirmed(
    rec: Recommendation,
    demand: list[DemandLine],
    quotes: list[Quote],
    policy: SourcingPolicy,
    on: date,
) -> Optional[str]:
    """What a single phone call to re-confirm a lapsed price is actually worth.

    Sometimes it is money. More often it is the only way to buy a second
    source, and the honest answer is that resilience costs something. Either
    way the buyer gets a number instead of a nag, and the work queue sorts
    itself by rupees rather than by whoever shouted loudest.
    """
    if not any("expired" in b["reason"] for b in rec.blocked):
        return None
    alt = optimise_spec(rec.spec_id, demand, quotes, policy, on, ignore_expiry=True)
    if not alt.award or rec.award_cost is None or alt.award_cost is None:
        return None
    who = " + ".join(a.vendor_name for a in alt.award)
    delta = alt.award_cost - rec.award_cost
    if delta < 0:
        return f"re-confirm the lapsed price: award becomes {who}, saving a further Rs {-delta:,.0f}"
    return (f"re-confirm the lapsed price: award becomes {who}, costing Rs {delta:,.0f} "
            f"but clearing the single-source exposure")

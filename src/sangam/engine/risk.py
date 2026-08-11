"""Risk register.

Savings are the headline. This file is the reason the system is trusted enough
to be allowed near a real PO.

Every flag names its evidence and its owner. A flag with no owner is noise,
and a queue of noise gets muted in a fortnight.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..config import SourcingPolicy
from ..domain.models import DemandLine, Quote, Recommendation, RiskFlag
from ..domain.registry import VENDOR_BY_ID, VENDOR_BY_NAME


def _short(text: str, n: int = 44) -> str:
    return text[:n]


def assess(
    recommendations: list[Recommendation],
    quotes: list[Quote],
    demand: list[DemandLine],
    policy: SourcingPolicy,
    on: date,
) -> list[RiskFlag]:
    flags: list[RiskFlag] = []
    warn_by = on + timedelta(days=policy.quote_expiring_warning_days)

    # 1. prices that have lapsed, or are about to
    for q in quotes:
        if not q.valid_until:
            continue
        vendor = VENDOR_BY_ID[q.vendor_id].name
        expiry = date.fromisoformat(q.valid_until)
        if expiry < on:
            flags.append(RiskFlag(
                severity="HIGH", code="QUOTE_EXPIRED",
                message=f"{vendor} - {_short(q.raw_description)}: price lapsed "
                        f"{q.valid_until} and cannot be committed",
                owner="Category buyer", evidence=q.source_uri))
        elif expiry <= warn_by:
            flags.append(RiskFlag(
                severity="MED", code="QUOTE_EXPIRING",
                message=f"{vendor} - {_short(q.raw_description)}: expires {q.valid_until}",
                owner="Category buyer", evidence=q.source_uri))

    # 2. the extractor's own doubt, surfaced rather than averaged away
    for q in quotes:
        if q.confidence < policy.hitl_confidence_floor:
            flags.append(RiskFlag(
                severity="MED", code="LOW_CONFIDENCE_EXTRACTION",
                message=f"{VENDOR_BY_ID[q.vendor_id].name} - {_short(q.raw_description)}: "
                        f"confidence {q.confidence:.2f}, held for human confirmation",
                owner="Sourcing analyst", evidence=q.evidence[:120]))

    # 3. concentration on a single vendor for a material spec
    for rec in recommendations:
        if len(rec.award) == 1 and (rec.award_cost or 0) > policy.dual_source_threshold_inr:
            flags.append(RiskFlag(
                severity="HIGH", code="SINGLE_SOURCE_EXPOSURE",
                message=f"{rec.spec_id}: Rs {rec.award_cost:,.0f} per quarter resting on "
                        f"one vendor with no qualified alternate",
                owner="Head of Sourcing",
                evidence=rec.counterfactual or ""))

    # 4. lead time long enough to need a safety-stock decision
    for rec in recommendations:
        for line in rec.award:
            if line.lead_time_days >= policy.lead_time_alert_days:
                months = line.lead_time_days // 30 + 1
                flags.append(RiskFlag(
                    severity="MED", code="LONG_LEAD_TIME",
                    message=f"{rec.spec_id}: {line.vendor_name} at {line.lead_time_days} days "
                            f"needs {months} months of cover before switching",
                    owner="Supply planner"))

    # 5. portfolio-level vendor concentration
    spend: dict[str, float] = {}
    total = 0.0
    for rec in recommendations:
        for line in rec.award:
            value = line.landed_unit_cost * line.qty
            spend[line.vendor_id] = spend.get(line.vendor_id, 0.0) + value
            total += value
    for vendor_id, amount in spend.items():
        share = amount / total if total else 0.0
        if share > policy.max_vendor_portfolio_share:
            flags.append(RiskFlag(
                severity="MED", code="VENDOR_CONCENTRATION",
                message=f"{VENDOR_BY_ID[vendor_id].name} would hold {share:.0%} of awarded "
                        f"spend against a policy cap of {policy.max_vendor_portfolio_share:.0%}",
                owner="Head of Sourcing"))

    # 6. commercial terms resting on reconstruction rather than a signed quote
    for q in quotes:
        if "NO PRIMARY QUOTE" in q.evidence.upper():
            flags.append(RiskFlag(
                severity="MED", code="NO_PRIMARY_QUOTE",
                message=f"{VENDOR_BY_ID[q.vendor_id].name} - {_short(q.raw_description)}: "
                        f"tiers inferred from PO history, no signed quote on file",
                owner="Category buyer", evidence=q.source_uri))

    # 7. incumbents we are still paying with nothing on file at all
    quoted = {q.vendor_id for q in quotes}
    for line in demand:
        vendor = VENDOR_BY_NAME.get(line.vendor_name_current)
        if vendor and vendor.vendor_id not in quoted:
            flags.append(RiskFlag(
                severity="MED", code="NO_QUOTE_ON_FILE",
                message=f"{vendor.name} supplies {line.brand} at "
                        f"{line.current_unit_price:.2f}/pc with no quote in the system, "
                        f"so the price is unbenchmarked",
                owner="Category buyer", evidence="erp_current_po_lines.csv"))

    return _dedupe(flags)


def harmonisation_candidates(unmatched: list[tuple[str, str]]) -> list[RiskFlag]:
    """Adjacent specs are where the next tranche of savings lives. Two brands
    on 300 gsm and 350 gsm kraft are one design decision away from one pooled
    order. This is a proposal to a human, never an automatic substitution:
    whether a carton can change caliper is a brand and structural question,
    and the system's job is to surface it and then stop."""
    return [
        RiskFlag(severity="INFO", code="SPEC_HARMONISATION",
                 message=f"'{desc[:50]}' matched no canonical spec: {note}",
                 owner="Brand + Packaging design")
        for desc, note in unmatched
    ]


def _dedupe(flags: list[RiskFlag]) -> list[RiskFlag]:
    seen, out = set(), []
    for f in flags:
        key = (f.code, f.message)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out

"""The sourcing council brief.

The output is written for the meeting where the award actually gets argued
about, which means every number carries what a buyer will immediately ask:
against what baseline, on what volume, with what left unresolved.
"""
from __future__ import annotations

from ..domain.models import Recommendation, RiskFlag

WIDTH = 78


def _rule(char: str = "-") -> str:
    return char * WIDTH


def _heading(title: str) -> list[str]:
    return ["", _rule("="), f"  {title}", _rule("=")]


def _inr(value: float) -> str:
    return f"Rs {value:,.0f}"


def render(state: dict) -> str:
    out: list[str] = []
    coverage = state.get("coverage", {})
    totals = state.get("totals", {})

    out += _heading("SANGAM  |  cross-portfolio sourcing brief")
    out.append(f"  run date {state.get('run_date', '')}   "
               f"extraction: {state.get('extraction_mode', 'unknown')}")

    out += _heading("1. CANONICALISATION")
    out.append(f"  supply side : {coverage.get('supply_resolved')}/"
               f"{coverage.get('supply_total')} quote lines resolved")
    out.append(f"  demand side : {coverage.get('demand_resolved')}/"
               f"{coverage.get('demand_total')} PO lines resolved")
    out.append("")
    for spec_id, count in (coverage.get("variants_per_spec") or {}).items():
        out.append(f"  {spec_id:<24} <- {count} distinct descriptions")
    if state.get("unmatched"):
        out.append("")
        out.append("  Held for human review, never auto-substituted:")
        for item in state["unmatched"]:
            out.append(f"    - {item['description'][:46]:<46} {item['note'][:60]}")

    out += _heading("2. AWARD RECOMMENDATIONS")
    for record in state.get("recommendations", []):
        rec = Recommendation.model_validate(record)
        out.append("")
        out.append(f"  {rec.spec_id}  {rec.spec_label}")
        out.append(_rule())
        out.append(f"  pooled from : {', '.join(rec.brands)}")
        out.append(f"  volume      : {rec.monthly_qty:,}/month -> "
                   f"{rec.horizon_qty:,} committed this quarter")
        out.append(f"  award mode  : {rec.mode}")
        for line in rec.award:
            out.append(f"    -> {line.vendor_name:<24}{line.qty:>9,} pc  "
                       f"@ landed {line.landed_unit_cost:>5.2f}  "
                       f"({line.lead_time_days}d, {line.payment_terms_days:+d}d terms)")
            if line.source:
                out.append(f"       terms read from {line.source}  [{line.quote_id}]")
        if rec.award:
            delta = (rec.award_unit - rec.baseline_unit) / rec.baseline_unit
            out.append(f"  landed/pc   : {rec.baseline_unit:.2f} today  ->  "
                       f"{rec.award_unit:.2f}   ({delta:+.1%})")
            out.append(f"  quarter     : {_inr(rec.baseline_cost)} -> "
                       f"{_inr(rec.award_cost)}   saving {_inr(rec.saving)}")
        if rec.resilience_premium and rec.resilience_premium > 0:
            out.append(f"  resilience  : {_inr(rec.resilience_premium)} paid against the "
                       f"cheapest single award, to hold a second qualified vendor")
        for blocked in rec.blocked:
            out.append(f"  blocked     : {blocked['vendor']} - {blocked['reason']}")
        if rec.counterfactual:
            out.append(f"  ACTION      : {rec.counterfactual}")

    out += _heading("3. RISK REGISTER")
    flags = [RiskFlag.model_validate(f) for f in state.get("risk_flags", [])]
    for severity in ("HIGH", "MED", "INFO"):
        for flag in [f for f in flags if f.severity == severity]:
            out.append(f"  [{severity:<4}] {flag.code:<26} owner: {flag.owner}")
            out.append(f"         {flag.message}")
            if flag.evidence:
                out.append(f"         evidence: {flag.evidence[:62]}")

    out += _heading("4. BOTTOM LINE")
    out.append(f"  addressable spend this quarter : {_inr(totals.get('baseline', 0))}")
    out.append(f"  optimised spend                : {_inr(totals.get('awarded', 0))}")
    out.append(f"  saving                         : {_inr(totals.get('saving', 0))}  "
               f"({totals.get('saving_pct', 0):.1%})")
    out.append(f"  annualised                     : {_inr(totals.get('annualised', 0))}")
    out.append("")
    out.append(f"  ...on {totals.get('specs', 0)} specs across {totals.get('brands', 0)} "
               f"brands. Organizations runs 30+ brands and a packaging")
    out.append("  catalogue two orders of magnitude wider than this repository.")
    out.append("")
    high = sum(1 for f in flags if f.severity == "HIGH")
    med = sum(1 for f in flags if f.severity == "MED")
    out.append(f"  human decisions required before any PO : {high} high, {med} medium")

    if state.get("negotiation_drafts"):
        out += _heading("5. NEGOTIATION DRAFTS (for the buyer to send, or not)")
        out.append(state["negotiation_drafts"])

    out.append(_rule("="))
    return "\n".join(out)

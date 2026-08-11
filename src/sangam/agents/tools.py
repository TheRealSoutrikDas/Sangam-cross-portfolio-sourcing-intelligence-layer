"""The engine, exposed as tools an agent can call.

This module is the answer to a real tension. A sourcing system has to be
agentic, because the questions buyers actually ask are open-ended ("what
happens if we drop Shakti entirely?", "why did Om Print win?", "can we get
Krishna qualified?") and there is no fixed pipeline that answers all of them.
But an award recommendation has to be exact, because a buyer will be asked in
a review to defend it.

Both are satisfied by the same move: **the model decides what to compute, and
deterministic code computes it.** The agent has genuine latitude over which
question to ask, which vendor to exclude, which volume to test, how many
simulations to run and in what order. It has no latitude at all over the
arithmetic that comes back. Every number an agent quotes in this system came
out of `sangam.engine`, which is the same code path the batch pipeline uses
and is covered by its own tests.

The alternative, letting a model do the arithmetic in its own head and
narrate the result, would be more "agentic" in the shallow sense and would
produce a system whose numbers change between runs. That is not a sourcing
system, it is a plausible-sounding one.
"""
from __future__ import annotations

from datetime import date

from google.adk.tools import ToolContext

from ..config import load_policy
from ..domain.models import DemandLine, Quote, Recommendation
from ..domain.registry import SPEC_BY_ID, VENDOR_BY_ID
from ..engine.bundling import is_tradeable, optimise_spec
from ..engine.canonicalize import canonicalize
from ..engine.costing import applicable_tier, quote_landed


def _load(ctx: ToolContext) -> tuple[list[Quote], list[DemandLine], date]:
    quotes = [Quote.model_validate(q) for q in ctx.state.get("quotes", [])]
    demand = [DemandLine.model_validate(d) for d in ctx.state.get("demand", [])]
    run_date = date.fromisoformat(ctx.state.get("run_date") or date.today().isoformat())
    return quotes, demand, run_date


def _for_spec(spec_id: str, quotes, demand):
    return ([q for q in quotes if q.spec_id == spec_id],
            [d for d in demand if d.spec_id == spec_id])


# --------------------------------------------------------------- inspection

def list_specs(tool_context: ToolContext) -> dict:
    """List every canonical spec the portfolio buys, with the brands buying it
    and the current monthly volume. Start here when you do not yet know which
    spec a question is about.
    """
    _, demand, _ = _load(tool_context)
    out = []
    for spec_id, spec in SPEC_BY_ID.items():
        lines = [d for d in demand if d.spec_id == spec_id]
        if not lines:
            continue
        out.append({
            "spec_id": spec_id,
            "label": spec.label,
            "brands": sorted({d.brand for d in lines}),
            "monthly_qty": sum(d.monthly_qty for d in lines),
        })
    return {"specs": out}


def explain_award(spec_id: str, tool_context: ToolContext) -> dict:
    """Explain why a spec was awarded the way it was: who won, at what landed
    cost, which candidates were blocked and why, and what the alternatives
    cost. Use this before answering any "why did X win" question, rather than
    reasoning about it from the brief.

    Args:
        spec_id: canonical spec id, e.g. SPEC-CTN-KFT-350-ML.
    """
    policy = load_policy()
    quotes, demand, run_date = _load(tool_context)
    spec_quotes, spec_demand = _for_spec(spec_id, quotes, demand)
    if not spec_demand:
        return {"error": f"no portfolio demand for {spec_id}", "known": list(SPEC_BY_ID)}

    rec = optimise_spec(spec_id, spec_demand, spec_quotes, policy, run_date)
    horizon = rec.horizon_qty

    candidates = []
    for q in spec_quotes:
        tradeable, why = is_tradeable(q, run_date, policy)
        landed = quote_landed(q, horizon, policy)
        candidates.append({
            "vendor": VENDOR_BY_ID[q.vendor_id].name,
            "landed_at_full_volume": round(landed, 3) if landed else None,
            "lead_time_days": q.lead_time_days,
            "payment_terms_days": q.payment_terms_days,
            "origin": VENDOR_BY_ID[q.vendor_id].origin,
            "tradeable": tradeable,
            "blocked_because": why or None,
            "extraction_confidence": q.confidence,
        })

    return {
        "spec_id": spec_id,
        "label": rec.spec_label,
        "award_mode": rec.mode,
        "awarded": [a.model_dump() for a in rec.award],
        "horizon_qty": horizon,
        "baseline_landed_per_pc": round(rec.baseline_unit, 3),
        "awarded_landed_per_pc": round(rec.award_unit, 3),
        "quarterly_saving": round(rec.saving),
        "resilience_premium": (round(rec.resilience_premium)
                               if rec.resilience_premium else None),
        "all_candidates": candidates,
    }


def price_at_volume(spec_id: str, vendor_name: str, qty: int,
                    tool_context: ToolContext) -> dict:
    """Get one vendor's real landed cost per piece for a specific quantity,
    including freight, duty, FX and the value of their payment terms.

    Use this to check any price before you state it. Never quote a number you
    have not obtained from this tool: a tier boundary or a payment term you
    reasoned about in your head will be wrong often enough to matter.

    Args:
        spec_id: canonical spec id.
        vendor_name: vendor name as it appears in the portfolio.
        qty: the order quantity to price, in pieces.
    """
    policy = load_policy()
    quotes, _, _ = _load(tool_context)
    match = [q for q in quotes if q.spec_id == spec_id
             and VENDOR_BY_ID[q.vendor_id].name.lower() == vendor_name.lower()]
    if not match:
        return {"error": f"no quote on file for {vendor_name} on {spec_id}"}

    quote = match[0]
    tier = applicable_tier(quote, qty)
    if tier is None:
        return {"error": f"{qty:,} is below {vendor_name}'s MOQ of {quote.moq:,}",
                "moq": quote.moq}
    landed = quote_landed(quote, qty, policy)
    return {
        "vendor": vendor_name,
        "qty": qty,
        "list_price": tier.price,
        "currency": tier.currency,
        "landed_per_pc_inr": round(landed, 3),
        "tier_reached": tier.min_qty,
        "next_tier": min((t.min_qty for t in quote.tiers if t.min_qty > qty), default=None),
        "lead_time_days": quote.lead_time_days,
        "payment_terms_days": quote.payment_terms_days,
        "valid_until": quote.valid_until,
    }


# --------------------------------------------------------------- simulation

def simulate_award(spec_id: str, tool_context: ToolContext,
                   exclude_vendor: str = "", include_expired: bool = False,
                   dual_source_threshold_inr: float = -1.0,
                   max_import_share: float = -1.0) -> dict:
    """Re-run the award for one spec under changed assumptions, and report the
    difference against the current award.

    This is the tool for every "what if" question. Change one thing at a time
    and compare, rather than trying to reason about the interaction between a
    tier boundary, an MOQ and a policy cap.

    Args:
        spec_id: canonical spec id.
        exclude_vendor: vendor name to remove from consideration entirely.
            Use for "what if we lost X" and "what if we dropped X".
        include_expired: treat lapsed quotes as committable. Use to price what
            re-confirming a price with a vendor would be worth.
        dual_source_threshold_inr: override the policy threshold. -1 keeps policy.
        max_import_share: override the import cap, 0.0 to 1.0. -1 keeps policy.
    """
    policy = load_policy()
    if dual_source_threshold_inr >= 0:
        policy = policy.model_copy(update={"dual_source_threshold_inr": dual_source_threshold_inr})
    if max_import_share >= 0:
        policy = policy.model_copy(update={"max_import_share": max_import_share})

    quotes, demand, run_date = _load(tool_context)
    spec_quotes, spec_demand = _for_spec(spec_id, quotes, demand)
    if not spec_demand:
        return {"error": f"no portfolio demand for {spec_id}"}

    baseline_rec = optimise_spec(spec_id, spec_demand, spec_quotes, load_policy(), run_date)

    if exclude_vendor:
        spec_quotes = [q for q in spec_quotes
                       if VENDOR_BY_ID[q.vendor_id].name.lower() != exclude_vendor.lower()]

    altered = optimise_spec(spec_id, spec_demand, spec_quotes, policy, run_date,
                            ignore_expiry=include_expired)

    delta = ((altered.award_cost or 0) - (baseline_rec.award_cost or 0)
             if altered.award and baseline_rec.award else None)
    return {
        "spec_id": spec_id,
        "assumptions_changed": {
            "exclude_vendor": exclude_vendor or None,
            "include_expired": include_expired or None,
            "dual_source_threshold_inr": (dual_source_threshold_inr
                                          if dual_source_threshold_inr >= 0 else None),
            "max_import_share": max_import_share if max_import_share >= 0 else None,
        },
        "current_award": [a.model_dump() for a in baseline_rec.award],
        "current_cost": round(baseline_rec.award_cost) if baseline_rec.award_cost else None,
        "simulated_award": [a.model_dump() for a in altered.award],
        "simulated_cost": round(altered.award_cost) if altered.award_cost else None,
        "simulated_mode": altered.mode,
        "cost_delta": round(delta) if delta is not None else None,
        "note": ("no viable award under these assumptions"
                 if not altered.award else None),
    }


def check_spec_match(description: str, tool_context: ToolContext) -> dict:
    """Resolve a free-text item description to a canonical spec, or explain
    which attribute stops it matching.

    Use this whenever someone refers to an item in their own words. Do not
    guess which spec they mean: "300 gsm" and "350 gsm" are different specs
    and treating them as one is the most expensive error this system can make.

    Args:
        description: the item description as written, verbatim.
    """
    result = canonicalize(description)
    if result.spec_id:
        return {"description": description, "spec_id": result.spec_id,
                "label": SPEC_BY_ID[result.spec_id].label, "evidence": result.evidence}
    return {"description": description, "spec_id": None,
            "why_not": result.adjacency_note,
            "action": "this is a harmonisation question for packaging design, "
                      "not something to resolve by picking the nearest spec"}


def open_risks(tool_context: ToolContext, severity: str = "") -> dict:
    """List the open risk flags, each with its owner and evidence.

    Args:
        severity: optional filter, one of HIGH, MED, INFO.
    """
    flags = tool_context.state.get("risk_flags", [])
    if severity:
        flags = [f for f in flags if f["severity"] == severity.upper()]
    return {"count": len(flags), "flags": flags}


# --------------------------------------------------------------- provenance

def show_source(quote_id: str, tool_context: ToolContext) -> dict:
    """Return the actual document text a quote's terms were read from.

    Use this whenever someone asks what a vendor said, whether a price is
    real, or why a record looks odd. Structured terms lose everything the
    document said around them: the board-index revision clause, the vendor
    chasing an answer, the fact that a rate was negotiated down rather than
    offered. Those sentences are often what a buyer actually needs, and they
    are the difference between reporting a number and being able to defend it.

    Quote what the document says. Never paraphrase a commercial term into
    something firmer than the vendor wrote.

    Args:
        quote_id: the quote id, e.g. Q003. Obtain it from explain_award.
    """
    from ..config import RAW_DIR

    quotes, _, _ = _load(tool_context)
    match = [q for q in quotes if q.quote_id == quote_id]
    if not match:
        return {"error": f"no quote {quote_id}",
                "known": [q.quote_id for q in quotes]}

    quote = match[0]
    path = RAW_DIR / quote.source_uri
    if not path.exists():
        return {"error": f"source document {quote.source_uri} is not available"}

    lines = path.read_text().splitlines()
    if quote.source_lines and len(quote.source_lines) == 2:
        start, end = quote.source_lines
        start, end = max(1, start), min(len(lines), end)
        excerpt = "\n".join(f"{n:>4}  {lines[n - 1]}" for n in range(start, end + 1))
        span = f"lines {start}-{end}"
    else:
        # No span recorded. Return the whole document rather than guessing a
        # location: a wrong excerpt is worse than a long one, because it looks
        # like evidence.
        excerpt = "\n".join(f"{n:>4}  {line}" for n, line in enumerate(lines, 1))
        span = "whole document, no span recorded"

    return {
        "quote_id": quote_id,
        "vendor": VENDOR_BY_ID[quote.vendor_id].name,
        "item_as_vendor_wrote_it": quote.raw_description,
        "document": quote.source_uri,
        "span": span,
        "excerpt": excerpt,
        "extractor_confidence": quote.confidence,
        "extractor_evidence": quote.evidence,
    }


def find_in_sources(search_text: str, tool_context: ToolContext) -> dict:
    """Search the raw vendor documents for a phrase and return the matching
    lines with their context.

    Use this for questions the structured data cannot answer: what a vendor
    said about a delay, whether anyone mentioned a price revision clause, who
    chased whom and when. Search the vendor's likely wording, not the
    canonical spec label, because these documents are written in the trade's
    own shorthand and in more than one language.

    Args:
        search_text: a phrase to look for, case-insensitive.
    """
    from ..config import RAW_DIR

    needle = search_text.lower().strip()
    if len(needle) < 3:
        return {"error": "search for at least three characters"}

    hits = []
    for path in sorted(RAW_DIR.iterdir()):
        if not path.is_file():
            continue
        lines = path.read_text().splitlines()
        for n, line in enumerate(lines, 1):
            if needle in line.lower():
                lo, hi = max(1, n - 1), min(len(lines), n + 1)
                hits.append({
                    "document": path.name,
                    "line": n,
                    "context": "\n".join(f"{i:>4}  {lines[i - 1]}" for i in range(lo, hi + 1)),
                })
    return {"query": search_text, "match_count": len(hits), "matches": hits[:12]}

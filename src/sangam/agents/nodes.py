"""The deterministic nodes of the graph.

Each function here is a thin adapter: read typed state, call a pure function
in `sangam.engine`, write typed state back. The business logic stays testable
without an event loop, a session service or a model.

If you want to know what this system decides and why, read `engine/`. This
file only decides when.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from google.adk.agents import Context

from ..config import load_policy
from ..domain.models import DemandLine, Quote, Recommendation
from ..domain.registry import SPEC_BY_ID
from ..engine.bundling import counterfactual_if_reconfirmed, optimise_spec
from ..engine.canonicalize import canonicalize
from ..engine.risk import assess, harmonisation_candidates
from ..extraction.ingest import load_demand, read_documents


def ingest_sources(ctx: Context) -> str:
    """Pull vendor artefacts and the ERP demand export into state.

    In production the documents arrive from a shared procurement mailbox, the
    WhatsApp Business API and an ERP nightly export. Swapping those in changes
    this function and nothing downstream, which is the reason ingestion is a
    node rather than a step inside the extractor.
    """
    documents = read_documents()
    demand = load_demand()
    ctx.state["documents"] = documents
    # The extractor is an LlmAgent, so the only way it sees a document is
    # through its prompt. Rendering happens here rather than in the agent so
    # that swapping the connectors later changes one node and nothing else.
    # Line numbers are included because the extractor must cite a span, and a
    # citation it had to guess at is worse than none.
    ctx.state["documents_text"] = "\n\n".join(
        f"<document uri=\"{d['uri']}\">\n"
        + "\n".join(f"{n:>4}  {line}" for n, line in enumerate(d["text"].splitlines(), 1))
        + "\n</document>"
        for d in documents)
    ctx.state["demand"] = [d.model_dump() for d in demand]
    ctx.state["run_date"] = ctx.state.get("run_date") or date.today().isoformat()
    brands = {d.brand for d in demand}
    return (f"Ingested {len(documents)} vendor documents and {len(demand)} "
            f"demand lines across {len(brands)} brands.")


def canonicalise(ctx: Context, quotes: list[dict], demand: list[dict]) -> str:
    """Resolve every free-text description on both sides to a canonical spec.

    Runs over supply and demand together because they share one matcher. A
    system that normalises vendor quotes with different logic than it uses on
    its own PO history will produce a comparison that is wrong in a way nobody
    can see.
    """
    unmatched: list[dict] = []

    resolved_quotes = []
    for record in quotes:
        quote = Quote.model_validate(record)
        match = canonicalize(quote.raw_description)
        quote.spec_id = match.spec_id
        quote.match_evidence = match.evidence
        quote.adjacency_note = match.adjacency_note
        if match.spec_id is None:
            unmatched.append({"side": "supply", "description": quote.raw_description,
                              "note": match.adjacency_note})
        resolved_quotes.append(quote.model_dump())

    resolved_demand = []
    for record in demand:
        line = DemandLine.model_validate(record)
        match = canonicalize(line.raw_description)
        line.spec_id = match.spec_id
        line.adjacency_note = match.adjacency_note
        if match.spec_id is None:
            unmatched.append({"side": "demand", "description": line.raw_description,
                              "note": match.adjacency_note})
        resolved_demand.append(line.model_dump())

    variants: dict[str, set[str]] = defaultdict(set)
    for record in resolved_quotes + resolved_demand:
        if record.get("spec_id"):
            variants[record["spec_id"]].add(record["raw_description"])

    ctx.state["quotes"] = resolved_quotes
    ctx.state["demand"] = resolved_demand
    ctx.state["unmatched"] = unmatched
    ctx.state["coverage"] = {
        "supply_resolved": sum(1 for q in resolved_quotes if q["spec_id"]),
        "supply_total": len(resolved_quotes),
        "demand_resolved": sum(1 for d in resolved_demand if d["spec_id"]),
        "demand_total": len(resolved_demand),
        "variants_per_spec": {k: len(v) for k, v in sorted(variants.items())},
    }
    return (f"Resolved {len(resolved_quotes) + len(resolved_demand) - len(unmatched)} of "
            f"{len(resolved_quotes) + len(resolved_demand)} descriptions to "
            f"{len(variants)} canonical specs. {len(unmatched)} held for human review.")


def optimise_portfolio(
    ctx: Context, quotes: list[dict], demand: list[dict], run_date: str
) -> str:
    """Pool demand across brands and across the horizon, then award under policy."""
    policy = load_policy()
    on = date.fromisoformat(run_date)

    demand_by_spec: dict[str, list[DemandLine]] = defaultdict(list)
    quotes_by_spec: dict[str, list[Quote]] = defaultdict(list)
    for record in demand:
        line = DemandLine.model_validate(record)
        if line.spec_id:
            demand_by_spec[line.spec_id].append(line)
    for record in quotes:
        quote = Quote.model_validate(record)
        if quote.spec_id:
            quotes_by_spec[quote.spec_id].append(quote)

    recommendations: list[Recommendation] = []
    for spec_id in SPEC_BY_ID:
        if not demand_by_spec[spec_id]:
            continue
        rec = optimise_spec(spec_id, demand_by_spec[spec_id],
                            quotes_by_spec[spec_id], policy, on)
        rec.counterfactual = counterfactual_if_reconfirmed(
            rec, demand_by_spec[spec_id], quotes_by_spec[spec_id], policy, on)
        recommendations.append(rec)

    baseline = sum(r.baseline_cost for r in recommendations if r.award)
    awarded = sum(r.award_cost or 0.0 for r in recommendations if r.award)
    ctx.state["recommendations"] = [r.model_dump() for r in recommendations]
    ctx.state["totals"] = {
        "baseline": baseline,
        "awarded": awarded,
        "saving": baseline - awarded,
        "saving_pct": (baseline - awarded) / baseline if baseline else 0.0,
        "annualised": (baseline - awarded) * (12 / policy.planning_horizon_months),
        "specs": len(recommendations),
        "brands": len({d["brand"] for d in demand}),
    }
    return (f"Awarded {len(recommendations)} specs. Baseline Rs {baseline:,.0f} to "
            f"Rs {awarded:,.0f}, saving Rs {baseline - awarded:,.0f}.")


def register_risks(
    ctx: Context,
    quotes: list[dict],
    demand: list[dict],
    recommendations: list[dict],
    unmatched: list[dict],
    run_date: str,
) -> str:
    """Everything the system refuses to decide on its own, routed to an owner."""
    policy = load_policy()
    flags = assess(
        [Recommendation.model_validate(r) for r in recommendations],
        [Quote.model_validate(q) for q in quotes],
        [DemandLine.model_validate(d) for d in demand],
        policy,
        date.fromisoformat(run_date),
    )
    flags += harmonisation_candidates([(u["description"], u["note"]) for u in unmatched])
    ctx.state["risk_flags"] = [f.model_dump() for f in flags]

    high = sum(1 for f in flags if f.severity == "HIGH")
    med = sum(1 for f in flags if f.severity == "MED")
    return f"{high} high and {med} medium items require a human before any PO is raised."


def prepare_negotiation_briefs(ctx: Context, recommendations: list[dict]) -> str:
    """Assemble the facts a negotiation ask rests on.

    The agent that drafts the ask does not choose the target, the volume or
    the leverage. It is handed settled numbers and asked to write. Keeping
    that boundary is what stops a language model from inventing a commitment
    the portfolio has not made.
    """
    briefs = []
    for record in recommendations:
        rec = Recommendation.model_validate(record)
        if not rec.award:
            continue
        for line in rec.award:
            leverage = [
                f"pooled volume of {line.qty:,} pieces per quarter across "
                f"{len(rec.brands)} brands ({', '.join(rec.brands)})",
                f"portfolio is moving from Rs {rec.baseline_unit:.2f} to "
                f"Rs {rec.award_unit:.2f} landed on this spec",
            ]
            if len(rec.award) > 1:
                other = [a.vendor_name for a in rec.award if a.vendor_id != line.vendor_id]
                leverage.append(f"a qualified second source is already awarded volume "
                                f"on this spec ({', '.join(other)})")
            briefs.append({
                "vendor_name": line.vendor_name,
                "spec_label": rec.spec_label,
                "current_landed": round(line.landed_unit_cost, 3),
                "target_landed": round(line.landed_unit_cost * 0.96, 3),
                "volume": line.qty,
                "leverage": leverage,
            })
    ctx.state["negotiation_briefs"] = briefs

    # Assemble the harmonisation input too: the adjacent-spec findings plus the
    # volumes that make them worth someone's attention.
    unmatched = ctx.state.get("unmatched", [])
    ctx.state["harmonisation_input"] = ""
    if unmatched:
        lines = ["Adjacent specs found. For each, write a proposal for packaging design.", ""]
        for item in unmatched:
            lines.append(f"- '{item['description']}' :: {item['note']}")
        lines.append("")
        lines.append("Portfolio volumes on the matched specs, for sizing the prize:")
        for record in recommendations:
            rec = Recommendation.model_validate(record)
            lines.append(f"- {rec.spec_id} ({rec.spec_label}): {rec.monthly_qty:,}/month "
                         f"across {len(rec.brands)} brands, landed "
                         f"Rs {rec.award_unit:.2f}/pc")
        ctx.state["harmonisation_input"] = "\n".join(lines)

    return (f"Prepared {len(briefs)} negotiation briefs and "
            f"{len(unmatched)} harmonisation candidates for review.")

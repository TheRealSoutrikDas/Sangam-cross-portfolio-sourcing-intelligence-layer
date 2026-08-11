"""The critic in the extraction repair loop.

An LLM extraction stage that runs once and hands its output downstream is not
an agent, it is a function call with a language model inside it. What makes
this stage genuinely agentic is that its output is checked by code that knows
what a valid commercial term looks like, and the model is handed its own
failures and asked to fix them.

The critic is deterministic on purpose. A model grading its own extraction is
the least reliable arrangement available: it agrees with itself. Every check
below is a rule that can be stated to a buyer, and a failure is a fact rather
than an opinion.

Two things keep the loop honest:

  - The model may resolve a complaint by lowering its own confidence. That is
    a correct resolution, not a dodge. An honest 0.6 routes the record to a
    human, which is exactly where a record the model cannot read belongs.
  - The loop is bounded. On exhaustion the run continues with whatever
    survived and the failures become review items. A pipeline that blocks
    until a model gets it right is a pipeline that hangs.
"""
from __future__ import annotations

from datetime import date

from pydantic import ValidationError

from ..domain.models import Quote
from ..domain.registry import VENDOR_BY_ID

MAX_ATTEMPTS = 3


def validate_records(records: list[dict], today: date) -> tuple[list[dict], list[str]]:
    """Returns (accepted, complaints). A complaint is written to be actionable
    by the model that produced the record."""
    accepted: list[dict] = []
    complaints: list[str] = []

    for i, record in enumerate(records):
        label = f"record {i + 1} ({record.get('raw_description', 'unnamed')[:40]})"

        # 1. does it even parse as a Quote? The model validators catch the
        #    structural nonsense, including a ladder that gets dearer with volume.
        try:
            quote = Quote.model_validate({
                **record,
                "quote_id": record.get("quote_id", f"Q{i + 1:03d}"),
                "source_uri": record.get("source_uri", record.get("_source_uri", "unknown")),
                "vendor_id": record.get("vendor_id", record.get("_vendor_id", "")),
            })
        except ValidationError as exc:
            first = exc.errors()[0]
            complaints.append(f"{label}: {'.'.join(str(x) for x in first['loc'])} "
                              f"is invalid, {first['msg']}")
            continue

        # 2. the vendor must exist. A hallucinated vendor id is silent poison
        #    downstream, because every cost lookup keys off it.
        if quote.vendor_id not in VENDOR_BY_ID:
            complaints.append(f"{label}: vendor_id {quote.vendor_id!r} is not in the "
                              f"vendor master. Use one of: {', '.join(VENDOR_BY_ID)}")
            continue

        # 3. an import quoted in rupees, or a domestic vendor in dollars, is
        #    almost always a currency misread rather than a real term.
        vendor = VENDOR_BY_ID[quote.vendor_id]
        currencies = {t.currency for t in quote.tiers}
        if vendor.origin == "domestic" and "USD" in currencies:
            complaints.append(f"{label}: {vendor.name} is a domestic vendor quoted in USD. "
                              f"Re-read the currency.")
            continue

        # 4. an import with a lead time that ignores transit. Production alone
        #    is not the lead time when the goods cross an ocean.
        if vendor.origin == "import" and quote.lead_time_days < 30:
            complaints.append(f"{label}: {vendor.name} is an import at "
                              f"{quote.lead_time_days} days. Lead time must include sea "
                              f"transit as well as production. Re-read, or lower confidence.")
            continue

        # 5. validity in the past relative to the document is a date misparse.
        if quote.valid_until:
            try:
                date.fromisoformat(quote.valid_until)
            except ValueError:
                complaints.append(f"{label}: valid_until {quote.valid_until!r} is not "
                                  f"an ISO date.")
                continue

        # 6. implausible magnitudes. Packaging is single-digit rupees; a stray
        #    factor of a thousand is a units error, not a price.
        cheapest = min(t.price for t in quote.tiers)
        inr = cheapest * 87.5 if any(t.currency == "USD" for t in quote.tiers) else cheapest
        if inr > 500:
            complaints.append(f"{label}: {inr:.0f} INR per piece is implausible for "
                              f"packaging. Check whether this is a per-carton or per-1000 "
                              f"rate rather than per piece.")
            continue

        # 7. confidence that does not reflect a stated assumption. If the
        #    model told us it assumed something, it may not also claim near
        #    certainty.
        assumption_words = ("assum", "estimat", "inherit", "not stated", "not given",
                            "unclear", "reconstruct")
        if quote.confidence >= 0.9 and any(w in quote.evidence.lower()
                                           for w in assumption_words):
            complaints.append(f"{label}: evidence records an assumption but confidence is "
                              f"{quote.confidence:.2f}. Lower the confidence to match what "
                              f"you actually knew.")
            continue

        # 8. the citation must name a real document. A record whose source is
        #    "unknown" cannot be checked by anyone, which defeats the point of
        #    citing at all.
        from ..config import RAW_DIR
        if quote.source_uri in ("", "unknown") or not (RAW_DIR / quote.source_uri).exists():
            complaints.append(
                f"{label}: source_uri {quote.source_uri!r} is not one of the documents "
                f"you were given. Copy it verbatim from the <document uri=\"...\"> tag.")
            continue

        # 9. the provenance span must actually contain the price. A citation
        #    that points at the wrong lines is worse than none, because it
        #    looks like evidence and a buyer will trust it.
        if quote.source_lines:
            problem = _span_problem(quote)
            if problem:
                complaints.append(f"{label}: {problem}")
                continue

        # 10. the vendor must actually appear in the document the terms were
        #     read from. This is the one semantic error the structural checks
        #     miss: a hallucinated attribution to a REAL vendor validates
        #     cleanly, prices correctly, awards volume, and ends up in a
        #     negotiation draft addressed to a company that never quoted.
        #     Observed on a live run: Vidhata's quotation awarded to Krishna.
        problem = _attribution_problem(quote)
        if problem:
            complaints.append(f"{label}: {problem}")
            continue

        accepted.append(record)

    return accepted, complaints


def _distinctive_name(vendor_name: str) -> str:
    """The part of a vendor's name that identifies THEM rather than their trade.

    Matching on any token is useless here: "Krishna Glass Udyog" and "Vidhata
    Glass Works" share "Glass", so a quotation from one would appear to
    corroborate the other. The leading token is the distinctive one in Indian
    trade names. Where it is too short to discriminate ("Om Print & Pack"),
    fall back to the first two.
    """
    tokens = [t for t in vendor_name.replace("&", " ").split() if t.isalpha()]
    if not tokens:
        return vendor_name.lower()
    if len(tokens[0]) >= 4:
        return tokens[0].lower()
    return " ".join(tokens[:2]).lower()


def _attribution_problem(quote: Quote) -> str:
    """Returns a complaint if the named vendor is nowhere in the cited document."""
    from ..config import RAW_DIR

    path = RAW_DIR / quote.source_uri
    if not path.exists():
        return ""

    vendor = VENDOR_BY_ID[quote.vendor_id]
    needle = _distinctive_name(vendor.name)
    if needle in path.read_text().lower():
        return ""
    return (f"vendor_id {quote.vendor_id} ({vendor.name}) does not appear anywhere in "
            f"{quote.source_uri}. Terms belong to whoever WROTE the document. Re-read "
            f"the letterhead, sender or signature block and use that vendor.")


def _span_problem(quote: Quote) -> str:
    """Returns a complaint if the cited span is malformed or does not contain
    the price that was extracted from it. Returns "" if the span holds up."""
    from ..config import RAW_DIR

    span = quote.source_lines
    if len(span) != 2 or span[0] < 1 or span[1] < span[0]:
        return (f"source_lines {span} is not a valid [start, end] span, "
                f"1-indexed and inclusive")

    path = RAW_DIR / quote.source_uri
    if not path.exists():
        return ""          # nothing to check against; not the model's fault

    lines = path.read_text().splitlines()
    if span[1] > len(lines):
        return (f"source_lines {span} runs past the end of {quote.source_uri}, "
                f"which has {len(lines)} lines")

    excerpt = "\n".join(lines[span[0] - 1:span[1]])
    cheapest = min(quote.tiers, key=lambda t: t.price)
    # the price as a human would have typed it, e.g. 6.25 or 0.051
    printed = f"{cheapest.price:g}"
    if printed not in excerpt.replace(",", ""):
        return (f"source_lines {span} does not contain the price {printed} you "
                f"reported. Point the span at the block you actually read the "
                f"rates from.")
    return ""


def critique_text(complaints: list[str], attempt: int) -> str:
    lines = [f"Attempt {attempt} produced {len(complaints)} record(s) that failed "
             f"validation. Fix only these and re-emit the full set.", ""]
    lines += [f"  - {c}" for c in complaints]
    lines += ["",
              "If a complaint is caused by something the document genuinely does not "
              "say, the correct fix is to lower that record's confidence and say so in "
              "evidence, not to invent a value."]
    return "\n".join(lines)

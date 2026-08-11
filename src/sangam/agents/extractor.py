"""The extraction stage, as a repair loop.

Not one model call. A cycle:

    extract  ->  validate  ->  route
                    |            |
                    |            +-- invalid, attempts left -> back to extract
                    |                 with a written critique in state
                    +-- valid or exhausted -> continue

This is the first place agency genuinely earns its keep. The model is not
being asked to be right first time on a Hinglish WhatsApp thread where the
11:40 message supersedes the 11:09 rate. It is being asked to produce
something, be told precisely how it failed by code that knows what a valid
commercial term looks like, and try again.

The critic is deterministic. A model grading its own extraction agrees with
itself, which is the least useful review available.
"""
from __future__ import annotations

from datetime import date

from google.adk.agents import Context, LlmAgent
from google.adk.workflow import FunctionNode

from ..config import model_name, model_retry_config
from ..domain.registry import VENDORS
from ..extraction.ingest import load_golden_quotes, quotes_from_extraction
from ..extraction.validate import MAX_ATTEMPTS, critique_text, validate_records
from .schema_bridge import parse_extraction_output


def _vendor_master() -> str:
    return "\n".join(f"  {v.vendor_id}: {v.name} ({v.location}, {v.origin})"
                     for v in VENDORS)


def build_live_extractor() -> LlmAgent:
    from ..extraction.schema import EXTRACTION_INSTRUCTION, ExtractionResult

    return LlmAgent(
        name="extractor",
        retry_config=model_retry_config(),
        model=model_name(),
        description="Reads unstructured vendor documents into structured commercial "
                    "terms with provenance and self-reported confidence.",
        # replace, not format: {documents_text} and {extraction_critique} must
        # survive into the instruction so ADK substitutes them from session
        # state on every pass. That is what makes the repair loop a loop -
        # without it the model retries blind, having never been told what it
        # got wrong.
        instruction=EXTRACTION_INSTRUCTION.replace("{vendor_master}", _vendor_master()),
        output_schema=ExtractionResult,
        output_key="extraction_raw",
    )


def materialise_extraction(ctx: Context, extraction_raw: dict) -> str:
    """Live model output to candidate records."""
    records = parse_extraction_output(extraction_raw)
    ctx.state["candidate_records"] = records
    ctx.state["extraction_mode"] = f"live extraction via {model_name()}"
    return f"Model returned {len(records)} candidate records."


def cached_extraction(ctx: Context, documents: list[dict]) -> str:
    """Replay a verified extraction set.

    Not a mock. These records go through the same validator the live path
    does, so a corrupted fixture fails exactly as loudly as a hallucinating
    model would.
    """
    quotes = load_golden_quotes()
    ctx.state["candidate_records"] = [q.model_dump() for q in quotes]
    ctx.state["extraction_mode"] = "cached golden set (set GOOGLE_API_KEY for the live path)"
    return f"Replayed {len(quotes)} records from the golden set."


def validate_extraction(ctx: Context, candidate_records: list[dict],
                        run_date: str, extraction_attempt: int = 0) -> str:
    """Deterministic critic. Sets ctx.route to drive the loop."""
    attempt = (extraction_attempt or 0) + 1
    accepted, complaints = validate_records(candidate_records,
                                            date.fromisoformat(run_date))
    ctx.state["extraction_attempt"] = attempt

    if not complaints:
        ctx.state["quotes"] = [q.model_dump() for q in quotes_from_extraction(accepted)]
        ctx.state["extraction_critique"] = ""
        ctx.state["extraction_rejected"] = []
        ctx.route = "accept"
        return f"All {len(accepted)} records passed validation on attempt {attempt}."

    if attempt >= MAX_ATTEMPTS:
        # Bounded. The run continues on what survived and the rest becomes
        # review work. A pipeline that blocks until a model gets it right is a
        # pipeline that hangs.
        ctx.state["quotes"] = [q.model_dump() for q in quotes_from_extraction(accepted)]
        ctx.state["extraction_critique"] = ""
        ctx.state["extraction_rejected"] = complaints
        ctx.route = "accept"
        return (f"Attempt {attempt} exhausted the retry budget. Proceeding with "
                f"{len(accepted)} valid records; {len(complaints)} escalated to review.")

    ctx.state["extraction_critique"] = critique_text(complaints, attempt)
    ctx.route = "repair"
    return f"Attempt {attempt}: {len(complaints)} record(s) failed. Sending back for repair."


def build_cached_extractor() -> FunctionNode:
    return FunctionNode(name="extractor", func=cached_extraction)

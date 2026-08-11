"""The workflow's typed state.

ADK validates every FunctionNode's parameters against this schema when the
graph is built, which means a node that reads a key nothing writes is a
construction-time error rather than a KeyError forty seconds into a run. That
is most of the value of using the graph API over hand-rolled orchestration.

State is JSON at rest, so everything here dumps cleanly. Domain objects go in
as `model_dump()` and come back out through pydantic coercion.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SourcingState(BaseModel):
    # ingestion
    documents: list[dict] = Field(default_factory=list)
    documents_text: str = Field(
        default="",
        description="The documents rendered with line numbers for the extractor's "
                    "prompt. An LlmAgent only sees what its instruction contains.")
    run_date: str = ""
    extraction_mode: str = ""

    # extraction
    # `extraction_raw` is the LlmAgent's output_key on the live path. It has to
    # be declared even though only one node reads it: ADK validates every
    # FunctionNode parameter against this schema at graph-build time, and the
    # live graph failed to construct until this line existed. That failure
    # arriving from `build_workflow` instead of from a live run forty seconds
    # in is the whole reason for declaring state.
    extraction_raw: dict = Field(
        default_factory=dict,
        description="The extractor's output. ADK writes the PARSED object here, not a "
                    "string, because the agent declares an output_schema. Typing this "
                    "as str fails at runtime with a StateSchemaError.")
    candidate_records: list[dict] = Field(default_factory=list)
    extraction_attempt: int = 0
    extraction_critique: str = Field(
        default="",
        description="Written by the deterministic validator, read by the extractor on "
                    "the next pass. This field IS the repair loop.")
    extraction_rejected: list[str] = Field(default_factory=list)
    quotes: list[dict] = Field(default_factory=list)
    demand: list[dict] = Field(default_factory=list)

    # canonicalisation
    unmatched: list[dict] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)

    # optimisation
    recommendations: list[dict] = Field(default_factory=list)
    totals: dict = Field(default_factory=dict)

    # risk
    risk_flags: list[dict] = Field(default_factory=list)

    # negotiation and the agentic tail
    negotiation_briefs: list[dict] = Field(default_factory=list)
    negotiation_drafts: str = ""
    harmonisation_input: str = ""
    harmonisation_proposals: str = ""

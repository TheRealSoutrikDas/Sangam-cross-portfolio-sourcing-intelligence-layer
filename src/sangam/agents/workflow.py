"""The graph.

                          START
                            |
                            v
                       [ ingest ]                       deterministic
                            |
                            v
                    +--> [ extractor ] <----+           LLM
                    |         |             |
                    |         v             | repair, with a written
                    |   [ validate ]--------+ critique in state
                    |         |
                    |         | accept
                    |         v
                    |  [ canonicalise ]                 deterministic
                    |         |
                    |         v
                    |    [ optimise ]                   deterministic
                    |         |
                    |         v
                    |      [ risk ]                     deterministic
                    |         |
                    |         v
                    |      [ brief ]                    deterministic
                    |         |
                    |         v
                    |  [ harmonisation_analyst ]        LLM
                    |         |
                    |         v
                    |    [ negotiator ]                 LLM
                    |
                    +-- bounded at 3 attempts

and above the graph, the surface a buyer actually uses:

       [ sourcing_analyst ]                             LLM
         tools: the engine, exactly as the pipeline runs it
         sub-agents: vendor_scout (web search), harmonisation_analyst

**Where agency earns its keep, and where it does not.**

Four stages here are agentic in a way that matters. Extraction runs as a
repair loop against a deterministic critic, because reading a Hinglish
WhatsApp thread is hard and the failures are machine-checkable. The vendor
scout runs an open-ended web search, because finding a second source takes an
unknown number of steps. The harmonisation analyst writes judgement, because
whether a carton can change caliper is not in the data. The sourcing analyst
chooses what to compute in response to a question nobody anticipated.

The award arithmetic is not one of them, and that is deliberate. A buyer will
be asked in a review why Om Print got 336,000 pieces. "The model weighed the
tradeoffs" does not survive that question; "here is the landed cost of every
candidate at that volume, and here is the policy that bound the split" does.
Making the arithmetic agentic would buy nothing and cost the property that
makes the recommendations usable.

So the analyst has genuine latitude over *which* computation to run, and none
at all over the result. Every figure it quotes comes out of `sangam.engine`,
the same code the batch pipeline uses, covered by the same tests.
"""
from __future__ import annotations

from google.adk import Workflow
from google.adk.agents import LlmAgent
from google.adk.workflow import Edge, FunctionNode, START

from ..config import llm_available
from . import nodes
from .analyst import build_analyst
from .extractor import (build_cached_extractor, build_live_extractor,
                        materialise_extraction, validate_extraction)
from .harmoniser import build_harmoniser
from .negotiator import build_negotiator
from .state import SourcingState


def build_workflow(live: bool | None = None) -> Workflow:
    """The batch pipeline.

    `live` is resolved from configuration when not passed. The graph shape is
    identical either way apart from the extraction strategy and the two
    drafting agents at the tail, so an offline run exercises the same
    orchestration, the same validator and the same repair cycle that the
    production run does.
    """
    live = llm_available() if live is None else live

    ingest = FunctionNode(name="ingest", func=nodes.ingest_sources)
    validate = FunctionNode(name="validate_extraction", func=validate_extraction)
    canonicalise = FunctionNode(name="canonicalise", func=nodes.canonicalise)
    optimise = FunctionNode(name="optimise", func=nodes.optimise_portfolio)
    risk = FunctionNode(name="risk", func=nodes.register_risks)
    brief = FunctionNode(name="brief", func=nodes.prepare_negotiation_briefs)

    edges: list = [(START, ingest)]

    if live:
        extractor = build_live_extractor()
        materialise = FunctionNode(name="materialise_extraction",
                                   func=materialise_extraction)
        edges += [(ingest, extractor), (extractor, materialise), (materialise, validate)]
        repair_target = extractor
    else:
        extractor = build_cached_extractor()
        edges += [(ingest, extractor), (extractor, validate)]
        repair_target = extractor

    # The repair cycle. The validator routes back to the extractor, which sees
    # its own failures in state and tries again.
    edges += [
        Edge(from_node=validate, to_node=repair_target, route="repair"),
        Edge(from_node=validate, to_node=canonicalise, route="accept"),
        (canonicalise, optimise),
        (optimise, risk),
        (risk, brief),
    ]

    if live:
        harmoniser = build_harmoniser()
        edges += [(brief, harmoniser), (harmoniser, build_negotiator())]

    return Workflow(
        name="sangam_pipeline",
        description="Cross-portfolio sourcing pipeline. Reads unstructured "
                    "vendor artefacts, resolves every brand's free-text descriptions to "
                    "canonical specs, normalises to landed cost, and awards pooled "
                    "volume under written policy.",
        state_schema=SourcingState,
        edges=edges,
    )


def build_root_agent(live: bool | None = None) -> LlmAgent:
    """The conversational surface, which is what `adk web` loads.

    The analyst is the root because it is what a buyer talks to. The batch
    pipeline still runs and still produces the quarterly brief; the analyst is
    how anyone interrogates the result.
    """
    return build_analyst()

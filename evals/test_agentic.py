"""Evals for the agentic layer.

Two things are worth testing here and they are different in kind.

The repair loop is testable end to end without a model: a stub extractor that
emits bad records first and good records later proves the cycle actually
cycles, that the critique reaches the next attempt, and that the retry budget
is enforced. That is the part where a bug would be silent in production.

The tool surface is testable for the property that actually matters: that
every number an agent can quote came out of the deterministic engine and
matches what the batch pipeline computed. An agent that could disagree with
the quarterly brief would be worse than no agent.
"""
import asyncio
from datetime import date

import pytest
from google.adk import Workflow
from google.adk.agents import Context
from google.adk.runners import InMemoryRunner
from google.adk.workflow import Edge, FunctionNode, START
from google.genai import types

from sangam.agents import nodes, tools
from sangam.agents.analyst import build_analyst
from sangam.agents.extractor import validate_extraction
from sangam.agents.state import SourcingState
from sangam.agents.workflow import build_root_agent, build_workflow
from sangam.extraction.validate import MAX_ATTEMPTS, validate_records
from sangam.domain.registry import VENDOR_BY_ID

APP = "sangam-agentic-test"
RUN_DATE = "2026-08-11"


# --------------------------------------------------------------- the critic

def _record(**kw):
    base = {
        "vendor_id": "V-OMPRINT", "raw_description": "Mono carton kraft 350gsm 4c matt",
        "tiers": [{"min_qty": 50000, "price": 6.25, "currency": "INR"}],
        "moq": 50000, "lead_time_days": 15, "payment_terms_days": 45,
        "incoterm": "FOR", "valid_until": "2026-09-30", "confidence": 0.94,
        "evidence": "rate card row 2", "source_uri": "om_print_ratecard.txt",
        "source_lines": [7, 15],
    }
    base.update(kw)
    return base


def test_clean_records_pass():
    accepted, complaints = validate_records([_record()], date(2026, 8, 11))
    assert len(accepted) == 1 and not complaints


def test_hallucinated_vendor_is_caught():
    """A vendor id that does not exist is silent poison: every cost lookup
    downstream keys off it."""
    _, complaints = validate_records([_record(vendor_id="V-NOTREAL")], date(2026, 8, 11))
    assert complaints and "vendor master" in complaints[0]


def test_domestic_vendor_quoted_in_usd_is_caught():
    _, complaints = validate_records(
        [_record(tiers=[{"min_qty": 50000, "price": 0.07, "currency": "USD"}])],
        date(2026, 8, 11))
    assert complaints and "USD" in complaints[0]


def test_import_lead_time_ignoring_transit_is_caught():
    """Production time alone is not the lead time when the goods cross an ocean."""
    _, complaints = validate_records(
        [_record(vendor_id="V-SUNRISE", lead_time_days=25,
                 tiers=[{"min_qty": 30000, "price": 0.06, "currency": "USD"}])],
        date(2026, 8, 11))
    assert complaints and "transit" in complaints[0]


def test_units_error_is_caught():
    _, complaints = validate_records(
        [_record(tiers=[{"min_qty": 50000, "price": 6250.0}])], date(2026, 8, 11))
    assert complaints and "implausible" in complaints[0]


def test_confidence_must_reflect_stated_assumptions():
    """If the model says it assumed something, it may not also claim near
    certainty. This is the check that keeps the confidence floor meaningful."""
    _, complaints = validate_records(
        [_record(confidence=0.95, evidence="freight estimated, vendor did not state it")],
        date(2026, 8, 11))
    assert complaints and "confidence" in complaints[0]


def test_price_rising_with_volume_is_caught():
    _, complaints = validate_records([_record(tiers=[
        {"min_qty": 50000, "price": 5.0}, {"min_qty": 100000, "price": 6.0}])],
        date(2026, 8, 11))
    assert complaints


def test_golden_set_passes_its_own_critic(quotes):
    """The fixture must satisfy the same validator live output does, or it is
    not a golden set, it is a bag of records that happen to parse."""
    accepted, complaints = validate_records(
        [q.model_dump() for q in quotes], date(2026, 8, 11))
    assert not complaints, complaints
    assert len(accepted) == len(quotes)


# ----------------------------------------------------------- the repair loop

class _StubExtractor:
    """Emits bad records until `fixes_on_attempt`, then clean ones. Stands in
    for the LlmAgent so the cycle can be tested without a model."""

    def __init__(self, fixes_on_attempt: int):
        self.fixes_on_attempt = fixes_on_attempt
        self.calls = 0
        self.critiques_seen: list[str] = []

    def __call__(self, ctx: Context) -> str:
        self.calls += 1
        critique = ctx.state.get("extraction_critique", "")
        if critique:
            self.critiques_seen.append(critique)
        bad = self.calls < self.fixes_on_attempt
        ctx.state["candidate_records"] = [
            _record(vendor_id="V-NOTREAL" if bad else "V-OMPRINT")]
        ctx.state["extraction_mode"] = "stub"
        return f"stub attempt {self.calls}"


def _run_loop(stub) -> dict:
    extractor = FunctionNode(name="extractor", func=stub)
    validate = FunctionNode(name="validate_extraction", func=validate_extraction)
    done = FunctionNode(name="done", func=lambda ctx: "done")
    wf = Workflow(name="loop_test", state_schema=SourcingState, edges=[
        (START, extractor),
        (extractor, validate),
        Edge(from_node=validate, to_node=extractor, route="repair"),
        Edge(from_node=validate, to_node=done, route="accept"),
    ])

    async def go():
        runner = InMemoryRunner(agent=wf, app_name=APP)
        session = await runner.session_service.create_session(
            app_name=APP, user_id="t", state={"run_date": RUN_DATE})
        async for _ in runner.run_async(
            user_id="t", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="go")])):
            pass
        final = await runner.session_service.get_session(
            app_name=APP, user_id="t", session_id=session.id)
        return dict(final.state)
    return asyncio.run(go())


def test_loop_repairs_and_exits():
    """The cycle must actually cycle: fail, be told why, succeed, move on."""
    stub = _StubExtractor(fixes_on_attempt=2)
    state = _run_loop(stub)
    assert stub.calls == 2, "the extractor should have been re-run exactly once"
    assert state["extraction_attempt"] == 2
    assert state["quotes"], "the accepted records must reach downstream state"
    assert not state["extraction_rejected"]


def test_critique_actually_reaches_the_next_attempt():
    """The loop is worthless if the model cannot see why it failed. This is
    the assertion that makes it a repair loop rather than a retry."""
    stub = _StubExtractor(fixes_on_attempt=2)
    _run_loop(stub)
    assert stub.critiques_seen, "no critique was visible on the second attempt"
    assert "vendor master" in stub.critiques_seen[0]
    assert "Attempt 1" in stub.critiques_seen[0]


def test_retry_budget_is_bounded_and_run_continues():
    """A model that never gets it right must not hang the pipeline. The run
    proceeds on what survived and the rest becomes review work."""
    stub = _StubExtractor(fixes_on_attempt=99)
    state = _run_loop(stub)
    assert stub.calls == MAX_ATTEMPTS
    assert state["extraction_rejected"], "failures must be escalated, not discarded silently"
    assert state["quotes"] == []


def test_lowering_confidence_is_an_acceptable_repair():
    """A record the model genuinely cannot read should end up at a low
    confidence and be routed to a human, not be invented into validity."""
    accepted, complaints = validate_records(
        [_record(confidence=0.6, evidence="freight estimated, vendor did not state it")],
        date(2026, 8, 11))
    assert not complaints and len(accepted) == 1


# --------------------------------------------------------- the tool surface

class _FakeToolContext:
    def __init__(self, state):
        self.state = state


@pytest.fixture(scope="module")
def pipeline_state():
    async def go():
        runner = InMemoryRunner(agent=build_workflow(live=False), app_name=APP)
        session = await runner.session_service.create_session(
            app_name=APP, user_id="t", state={"run_date": RUN_DATE})
        async for _ in runner.run_async(
            user_id="t", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="go")])):
            pass
        final = await runner.session_service.get_session(
            app_name=APP, user_id="t", session_id=session.id)
        return dict(final.state)
    return asyncio.run(go())


@pytest.fixture
def ctx(pipeline_state):
    return _FakeToolContext(dict(pipeline_state))


def test_tools_and_pipeline_cannot_disagree(ctx, pipeline_state):
    """The property that makes the analyst safe to put in front of a buyer: a
    number quoted in conversation and the same number in the quarterly brief
    come from one code path."""
    for record in pipeline_state["recommendations"]:
        if not record["award"]:
            continue
        told = tools.explain_award(record["spec_id"], ctx)
        assert told["awarded"] == record["award"]
        assert told["quarterly_saving"] == round(record["saving"])


def test_explain_award_surfaces_the_blocked_candidates(ctx):
    """"Why did Om Print win" is usually answered by what was blocked, not by
    what won."""
    told = tools.explain_award("SPEC-CTN-KFT-350-ML", ctx)
    blocked = [c for c in told["all_candidates"] if not c["tradeable"]]
    assert blocked and any("expired" in (c["blocked_because"] or "") for c in blocked)


def test_simulate_excluding_a_vendor_changes_the_award(ctx):
    """The "what if we lost X" question, which is the one buyers actually ask."""
    result = tools.simulate_award("SPEC-GLS-AMB-050-N18", ctx,
                                  exclude_vendor="Vidhata Glass Works")
    winners = {a["vendor_name"] for a in result["current_award"]}
    assert "Vidhata Glass Works" in winners
    assert "Vidhata Glass Works" not in {a["vendor_name"] for a in result["simulated_award"]}


def test_simulate_prices_reconfirming_a_lapsed_quote(ctx):
    """A risk flag with a rupee value attached is a decision. Without one it
    is a nag."""
    result = tools.simulate_award("SPEC-CTN-KFT-350-ML", ctx, include_expired=True)
    assert result["cost_delta"] is not None
    assert "Shakti Packaging" in {a["vendor_name"] for a in result["simulated_award"]}


def test_price_at_volume_respects_moq(ctx):
    below = tools.price_at_volume("SPEC-CTN-KFT-350-ML", "Om Print & Pack", 1000, ctx)
    assert "error" in below and "MOQ" in below["error"]


def test_price_at_volume_matches_the_engine(ctx):
    from sangam.config import load_policy
    from sangam.domain.models import Quote
    from sangam.engine.costing import quote_landed
    result = tools.price_at_volume("SPEC-CTN-KFT-350-ML", "Om Print & Pack", 336000, ctx)
    quote = next(Quote.model_validate(q) for q in ctx.state["quotes"]
                 if q["spec_id"] == "SPEC-CTN-KFT-350-ML"
                 and VENDOR_BY_ID[q["vendor_id"]].name == "Om Print & Pack")
    assert result["landed_per_pc_inr"] == pytest.approx(
        round(quote_landed(quote, 336000, load_policy()), 3))


def test_check_spec_match_refuses_to_guess(ctx):
    """The tool an agent reaches for when a buyer uses their own words. It must
    refuse a near-miss rather than pick the closest spec."""
    ok = tools.check_spec_match("kraft carton 350gsm matt lam 4c", ctx)
    assert ok["spec_id"] == "SPEC-CTN-KFT-350-ML"
    near = tools.check_spec_match("kraft carton 300gsm matt lam 4c", ctx)
    assert near["spec_id"] is None and "gsm=300" in near["why_not"]


def test_tools_never_expose_a_mutating_operation():
    """Nothing the analyst holds can raise a PO, contact a vendor or change
    policy. The blast radius of a confused agent is a wrong answer, not a
    wrong order."""
    public = [n for n in dir(tools) if not n.startswith("_") and callable(getattr(tools, n))]
    forbidden = ("send", "email", "order", "commit", "approve", "write", "post", "delete")
    assert not [n for n in public if any(f in n.lower() for f in forbidden)]


# --------------------------------------------------------------- assembly

def test_analyst_holds_the_engine_and_both_sub_agents():
    analyst = build_analyst()
    names = [getattr(t, "name", None) or getattr(getattr(t, "agent", None), "name", "")
             for t in analyst.tools]
    fn_names = [getattr(t, "__name__", "") for t in analyst.tools]
    assert "vendor_scout" in names
    assert "harmonisation_analyst" in names
    for expected in ("explain_award", "simulate_award", "price_at_volume"):
        assert expected in fn_names


def test_root_agent_is_the_analyst():
    assert build_root_agent().name == "sourcing_analyst"


def test_live_graph_contains_the_repair_cycle_and_the_drafting_tail():
    names = [n.name for n in build_workflow(live=True).graph.nodes]
    for expected in ("extractor", "validate_extraction", "harmonisation_analyst",
                     "negotiator"):
        assert expected in names


# --------------------------------------------------------------- provenance

def test_every_quote_cites_a_span(quotes):
    """A number nobody can trace back to a sentence is a number a buyer cannot
    defend in a review."""
    missing = [q.quote_id for q in quotes
               if q.source_uri != "erp_current_po_lines.csv" and not q.source_lines]
    assert not missing, f"no provenance span on {missing}"


def test_cited_span_actually_contains_the_price(quotes):
    """The check that keeps provenance honest. A citation pointing at the
    wrong lines is worse than none, because it looks like evidence."""
    from sangam.config import RAW_DIR
    for quote in quotes:
        if not quote.source_lines:
            continue
        lines = (RAW_DIR / quote.source_uri).read_text().splitlines()
        start, end = quote.source_lines
        excerpt = "\n".join(lines[start - 1:end]).replace(",", "")
        cheapest = min(quote.tiers, key=lambda t: t.price)
        assert f"{cheapest.price:g}" in excerpt, (
            f"{quote.quote_id} cites lines {start}-{end} but the price "
            f"{cheapest.price:g} is not there")


def test_validator_rejects_a_span_that_misses_the_price():
    """A model that cites plausible-looking but wrong lines must be caught."""
    _, complaints = validate_records(
        [_record(source_lines=[1, 3], source_uri="om_print_ratecard.txt")],
        date(2026, 8, 11))
    assert complaints and "does not contain the price" in complaints[0]


def test_validator_rejects_a_span_past_end_of_document():
    _, complaints = validate_records(
        [_record(source_lines=[900, 950], source_uri="om_print_ratecard.txt")],
        date(2026, 8, 11))
    assert complaints and "past the end" in complaints[0]


def test_show_source_returns_the_document_not_a_summary(ctx):
    """The whole point: the buyer reads the vendor's own words."""
    result = tools.show_source("Q003", ctx)
    assert "5.95" in result["excerpt"], "the negotiated rate should be in the excerpt"
    assert "11:40" in result["excerpt"], "the message that superseded should be visible"
    assert result["document"] == "whatsapp_shakti_packaging.txt"
    assert result["extractor_confidence"] > 0


def test_show_source_is_honest_when_no_span_recorded(ctx):
    """The ERP-reconstructed record has no document span. It must return the
    whole file rather than guess a location, because a wrong excerpt looks
    like evidence."""
    state = dict(ctx.state)
    target = next(q for q in state["quotes"] if not q["source_lines"])
    result = tools.show_source(target["quote_id"], _FakeToolContext(state))
    assert "error" in result or "no span recorded" in result["span"]


def test_find_in_sources_answers_what_structured_data_cannot(ctx):
    """The board-index revision clause is nowhere in the structured terms, and
    it is exactly the sort of thing that decides whether a price holds."""
    result = tools.find_in_sources("board index", ctx)
    assert result["match_count"] >= 1
    assert "om_print_ratecard.txt" in result["matches"][0]["document"]


def test_find_in_sources_reaches_the_vendors_own_language(ctx):
    """These documents are written in the trade's shorthand and in more than
    one language. A search that only works on canonical labels is useless."""
    result = tools.find_in_sources("expire ho raha hai", ctx)
    assert result["match_count"] >= 1


# ------------------------------------------------- the prompt actually carries

def test_extractor_prompt_carries_the_documents():
    """An LlmAgent only sees what its instruction contains. Building the graph
    correctly and never putting the documents in the prompt produces a model
    that politely returns nothing, which is what happened on the first live
    run of this system."""
    from sangam.agents.extractor import build_live_extractor
    instruction = build_live_extractor().instruction
    assert "{documents_text}" in instruction


def test_extractor_prompt_carries_the_critique():
    """Without this the repair loop is a retry loop: the model tries again
    having never been told what it got wrong."""
    from sangam.agents.extractor import build_live_extractor
    assert "{extraction_critique?}" in build_live_extractor().instruction


def test_placeholders_absent_on_first_pass_are_marked_optional():
    """ADK raises KeyError rather than substituting empty when an instruction
    references a state key that does not exist yet. Two keys here are written
    by a LATER node than the agent that reads them: the extraction critique
    exists only after a failed attempt, and the harmonisation brief only when
    adjacent specs were found. Both must use the `{var?}` form.

    This is a build-time-invisible, runtime-fatal class of bug: the graph
    constructs fine and dies on the first live call.
    """
    from sangam.agents.extractor import build_live_extractor
    from sangam.agents.harmoniser import build_harmoniser
    assert "{extraction_critique}" not in build_live_extractor().instruction
    assert "{harmonisation_input}" not in build_harmoniser().instruction


def test_keys_written_before_their_reader_stay_required():
    """The converse. `documents_text` is written by ingest, which runs before
    the extractor, so a missing value there is a real bug and should fail
    loudly rather than silently prompting the model with nothing."""
    from sangam.agents.extractor import build_live_extractor
    assert "{documents_text}" in build_live_extractor().instruction
    assert "{documents_text?}" not in build_live_extractor().instruction


def test_harmonisation_input_is_always_written():
    """Even when there is nothing to harmonise, so the agent receives an empty
    brief rather than a missing one."""
    state = pipeline_state_dict()
    assert "harmonisation_input" in state


def pipeline_state_dict() -> dict:
    async def go():
        runner = InMemoryRunner(agent=build_workflow(live=False), app_name=APP)
        session = await runner.session_service.create_session(
            app_name=APP, user_id="t", state={"run_date": RUN_DATE})
        async for _ in runner.run_async(
            user_id="t", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="go")])):
            pass
        final = await runner.session_service.get_session(
            app_name=APP, user_id="t", session_id=session.id)
        return dict(final.state)
    return asyncio.run(go())


def test_state_placeholders_survive_instruction_assembly():
    """The vendor master is substituted at build time; the state-backed
    placeholders must NOT be, or ADK has nothing to fill on each pass."""
    from sangam.agents.extractor import build_live_extractor
    instruction = build_live_extractor().instruction
    assert "{vendor_master}" not in instruction
    assert "V-VIDHATA" in instruction


def test_ingest_renders_documents_with_line_numbers():
    """The extractor has to cite a line span, so the prompt has to show line
    numbers. A citation the model guessed at is worse than none."""
    state = _run_loop(_StubExtractor(fixes_on_attempt=1))
    # the loop stub does not run ingest, so exercise the real pipeline state
    text = pipeline_state_for_docs()
    assert "<document uri=" in text
    assert "   1  " in text


def pipeline_state_for_docs() -> str:
    async def go():
        runner = InMemoryRunner(agent=build_workflow(live=False), app_name=APP)
        session = await runner.session_service.create_session(
            app_name=APP, user_id="t", state={"run_date": RUN_DATE})
        async for _ in runner.run_async(
            user_id="t", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="go")])):
            pass
        final = await runner.session_service.get_session(
            app_name=APP, user_id="t", session_id=session.id)
        return dict(final.state)["documents_text"]
    return asyncio.run(go())


def test_extraction_raw_is_typed_as_the_parsed_object():
    """ADK writes the parsed object to output_key when an agent declares an
    output_schema. Typing this field as str fails at runtime, not at build
    time, which is the worst place to find it."""
    from sangam.agents.state import SourcingState
    SourcingState(extraction_raw={"quotes": []})
    with pytest.raises(Exception):
        SourcingState(extraction_raw="a string")


# ------------------------------------------------------------ rate limiting

def test_every_model_agent_has_a_retry_policy():
    """This pipeline makes several model calls and the extraction repair loop
    alone can burn three. On a free-tier quota of a few requests per minute,
    a run without backoff dies partway through and loses the work that already
    succeeded. Found on the first successful live run."""
    from sangam.agents.extractor import build_live_extractor
    from sangam.agents.harmoniser import build_harmoniser
    from sangam.agents.negotiator import build_negotiator
    for build in (build_live_extractor, build_harmoniser, build_negotiator):
        agent = build()
        assert agent.retry_config is not None, f"{agent.name} has no retry policy"
        assert agent.retry_config.max_attempts >= 2
        assert agent.retry_config.backoff_factor > 1, "retries must back off"


def test_retries_are_scoped_to_transient_failures():
    """A 400 or a schema violation is a bug. Retrying it just makes the same
    mistake more slowly, and burns quota that the repair loop needs."""
    from sangam.config import model_retry_config
    scoped = " ".join(model_retry_config().exceptions)
    assert "_ResourceExhaustedError" in scoped


def test_retry_exceptions_are_bare_class_names():
    """ADK matches retry_config.exceptions against type(exception).__name__.
    A dotted import path looks correct, passes construction, and silently
    never matches, so the retry policy is attached and inert. Caught on a live
    run where a configured backoff did nothing."""
    from sangam.config import model_retry_config
    for name in model_retry_config().exceptions:
        assert "." not in name, (
            f"{name!r} is a dotted path; ADK compares against the bare class "
            f"name and this will never match")


def test_the_actual_rate_limit_exception_would_match():
    """Pin it to the real class rather than a string I believe is right."""
    from google.adk.models.google_llm import _ResourceExhaustedError
    from sangam.config import model_retry_config
    assert _ResourceExhaustedError.__name__ in model_retry_config().exceptions


def test_uncheckable_citation_is_rejected():
    """A record whose source document cannot be opened defeats the point of
    citing at all. The first live run produced 'terms read from unknown:10-22'
    because the extraction schema never asked for the document name."""
    _, complaints = validate_records(
        [_record(source_uri="unknown")], date(2026, 8, 11))
    assert complaints and "not one of the documents" in complaints[0]


def test_extraction_schema_asks_for_the_source_document():
    from sangam.extraction.schema import ExtractedQuote
    assert "source_uri" in ExtractedQuote.model_fields

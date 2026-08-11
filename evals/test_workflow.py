"""End-to-end evals.

These drive the real ADK graph through a real Runner and session service, not
the node functions called in order. The orchestration is part of what has to
keep working: a state key that stops being written is exactly the kind of
regression that unit tests on `engine/` will never see.
"""
import asyncio

import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types

from sangam.agents.state import SourcingState
from sangam.agents.workflow import build_workflow
from sangam.reporting.brief import render

APP = "sangam-test"


def _run(run_date="2026-08-11") -> dict:
    async def go():
        workflow = build_workflow(live=False)
        runner = InMemoryRunner(agent=workflow, app_name=APP)
        session = await runner.session_service.create_session(
            app_name=APP, user_id="test", state={"run_date": run_date})
        async for _ in runner.run_async(
            user_id="test", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="run")]),
        ):
            pass
        final = await runner.session_service.get_session(
            app_name=APP, user_id="test", session_id=session.id)
        return dict(final.state)
    return asyncio.run(go())


@pytest.fixture(scope="module")
def state():
    return _run()


def test_graph_builds_with_the_expected_nodes():
    names = [n.name for n in build_workflow(live=False).graph.nodes]
    assert names == ["__START__", "ingest", "extractor", "validate_extraction",
                     "canonicalise", "optimise", "risk", "brief"]


def test_live_graph_adds_the_llm_stages():
    """The graph shape is the same offline and live apart from the extraction
    strategy and the drafting tail. The validator and the repair cycle are
    common to both, so an offline run is evidence about the production run."""
    names = [n.name for n in build_workflow(live=True).graph.nodes]
    for expected in ("extractor", "materialise_extraction", "validate_extraction",
                     "harmonisation_analyst", "negotiator"):
        assert expected in names


def test_state_conforms_to_the_declared_schema(state):
    SourcingState.model_validate(state)


def test_every_stage_wrote_its_output(state):
    for key in ("documents", "quotes", "demand", "coverage", "candidate_records",
                "recommendations", "totals", "risk_flags", "negotiation_briefs"):
        assert state.get(key), f"{key} was never written: a stage silently did nothing"


def test_run_is_deterministic():
    """Same input, same date, same award. If this ever fails, something
    non-deterministic has crept into the decision path and no recommendation
    from this system is defensible in a review."""
    a, b = _run(), _run()
    assert a["totals"] == b["totals"]
    assert a["recommendations"] == b["recommendations"]


def test_headline_saving_is_material(state):
    totals = state["totals"]
    assert totals["saving"] > 0
    assert 0.05 < totals["saving_pct"] < 0.50, (
        "a saving outside this band is far more likely to be a modelling bug "
        "than a real result")


def test_nothing_unmatched_reaches_an_award(state):
    """Adjacent specs are a proposal to a human. If one ever leaks into an
    award, the system has substituted a 300 gsm carton for a 350 gsm one."""
    awarded_specs = {r["spec_id"] for r in state["recommendations"] if r["award"]}
    unmatched = {u["description"] for u in state["unmatched"]}
    assert unmatched, "the fixture is meant to contain near-misses"
    for quote in state["quotes"]:
        if quote["raw_description"] in unmatched:
            assert quote["spec_id"] is None
            assert quote["spec_id"] not in awarded_specs


def test_every_risk_flag_has_an_owner(state):
    for flag in state["risk_flags"]:
        assert flag["owner"], f"{flag['code']} has no owner and is therefore noise"


def test_high_severity_flags_are_raised(state):
    codes = {f["code"] for f in state["risk_flags"] if f["severity"] == "HIGH"}
    assert "QUOTE_EXPIRED" in codes
    assert "SINGLE_SOURCE_EXPOSURE" in codes


def test_brief_renders_without_a_model(state):
    text = render(state)
    assert "SANGAM" in text
    assert "AWARD RECOMMENDATIONS" in text
    assert "RISK REGISTER" in text
    assert len(text.splitlines()) > 60


def test_negotiation_briefs_carry_only_settled_facts(state):
    """The drafting agent must not be handed anything it could turn into an
    invented commitment."""
    for brief in state["negotiation_briefs"]:
        assert brief["target_landed"] < brief["current_landed"]
        assert brief["volume"] > 0
        assert brief["leverage"], "an ask with no stated leverage is just a demand"

#!/usr/bin/env python3
"""Exercises the analyst's tools without a model.

    python -m sangam.demo_tools

The analyst is an LlmAgent, so its reasoning needs an API key. Its *tools* do
not. This script calls them in the order an analyst would for a few real
buyer questions, so the deterministic half of the agentic surface can be
inspected offline.

What it demonstrates is the division of labour the whole design rests on: the
model chooses which of these to call and in what order, and the answers come
back from `sangam.engine` unchanged. Swap in a model and the calls are the
same; only the choosing moves.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date

from google.adk.runners import InMemoryRunner
from google.genai import types

from .agents import tools
from .agents.workflow import build_workflow

APP = "sangam-demo"
W = 78


class _Ctx:
    """Stands in for ToolContext, which is all these tools need."""
    def __init__(self, state):
        self.state = state


async def _pipeline_state(run_date: str) -> dict:
    runner = InMemoryRunner(agent=build_workflow(live=False), app_name=APP)
    session = await runner.session_service.create_session(
        app_name=APP, user_id="demo", state={"run_date": run_date})
    async for _ in runner.run_async(
        user_id="demo", session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="run")])):
        pass
    final = await runner.session_service.get_session(
        app_name=APP, user_id="demo", session_id=session.id)
    return dict(final.state)


def _q(question: str) -> None:
    print()
    print("=" * W)
    print(f"  BUYER:  {question}")
    print("=" * W)


def _call(label: str, payload) -> None:
    print(f"\n  -> {label}")
    text = json.dumps(payload, indent=2, default=str)
    for line in text.splitlines():
        print(f"     {line}")


def main(argv: list[str] | None = None) -> int:
    run_date = (argv or sys.argv[1:] or ["2026-08-11"])[0]
    state = asyncio.run(_pipeline_state(run_date))
    ctx = _Ctx(state)

    print()
    print("  The analyst's tool calls, run without a model.")
    print("  With a key, the analyst chooses these; the answers are identical.")

    # ---- 1
    _q("Why did Om Print win the kraft carton?")
    _call('explain_award("SPEC-CTN-KFT-350-ML")',
          tools.explain_award("SPEC-CTN-KFT-350-ML", ctx))
    print("\n     The answer is in the blocked list, not the winner: Shakti's price")
    print("     lapsed on 31 July, so it could not be awarded volume at all.")

    # ---- 2
    _q("What would re-confirming Shakti's price actually be worth?")
    _call('simulate_award("SPEC-CTN-KFT-350-ML", include_expired=True)',
          tools.simulate_award("SPEC-CTN-KFT-350-ML", ctx, include_expired=True))
    print("\n     It costs money and buys a second source. That is the trade the")
    print("     Head of Sourcing is actually being asked to make.")

    # ---- 3
    _q("What happens if we lose Vidhata on glass entirely?")
    _call('simulate_award("SPEC-GLS-AMB-050-N18", exclude_vendor="Vidhata Glass Works")',
          tools.simulate_award("SPEC-GLS-AMB-050-N18", ctx,
                               exclude_vendor="Vidhata Glass Works"))

    # ---- 4
    _q("Can we put the whole PET jar volume on the cheap import?")
    _call('simulate_award("SPEC-JAR-PET-200-N70", max_import_share=1.0)',
          tools.simulate_award("SPEC-JAR-PET-200-N70", ctx, max_import_share=1.0))
    print("\n     Cheaper, and it puts a 47-day lead time on the whole spec with no")
    print("     domestic fallback. The policy cap exists for this reason.")

    # ---- 5
    _q("Nimbu Home wants 300gsm kraft cartons. Same spec, right?")
    _call('check_spec_match("mono carton kraft 300 gsm 4 colour matt lam")',
          tools.check_spec_match("mono carton kraft 300 gsm 4 colour matt lam", ctx))
    print("\n     No. This is the refusal that stops the system substituting a")
    print("     near-match and calling it an optimisation.")

    # ---- 6
    _q("Shakti's number looks odd. What did they actually say?")
    _call('show_source("Q003")', tools.show_source("Q003", ctx))
    print("\n     The extraction is defensible because the buyer can read the")
    print("     thread: 6.10 was the list rate at 11:09, 5.95 was agreed at 11:40.")

    # ---- 7
    _q("Is there anything in these documents about prices being revised?")
    _call('find_in_sources("board index")', tools.find_in_sources("board index", ctx))
    print("\n     Nowhere in the structured terms, and it decides whether the")
    print("     award holds. This is why the raw documents stay reachable.")

    # ---- 8
    _q("What is open on my desk right now?")
    _call('open_risks(severity="HIGH")', tools.open_risks(ctx, severity="HIGH"))

    print()
    print("=" * W)
    print("  Every figure above came from sangam.engine, the same code path the")
    print("  quarterly brief uses. The analyst picks the questions; it does not")
    print("  do the arithmetic.")
    print("=" * W)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

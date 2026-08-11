"""Command line entry point.

    python -m sangam                    run the graph, print the council brief
    python -m sangam --json out.json    also dump final state
    python -m sangam --trace            show every node event as it fires
    python -m sangam --live             force the live extraction path

The graph is driven through a real ADK Runner and session service rather than
by calling the node functions in order, because the orchestration is part of
what is being demonstrated. If you want the engine without the event loop,
import `sangam.engine` directly: it has no ADK dependency at all.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from datetime import date

from google.adk.runners import InMemoryRunner
from google.genai import types

from .agents.workflow import build_workflow
from .config import llm_available
from .reporting.brief import render

APP_NAME = "sangam"


async def run(run_date: str, live: bool | None, trace: bool) -> dict:
    workflow = build_workflow(live=live)
    runner = InMemoryRunner(agent=workflow, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id="sourcing_council",
        state={"run_date": run_date, "extraction_critique": "",
               "harmonisation_input": ""},
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text="Produce this quarter's cross-portfolio sourcing brief.")],
    )
    async for event in runner.run_async(
        user_id="sourcing_council", session_id=session.id, new_message=message
    ):
        if not trace:
            continue
        output = getattr(event, "output", None)
        if output:
            print(f"  [{event.author}] {output}", file=sys.stderr)
        elif event.content and event.content.parts:
            text = event.content.parts[0].text
            if text:
                print(f"  [{event.author}] {text.strip()[:150]}", file=sys.stderr)

    final = await runner.session_service.get_session(
        app_name=APP_NAME, user_id="sourcing_council", session_id=session.id
    )
    return dict(final.state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sangam", description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="run date, ISO. Governs quote expiry.")
    parser.add_argument("--json", type=pathlib.Path, help="dump final state to this path")
    parser.add_argument("--trace", action="store_true", help="print node events to stderr")
    parser.add_argument("--live", action="store_true", help="force the live extraction path")
    parser.add_argument("--cached", action="store_true", help="force the golden replay path")
    args = parser.parse_args(argv)

    live = True if args.live else (False if args.cached else None)
    if live is None and not llm_available():
        print("  no model configured, replaying the golden extraction set "
              "(set GOOGLE_API_KEY for the live path)\n", file=sys.stderr)

    if args.trace:
        print("  graph events:", file=sys.stderr)

    state = asyncio.run(run(args.date, live, args.trace))
    print(render(state))

    if args.json:
        args.json.write_text(json.dumps(state, indent=2, default=str))
        print(f"\n  final state written to {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

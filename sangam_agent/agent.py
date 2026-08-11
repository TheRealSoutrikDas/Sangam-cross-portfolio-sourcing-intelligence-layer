"""ADK discovery module.

    adk run sangam_agent
    adk web

`adk` looks for a package exposing `root_agent`. Everything real lives in
`src/sangam`; this is the four lines that make the graph loadable by the ADK
CLI and the dev UI, where you can step the nodes and inspect session state
between them.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sangam.agents.workflow import build_root_agent  # noqa: E402

root_agent = build_root_agent()

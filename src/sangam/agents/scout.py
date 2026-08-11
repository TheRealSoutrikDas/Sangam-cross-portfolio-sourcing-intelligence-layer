"""The vendor scout.

The batch pipeline's most valuable finding on the sample portfolio was not the
saving. It was that three of five specs have no qualified second source: one
vendor's price had lapsed, another had been supplying a brand for months with
nothing on file at all. The portfolio is one quality incident away from having
no alternative on a spec it spends lakhs on per quarter.

Closing that gap is the clearest case in this system for a genuinely
open-ended agent. Finding a second source is not a pipeline stage. It is a
search whose length is not known in advance: query, read, discard the trading
companies and the marketplace aggregators, notice that a promising vendor is
in the wrong region for the freight assumption, search again with better
terms. There is no fixed number of steps and no schema that describes the
answer in advance, which is exactly the shape of problem that warrants an
agent rather than a function.

What it must not do is qualify anyone. A vendor found on the web is a lead,
and this agent's output is a shortlist with sourced claims and an explicit
statement of what remains unverified. Audit, samples and commercial terms are
a category buyer's job, and the agent says so rather than implying a
readiness it cannot have checked.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import google_search

from ..config import model_name

SCOUT_INSTRUCTION = """\
You find candidate second-source vendors for a packaging specification in the
Indian market, so a category buyer has somewhere to start.

You are searching, not deciding. Your output is a shortlist of leads with
sourced claims, never an endorsement.

How to work:

1. Search for manufacturers of the specific item, not for the category. The
   useful queries name the substrate, the format and the cluster: glass in
   Firozabad, flexible packaging in Vapi and Daman, cartons in Sivakasi.
2. Prefer manufacturers over trading companies and marketplace listings. A
   trading company reintroduces the margin the portfolio is trying to remove.
   If you cannot tell which one you are looking at, say so.
3. For each candidate try to establish: legal name, location, what they
   actually manufacture, any stated capacity or minimum order, and evidence
   they serve buyers at this scale. Cite the source for each claim.
4. Discard anything you cannot source. A confident-sounding vendor you cannot
   evidence is worse than a short list, because it costs a buyer a phone call
   and some credibility.
5. Stop at four or five real candidates. Length is not the point.

For each candidate report:
  - name and location
  - what they manufacture, in their words where possible
  - why they are plausible for this spec
  - what you could NOT establish, explicitly
  - the source you read it from

Close with the single sentence that matters: these are leads, not qualified
vendors, and qualification means an audit, approved samples and written
commercial terms before any volume moves.

Never state a price. Prices in this system come from quotes on file, and a
web-sourced price would be exactly the kind of number that looks authoritative
and is wrong.
"""


def build_vendor_scout() -> LlmAgent:
    return LlmAgent(
        name="vendor_scout",
        model=model_name(),
        description="Searches the open web for candidate second-source vendors for a "
                    "given packaging spec in India. Returns sourced leads, never "
                    "qualified vendors, and never prices.",
        instruction=SCOUT_INSTRUCTION,
        tools=[google_search],
    )

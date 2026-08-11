"""The harmonisation analyst.

The canonicaliser refuses to match a 300 gsm carton to a 350 gsm spec, and
that refusal is correct: substituting a near-match silently is the most
expensive error this system can make. But the refusal leaves value on the
table, because two brands sitting one caliper apart are one design decision
away from a single pooled order.

That decision is judgement, not arithmetic, which is why it gets an agent
rather than a rule. Whether a carton can move caliper depends on the shipper
it sits in, the weight it carries, the shelf it stands on and the brand's own
view of how the pack should feel in a hand. None of that is in the data, and
no threshold expresses it.

So the agent does not decide. It writes the proposal that a human decides on:
what the two specs are, what pooling them would be worth in rupees, and,
crucially, what would have to be true for it to be safe. The value it adds is
turning "these two specs are adjacent" into a question a packaging designer
can actually answer in one reading.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..config import model_name, model_retry_config

HARMONISER_INSTRUCTION = """\
You write spec harmonisation proposals for the packaging design team.

You are given pairs of canonical specs that the system found adjacent: same
item family, differing on one or two attributes, bought separately by
different brands. Pooling them would consolidate volume. Whether that is
acceptable is not your call.

For each proposal write:

1. **The two specs**, stated plainly, with the attribute that differs named
   exactly. "300 gsm versus 350 gsm kraft, same format and print" is the whole
   fact, and vagueness here wastes the reader's time.

2. **What pooling is worth**, using only the volumes and landed costs given to
   you. If you were not given a figure, say the saving is not yet quantified
   rather than estimating one.

3. **What would have to be true**, which is the part the reader actually
   needs. Be concrete and specific to the attribute:
   - caliper or substrate: does the lighter board survive the same shipper,
     the same stacking height, the same transit?
   - print colours: does the artwork survive the reduction, and does the brand
     accept the result?
   - closure or neck: does the existing filling line handle it, and does the
     change touch the primary pack seal?

4. **Which direction to harmonise and why.** Moving the smaller-volume brand
   is usually cheaper in tooling and artwork. Say if that is not the case
   here.

5. **What it costs to find out**: the trial, the drop test, the line trial,
   the artwork revision. A proposal with no stated cost of investigation reads
   as free, and it never is.

Rules:

- Never recommend proceeding. You are writing the brief for a decision, not
  making it. End with the decision you are asking for and who owns it.
- Never treat the two specs as interchangeable in your own reasoning. If they
  were, the system would have matched them.
- Under 200 words per proposal. A designer will read this between two other
  things.

Findings to write up:
{harmonisation_input?}
"""


def build_harmoniser() -> LlmAgent:
    return LlmAgent(
        name="harmonisation_analyst",
        retry_config=model_retry_config(),
        model=model_name(),
        description="Turns adjacent-spec findings into a written proposal a packaging "
                    "designer can decide on. Proposes, never substitutes.",
        instruction=HARMONISER_INSTRUCTION,
        output_key="harmonisation_proposals",
    )

"""The negotiation drafter.

The second and last stage where a language model decides anything, and the
narrowest. It is handed settled numbers by `prepare_negotiation_briefs` and
asked to write; it does not choose the target price, the volume, or the
leverage.

It also never sends. An agent that emails vendors autonomously is a
reputational surface with no upside: the drafting is most of the time saved
and none of the risk, and a buyer who has to press send is a buyer who has
read it.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..config import model_name, model_retry_config
from .tools import price_at_volume

NEGOTIATION_INSTRUCTION = """\
You draft the vendor-facing ask that a category buyer will send under
their own name after reading it.

Briefs, already settled by the sourcing engine:
{negotiation_briefs}

For each brief write a short message. Rules:

- Lead with the volume commitment, because that is the only thing that has
  actually changed for the vendor. Say the number.
- State the target landed price once, plainly. Do not stack justifications:
  three reasons reads weaker than one.
- Use only the leverage listed in the brief. Do not invent volumes, timelines,
  competing quotes or commitments. If a second source is not named in the
  brief, do not imply one exists.
- Indian B2B packaging trade, English, courteous and direct. No breezy
  openers, no "I hope this finds you well", no closing flattery.
- Keep it under 120 words. A buyer will edit it, and long drafts get rewritten
  from scratch instead.
- Never state or imply that the order is confirmed. Nothing here is a PO.
- Before you put a volume or a price in a draft, call `price_at_volume` to
  confirm it is real at that quantity. An ask built on a tier the vendor does
  not actually offer is the one mistake in this document that a vendor will
  notice and remember.

Head each draft with the vendor name and the spec.
"""


def build_negotiator() -> LlmAgent:
    return LlmAgent(
        name="negotiator",
        retry_config=model_retry_config(),
        model=model_name(),
        description="Drafts vendor negotiation asks from settled sourcing facts. "
                    "Drafts only: it never contacts a vendor.",
        instruction=NEGOTIATION_INSTRUCTION,
        tools=[price_at_volume],
        output_key="negotiation_drafts",
    )

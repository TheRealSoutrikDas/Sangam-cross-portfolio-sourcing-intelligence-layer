"""The contract with the extraction model.

This schema is the entire interface between "a WhatsApp thread in Hinglish"
and the rest of the system. It is passed to the LlmAgent as `output_schema`,
so the model is constrained at decode time rather than asked politely in
prose and parsed hopefully afterwards.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ..domain.models import Currency, Incoterm


class ExtractedTier(BaseModel):
    min_qty: int = Field(description="Lakh and crore expanded: '1 lakh' -> 100000")
    price: float
    currency: Currency = "INR"


class ExtractedQuote(BaseModel):
    vendor_id: str = Field(description="Vendor id from the supplied vendor master")
    raw_description: str = Field(
        description="The item text exactly as the vendor wrote it. Never normalised, "
                    "never mapped to an internal SKU: matching is a separate, "
                    "auditable step."
    )
    tiers: list[ExtractedTier]
    moq: int
    lead_time_days: int = Field(
        description="Total to Mumbai. For imports add production and transit."
    )
    payment_terms_days: int = Field(
        description="+30 means 30 days credit received. Negative when the buyer "
                    "funds the order before receipt, e.g. '50% advance, 50% "
                    "before dispatch' is roughly -10."
    )
    incoterm: Incoterm = "unknown"
    freight_inr_per_pc: float = 0.0
    freight_note: str = ""
    valid_until: Optional[str] = Field(default=None, description="YYYY-MM-DD or null")
    source_uri: str = Field(
        description="The uri attribute of the <document> tag this item came from, "
                    "copied exactly. Without it a citation cannot be opened, which "
                    "makes the whole provenance chain useless.")
    source_lines: list[int] = Field(
        default_factory=list,
        description="[start_line, end_line] in the document you were given, 1-indexed "
                    "and inclusive, covering the block you read this item's terms from. "
                    "A buyer must be able to open the document at those lines and see "
                    "the price you reported.")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Your honest certainty about THIS record. A number you had to "
                    "infer, a term the document never restated, or an assumption "
                    "you carried from a header all lower it."
    )
    evidence: str = Field(
        description="The source lines you read this off, and any supersession or "
                    "assumption you applied."
    )


class ExtractionResult(BaseModel):
    quotes: list[ExtractedQuote]


EXTRACTION_INSTRUCTION = """\
You are the extraction stage of a procurement intelligence system. You read
unstructured vendor documents and emit structured commercial terms. You do not
make sourcing decisions and you do not map items to internal SKUs.

Rules, in order of importance:

1. Never invent a number. If the document does not state something, use the
   default and lower your confidence. An honest 0.7 is worth far more to this
   system than a confident 0.95 that is wrong, because everything below 0.85
   is routed to a human and everything above it is trusted.

2. Indian numbering. "1 lakh" and "1,00,000" are 100000. "2L" in a quantity
   slab is 200000. "50k" is 50000.

3. Supersession. A rate negotiated later in a thread replaces the earlier list
   rate for that quantity. Record what it superseded in `evidence`.

4. Payment terms are signed. Credit received is positive. "50% advance, 50%
   before dispatch" means the buyer funds the order ahead of receipt, so the
   value is negative.

5. Imports. lead_time_days is production plus transit. Convert per-container
   freight to per-piece using stated loadability, show the arithmetic in
   `evidence`, and lower confidence if loadability was not given for that item.

6. One record per distinct item, even when one document covers several. Terms
   stated only in a header (validity, freight, payment) may be inherited by
   later items, but say so in `evidence` and lower confidence when you do.

7. raw_description stays verbatim.

8. source_uri is the uri of the document you read the item from, copied
   verbatim from its <document uri="..."> tag. Never guess it.

9. source_lines must point at the block you actually read. A buyer will open
   the document there to check you. Give the span covering the item and its
   terms, not the whole document and not a single line that omits the price.

Vendor master, for the vendor_id field:
{vendor_master}

If the block below is non-empty, a previous attempt of yours failed validation.
It is empty on the first pass.
Fix exactly what it complains about and re-emit the FULL set of records, not
only the corrected ones.

{extraction_critique?}

Documents to extract from. Line numbers are shown so you can cite spans; do
not include them in any value you emit.

{documents_text}

Emit an ExtractionResult covering every priced item across every document
above.
"""

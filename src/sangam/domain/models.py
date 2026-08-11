"""Domain models.

Pydantic rather than dataclasses for one reason: these objects have to survive
a round trip through ADK session state, which serialises to JSON. Every model
here is the same type whether it came from an LLM, from a cache, or from a
session that was persisted three days ago.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Currency = Literal["INR", "USD"]
Incoterm = Literal["EXW", "FOR", "FOB", "CIF", "unknown"]
Severity = Literal["HIGH", "MED", "INFO"]


class PriceTier(BaseModel):
    min_qty: int
    price: float
    currency: Currency = "INR"


class CanonicalSpec(BaseModel):
    """One physical object, one identifier, portfolio-wide."""
    spec_id: str
    family: str
    label: str
    attrs: dict = Field(
        description="Discriminating attributes. Anything that differs here is "
                    "a DIFFERENT spec and is never silently collapsed."
    )
    unit: str = "pc"


class Vendor(BaseModel):
    vendor_id: str
    name: str
    location: str
    origin: Literal["domestic", "import"]
    inland_freight_inr_per_pc: float = 0.0
    duty_pct: float = 0.0
    qualified: bool = True


class Quote(BaseModel):
    """A vendor's commercial terms for one item, after extraction."""
    quote_id: str
    vendor_id: str
    raw_description: str
    tiers: list[PriceTier]
    moq: int
    lead_time_days: int
    payment_terms_days: int = Field(
        description="+30 = 30 days credit received. -10 = buyer funds the "
                    "order ~10 days before receipt."
    )
    incoterm: Incoterm = "unknown"
    freight_inr_per_pc: float = 0.0
    valid_until: Optional[str] = None
    source_uri: str
    source_lines: list[int] = Field(
        default_factory=list,
        description="[start, end] line span in the source document, 1-indexed and "
                    "inclusive. This is what lets a buyer read the sentence a number "
                    "came from instead of trusting the extraction.")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""

    # filled by the canonicaliser, not the extractor
    spec_id: Optional[str] = None
    match_evidence: str = ""
    adjacency_note: str = ""

    @field_validator("tiers")
    @classmethod
    def _tiers_sane(cls, v: list[PriceTier]) -> list[PriceTier]:
        if not v:
            raise ValueError("a quote with no price tiers is not a quote")
        qtys = [t.min_qty for t in v]
        if qtys != sorted(qtys):
            raise ValueError("tiers must ascend by quantity")
        prices = [t.price for t in v]
        if prices != sorted(prices, reverse=True):
            # A price that rises with volume is a misread of the document,
            # not a commercial term anyone has ever offered. Fail loudly here
            # rather than quietly awarding on it downstream.
            raise ValueError(f"price rises with volume in {qtys} - suspect misread")
        return v

    def is_expired(self, on: date) -> bool:
        return bool(self.valid_until) and date.fromisoformat(self.valid_until) < on


class DemandLine(BaseModel):
    """What one brand actually buys today."""
    brand: str
    raw_description: str
    vendor_name_current: str
    monthly_qty: int
    current_unit_price: float
    payment_terms_days: int
    lead_time_days: int
    spec_id: Optional[str] = None
    adjacency_note: str = ""


class AwardLine(BaseModel):
    quote_id: str
    source: str = Field(default="", description="document and line span the terms came from")
    vendor_id: str
    vendor_name: str
    qty: int
    landed_unit_cost: float
    lead_time_days: int
    payment_terms_days: int


class Recommendation(BaseModel):
    spec_id: str
    spec_label: str
    brands: list[str]
    monthly_qty: int
    horizon_qty: int
    baseline_cost: float
    award: list[AwardLine] = []
    award_cost: Optional[float] = None
    mode: str = ""
    saving: float = 0.0
    resilience_premium: Optional[float] = Field(
        default=None,
        description="What the portfolio pays to hold a second qualified source "
                    "instead of taking the cheapest single award.",
    )
    blocked: list[dict] = []
    counterfactual: Optional[str] = None

    @property
    def baseline_unit(self) -> float:
        return self.baseline_cost / self.horizon_qty if self.horizon_qty else 0.0

    @property
    def award_unit(self) -> float:
        return (self.award_cost or 0.0) / self.horizon_qty if self.horizon_qty else 0.0


class RiskFlag(BaseModel):
    severity: Severity
    code: str
    message: str
    owner: str = Field(description="A named human role. A flag with no owner is noise.")
    evidence: str = ""


class NegotiationBrief(BaseModel):
    """Input handed to the negotiation agent. Deliberately narrow: the agent
    drafts an ask from settled facts, it does not decide the ask."""
    vendor_name: str
    spec_label: str
    current_landed: float
    target_landed: float
    volume: int
    leverage: list[str]

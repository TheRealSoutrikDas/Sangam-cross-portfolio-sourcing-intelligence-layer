"""Ingestion and the golden extraction set.

The golden set is not a shortcut around the model. It is the regression
fixture: the records a live extraction run produced, hand-verified once and
checked in, so that every subsequent change to the canonicaliser, the cost
model or the optimiser is tested against fixed input.

You want this in production too. Without it, an extraction change that quietly
degrades accuracy shows up as a bad PO three weeks later instead of as a red
build in four seconds.
"""
from __future__ import annotations

import csv
import json
import pathlib
from typing import Iterable

from ..config import GOLDEN_PATH, RAW_DIR
from ..domain.models import DemandLine, Quote

DOC_SUFFIXES = {".txt", ".md", ".json"}
ERP_FILE = "erp_current_po_lines.csv"


def list_documents(raw_dir: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Vendor artefacts, excluding the ERP export, which is demand not supply."""
    raw_dir = raw_dir or RAW_DIR
    return sorted(
        p for p in raw_dir.iterdir()
        if p.is_file() and p.suffix in DOC_SUFFIXES and p.name != ERP_FILE
    )


def read_documents(raw_dir: pathlib.Path | None = None) -> list[dict]:
    return [{"uri": p.name, "text": p.read_text()} for p in list_documents(raw_dir)]


def load_golden_quotes(path: pathlib.Path | None = None) -> list[Quote]:
    """Replay a verified extraction run. Validation is not skipped: the same
    pydantic validators the live path uses run here, so a corrupted fixture
    fails as loudly as a hallucinating model."""
    path = path or GOLDEN_PATH
    records = json.loads(path.read_text())
    return [_to_quote(r, i) for i, r in enumerate(records)]


def quotes_from_extraction(records: Iterable[dict]) -> list[Quote]:
    """Live model output to domain objects, through the same validators."""
    return [_to_quote(r, i) for i, r in enumerate(records)]


def _to_quote(record: dict, index: int) -> Quote:
    payload = dict(record)
    payload.setdefault("quote_id", f"Q{index + 1:03d}")
    payload.setdefault("source_uri", payload.pop("_source_uri", "unknown"))
    if "_vendor_id" in payload:
        payload["vendor_id"] = payload.pop("_vendor_id")
    payload.pop("freight_note", None)
    return Quote.model_validate(payload)


def load_demand(raw_dir: pathlib.Path | None = None) -> list[DemandLine]:
    raw_dir = raw_dir or RAW_DIR
    out: list[DemandLine] = []
    with open(raw_dir / ERP_FILE) as fh:
        for row in csv.DictReader(fh):
            out.append(DemandLine(
                brand=row["brand"],
                raw_description=row["item_description_free_text"],
                vendor_name_current=row["vendor"],
                monthly_qty=int(row["monthly_qty"]),
                current_unit_price=float(row["unit_price_inr"]),
                payment_terms_days=int(row["payment_terms_days"]),
                lead_time_days=int(row["lead_time_days"]),
            ))
    return out

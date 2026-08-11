"""Parsing the model's output back into records.

`output_schema` constrains the model at decode time, but the value that lands
in session state is still a string. This is the one place that string is
turned back into data, so there is one place to harden when a model returns
something unexpected.
"""
from __future__ import annotations

import json


def parse_extraction_output(raw: str | dict | list) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("quotes", [])
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    return payload.get("quotes", [])

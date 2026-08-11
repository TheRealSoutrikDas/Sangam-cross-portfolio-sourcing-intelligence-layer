"""Policy loading and paths.

Policy is data, not code. It is loaded once, validated, and passed explicitly
into the engine functions that use it, so that a test can run the optimiser
against a different policy without patching a module global.
"""
from __future__ import annotations

import os
import pathlib

from pydantic import BaseModel, Field

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _find_data_dir() -> pathlib.Path:
    """Locate `data/`.

    Explicit env var wins. Otherwise walk up from this module, which covers
    both an editable install from the repo and a checkout run without
    installing. A packaged deployment sets SANGAM_DATA_DIR.
    """
    if env := os.environ.get("SANGAM_DATA_DIR"):
        return pathlib.Path(env).expanduser().resolve()
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data"
        if (candidate / "raw").is_dir():
            return candidate
    return ROOT / "data"


DATA = _find_data_dir()
RAW_DIR = DATA / "raw"
GOLDEN_PATH = DATA / "extracted" / "quotes.golden.json"
POLICY_PATH = DATA / "policy.yaml"


class SourcingPolicy(BaseModel):
    planning_horizon_months: int = 3
    cost_of_capital: float = 0.12
    fx_usd_inr: float = 87.50

    dual_source_threshold_inr: float = 500_000
    dual_source_primary_share: float = 0.70
    dual_source_secondary_share: float = 0.30
    max_import_share: float = 0.60

    quote_stale_after_days: int = 45
    quote_expiring_warning_days: int = 14
    hitl_confidence_floor: float = Field(default=0.85, ge=0.0, le=1.0)
    lead_time_alert_days: int = 35
    max_vendor_portfolio_share: float = 0.35


def load_policy(path: pathlib.Path | None = None) -> SourcingPolicy:
    path = path or POLICY_PATH
    if not path.exists():
        return SourcingPolicy()
    try:
        import yaml
        raw = yaml.safe_load(path.read_text()) or {}
    except ImportError:
        raw = _parse_flat_yaml(path.read_text())
    return SourcingPolicy(**raw)


def _parse_flat_yaml(text: str) -> dict:
    """PyYAML is a one-line dependency, but the policy file is flat scalars and
    the demo should run on a bare interpreter. Falls back to this."""
    out: dict = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = (p.strip() for p in line.split(":", 1))
        if not v:
            continue
        try:
            out[k] = int(v) if v.isdigit() else float(v)
        except ValueError:
            out[k] = v
    return out


def model_name() -> str:
    """The model used for the two LLM stages."""
    return os.environ.get("SANGAM_MODEL", "gemini-3.5-flash")


def model_retry_config():
    """Retry policy for every model-backed node.

    Free-tier Gemini allows a handful of requests per minute, and this pipeline
    makes several: the extraction repair loop alone can burn three. Without a
    backoff the run dies partway through on a 429, which loses the work that
    already succeeded rather than waiting a minute for the quota window to roll.

    Retries are scoped to rate limiting and transient server errors. A 400 or a
    schema violation is a bug and must fail immediately: retrying it just makes
    the same mistake more slowly.
    """
    from google.adk.workflow import RetryConfig

    return RetryConfig(
        max_attempts=int(os.environ.get("SANGAM_RETRY_ATTEMPTS", "5")),
        initial_delay=float(os.environ.get("SANGAM_RETRY_DELAY", "20")),
        max_delay=90.0,
        backoff_factor=2.0,
        jitter=0.3,
        # ADK matches these against type(exception).__name__, so a dotted
        # path never matches. Bare class names only.
        exceptions=["_ResourceExhaustedError", "ClientError", "ServerError"],
    )


def llm_available() -> bool:
    """Whether a live model is configured. Governs which extraction strategy
    the workflow binds. The demo must run either way."""
    if os.environ.get("SANGAM_FORCE_CACHED"):
        return False
    return bool(
        os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")
    )

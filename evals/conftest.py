import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sangam.config import load_policy
from sangam.extraction.ingest import load_demand, load_golden_quotes

RUN_DATE = datetime.date(2026, 8, 11)


@pytest.fixture(scope="session")
def policy():
    return load_policy()


@pytest.fixture(scope="session")
def quotes():
    return load_golden_quotes()


@pytest.fixture(scope="session")
def demand():
    return load_demand()


@pytest.fixture(scope="session")
def run_date():
    return RUN_DATE

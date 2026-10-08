import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import config  # noqa: E402
from ingest import pipeline  # noqa: E402

SAMPLE_PDF = config.REPORTS_DIR / "Bharat-Forge-IR-2026-conv-single-page.pdf"


@pytest.fixture(scope="session")
def index():
    if not SAMPLE_PDF.exists():
        pytest.skip(f"Sample report not found: {SAMPLE_PDF}")
    return pipeline.load_index(SAMPLE_PDF)


TEN_K_PDF = config.REPORTS_DIR / "Chubb-10-K-2025.pdf"


@pytest.fixture(scope="session")
def ten_k_index():
    """A US 10-K: small bold statement titles, "F-6" page numbers."""
    if not TEN_K_PDF.exists():
        pytest.skip(f"10-K sample not found: {TEN_K_PDF}")
    return pipeline.load_index(TEN_K_PDF)


@pytest.fixture(scope="session")
def package(index):
    """The sample report's package (built by the `index` fixture)."""
    return pipeline.open_report(SAMPLE_PDF)


@pytest.fixture(scope="session")
def ten_k_package(ten_k_index):
    return pipeline.open_report(TEN_K_PDF)

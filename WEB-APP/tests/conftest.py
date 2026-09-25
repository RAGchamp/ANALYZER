import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import config  # noqa: E402
import notes_index  # noqa: E402

SAMPLE_PDF = config.REPORTS_DIR / "Bharat-Forge-IR-2026-conv-single-page.pdf"


@pytest.fixture(scope="session")
def index():
    if not SAMPLE_PDF.exists():
        pytest.skip(f"Sample report not found: {SAMPLE_PDF}")
    return notes_index.load_or_build(SAMPLE_PDF)

"""The split stays a split (INFO/SPLIT-FUNCTIONALITY-PLAN.md §9, §11 "Import boundary"):
the analyzer never reads a PDF or uses the ingester, the ingester never uses
the analyzer, and the package library uses neither."""

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]

FORBIDDEN = {
    "analyzer": {"pymupdf", "fitz", "ingest"},
    "ingest": {"analyzer"},
    "rptpkg": {"pymupdf", "fitz", "ingest", "analyzer", "flask"},
}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0]


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_no_forbidden_imports(package):
    offenders = [f"{path.relative_to(APP_DIR)} imports {name}"
                 for path in sorted((APP_DIR / package).glob("*.py"))
                 for name in _imports(path) if name in FORBIDDEN[package]]
    assert offenders == []


def test_shared_modules_stay_neutral():
    """webcommon and claude_client serve both sides, so they use neither."""
    for name in ("webcommon.py", "claude_client.py", "config.py"):
        used = set(_imports(APP_DIR / name))
        assert not used & {"ingest", "analyzer", "pymupdf", "fitz"}, name

"""Where the analyzer gets its reports from: report packages only.

The analyzer never reads a PDF and never imports the ingester
(INFO/SPLIT-FUNCTIONALITY-PLAN.md §9). app.py registers the loader that
ingests a report when it has no up-to-date package yet ("Ingest now");
without one, only reports that already have a package can be analyzed.
"""

import rptpkg
from rptpkg import store
from webcommon import UserError, resolve_report

_loader = None


def set_loader(loader):
    """loader(pdf_path) -> rptpkg.Package, ingesting the report if needed."""
    global _loader
    _loader = loader


def open_report(report_name):
    """The report package for a PDF listed in REPORTS_DIR."""
    path = resolve_report(report_name)
    if _loader is not None:
        return _loader(path)
    found = store.find_package(path)
    if found is None:
        raise UserError(f"{report_name} hasn't been ingested yet. Select it in step 1 first.")
    try:
        return rptpkg.open_package(found)
    except rptpkg.PackageError as exc:
        raise UserError(str(exc))

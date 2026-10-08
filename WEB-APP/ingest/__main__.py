"""Ingest one annual report from the command line (development and tests).

    python -m ingest "..\\Annual-reports\\Chubb-10-K-2025.pdf"
    python -m ingest report.pdf --format legacy     # force a format
    python -m ingest report.pdf --force             # rebuild even if up to date

One report at a time (INFO/SPLIT-FUNCTIONALITY-PLAN.md, Q7). Scanned reports
must be transcribed first, from the app (it costs Claude usage).

Exit codes: 0 ingested, 1 unreadable PDF, 2 needs transcription,
3 new model document (it matches no model in MODEL-DOCS; see the gap report).
"""

import argparse
import logging
import sys
from pathlib import Path

from ingest import pipeline, quality, report_format
from ingest.pdf_utils import PdfError


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m ingest", description="Ingest one annual report PDF.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--format", choices=["auto", *report_format.FORMATS],
                        help="force a report format (default: keep the package's, or detect)")
    parser.add_argument("--force", action="store_true", help="rebuild the package even if it is up to date")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not args.pdf.is_file():
        parser.error(f"not a file: {args.pdf}")
    try:
        if args.force:
            path = pipeline.build_package(args.pdf, args.format)
        else:
            path = pipeline.ensure_package(args.pdf, args.format)
    except pipeline.NeedsTranscription as exc:
        print(f"{args.pdf.name}: {exc}", file=sys.stderr)
        return 2
    except pipeline.NewModelDocument as exc:
        print(f"{args.pdf.name}: NEW MODEL DOCUMENT. {exc}", file=sys.stderr)
        if exc.gap_report:
            print(f"Gap report: {exc.gap_report}", file=sys.stderr)
        return 3
    except PdfError as exc:
        print(f"{args.pdf.name}: {exc}", file=sys.stderr)
        return 1
    package = pipeline.rptpkg.open_package(path)
    print(quality.report_text(package))
    print(f"Package: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

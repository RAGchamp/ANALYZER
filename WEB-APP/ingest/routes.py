"""The Ingest screen (INFO/SPLIT-FUNCTIONALITY-PLAN.md §6.3, Phase 5) and the
ingester's other routes:

  GET  /ingest                  the screen: every report with its package status
  GET  /api/ingest/reports      that list
  POST /api/ingest/run          ingest one report (a job; one report at a time)
  GET  /api/packages/export     download a report's package
  POST /api/packages/import     add a package exported on another PC
  GET  /page-image              any PDF page as an image
  GET  /gap-report              a new model document's gap report; /api/models, /api/models/propose,
                                /api/models/describe (INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md)
  scanned reports: /api/ocr/start, /api/ocr/page, /ocr-review, /ocr-image
"""

import io
import logging
import re
import shutil
import tempfile
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

import config
from ingest import notes_index, ocr_quality, ocr_transcribe, pipeline
from ingest.models import gap_report, registry
from ingest.pdf_utils import PdfError, open_pdf
from rptpkg import store
from webcommon import (UserError, current_job_id, job_cancelled, list_reports, logo_svg, parse_page_spec,
                       render_markdown, resolve_report, start_job, update_job)

PAGE_IMAGE_DPI = 110

log = logging.getLogger("app")
bp = Blueprint("ingest", __name__)


def needs_transcription(path, exc):
    """Step 1's answer for a scanned report that hasn't been transcribed yet."""
    pages = list(range(1, exc.page_count + 1))
    minutes, usd, todo = ocr_transcribe.estimate(path, pages)
    return {
        "report": path.name, "page_count": exc.page_count, "needs_transcription": True,
        "status": exc.status, "tesseract": bool(ocr_quality.tesseract_path()),
        "estimate": {"minutes": minutes, "usd": usd, "pages": todo},
        "review_url": f"/ocr-review?report={path.name}",
    }


def new_model_document(path, exc):
    """Step 1's answer for a report that matches no model document: blocked (Q1)."""
    return {"report": path.name, "new_model": exc.to_json(),
            "gap_report_url": f"/gap-report?report={path.name}"}


def ocr_job(report_name, page_spec, force, dpi=None):
    """Transcribe a scanned report (or some of its pages) with Claude."""
    path = resolve_report(report_name)
    pages = parse_page_spec(page_spec) if page_spec else None
    job_id = current_job_id()   # the callbacks run on the transcription thread pool

    def progress(done, total, text):
        update_job(job_id, progress=text, done=done, total=total)

    try:
        status = ocr_transcribe.run(path, pages, progress=progress, cancel=lambda: job_cancelled(job_id),
                                    force=force, dpi=dpi)
    except ocr_transcribe.Cancelled:
        raise UserError("Transcription cancelled. Pages already transcribed are kept: "
                        "click Transcribe to continue where it stopped.")
    log.info("Transcribed %s: %s", report_name, status)
    return status


@bp.route("/api/ocr/start", methods=["POST"])
def api_ocr_start():
    data = request.get_json(silent=True) or {}
    report = data.get("report", "")
    resolve_report(report)
    pages = (data.get("pages") or "").strip()
    if pages:
        parse_page_spec(pages)   # validate now, not in the job
    return jsonify({"job_id": start_job("ocr", ocr_job, report, pages, False)})


@bp.route("/api/ocr/page", methods=["POST"])
def api_ocr_page():
    """Re-transcribe one page (the review page's button)."""
    data = request.get_json(silent=True) or {}
    report = data.get("report", "")
    resolve_report(report)
    page = int(data.get("page") or 0)
    dpi = int(data["dpi"]) if data.get("dpi") else None
    return jsonify({"job_id": start_job("ocr", ocr_job, report, str(page), True, dpi)})


@bp.route("/ocr-image")
def ocr_image():
    """The page image Claude read (rendered now if the page isn't transcribed yet)."""
    path = resolve_report(request.args.get("report", ""))
    page = int(request.args.get("page", "1"))
    image = ocr_transcribe.page_file(path, page, "png")
    if not image.exists():
        image = ocr_transcribe.render_page(path, page, config.OCR_DPI)
    return send_file(image, mimetype="image/png")


def _highlight(html, figures):
    for figure in figures:
        escaped = figure.replace("&", "&amp;").replace("<", "&lt;")
        html = html.replace(escaped, f'<mark title="Not confirmed by the cross-check">{escaped}</mark>')
    return html.replace("[?]", '<mark class="unreadable" title="Unreadable in the scan">[?]</mark>')


@bp.route("/ocr-review")
def ocr_review():
    """A scanned page next to its transcription, with the automatic checks."""
    path = resolve_report(request.args.get("report", ""))
    manifest = ocr_transcribe.load_manifest(path)
    page_count = manifest.get("page_count")
    if not page_count:
        with ocr_transcribe._pdf_lock:
            doc = notes_index.open_pdf(path)
            page_count = doc.page_count
            doc.close()
    page = max(1, min(int(request.args.get("page", "1")), page_count))
    record = manifest["pages"].get(str(page), {})
    header = ocr_transcribe.page_header(path, page)
    markdown_text = ocr_transcribe.page_markdown(path, page)
    pages = []
    for pno in range(1, page_count + 1):
        rec = manifest["pages"].get(str(pno), {})
        flags = (rec.get("unreadable", 0) + len(rec.get("figures_to_check", []))
                 + len(rec.get("untied", [])))
        pages.append({"no": pno, "status": rec.get("status", "not transcribed"),
                      "type": rec.get("page_type", ""), "flags": flags})
    return render_template(
        "ocr_review.html", report=path.name, page=page, page_count=page_count, pages=pages,
        record=record, header=header, logo_svg=logo_svg(),
        transcription_html=_highlight(render_markdown(markdown_text), header.get("figures_to_check", []))
        if markdown_text else "",
        tesseract=bool(ocr_quality.tesseract_path()),
    )


# ---------------------------------------------------------------- the Ingest screen

@bp.route("/ingest")
def ingest_screen():
    return render_template("ingest.html", reports_dir=str(config.REPORTS_DIR), logo_svg=logo_svg())


def _report_row(path):
    row = {"name": path.name, "size_mb": round(path.stat().st_size / 1_048_576, 1)}
    row.update(pipeline.package_status(path))
    manifest = ocr_transcribe.load_manifest(path)
    row["scanned"] = row.get("format") == "transcribed" or bool(manifest.get("requested"))
    if row["scanned"]:
        row["review_url"] = f"/ocr-review?report={path.name}"
    return row


@bp.route("/api/ingest/reports")
def api_ingest_reports():
    return jsonify({"reports": [_report_row(p) for p in list_reports()],
                    "reports_dir": str(config.REPORTS_DIR)})


def ingest_job(report_name, format_choice, force):
    """Ingest one report: its package, or what a scanned report needs first."""
    path = resolve_report(report_name)
    try:
        if force:
            pipeline.build_package(path, format_choice)
        else:
            pipeline.ensure_package(path, format_choice)
    except pipeline.NeedsTranscription as exc:
        return needs_transcription(path, exc)
    except pipeline.NewModelDocument as exc:
        return {**_report_row(path), **new_model_document(path, exc), "status": "new model document"}
    except PdfError as exc:
        raise UserError(str(exc))
    return _report_row(path)


@bp.route("/api/ingest/run", methods=["POST"])
def api_ingest_run():
    data = request.get_json(silent=True) or {}
    report = data.get("report", "")
    resolve_report(report)
    format_choice = data.get("format") or None
    if format_choice not in (None, "auto", *pipeline.report_format.FORMATS):
        raise UserError(f"Unknown report format: {format_choice}")
    return jsonify({"job_id": start_job("ingest", ingest_job, report, format_choice, bool(data.get("force")))})


@bp.route("/api/packages/export")
def api_package_export():
    """Download a report's package, e.g. to use a scanned report's paid
    transcription on another PC."""
    path = resolve_report(request.args.get("report", ""))
    found = store.find_package(path)
    if found is None:
        raise UserError(f"{path.name} hasn't been ingested yet.")
    return send_file(found, as_attachment=True, download_name=f"{path.stem}{store.SUFFIX}",
                     mimetype="application/octet-stream")


@bp.route("/api/packages/import", methods=["POST"])
def api_package_import():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        raise UserError("Choose a .rptpkg.db file to import.")
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "upload.rptpkg.db"
        upload.save(target)
        try:
            result = pipeline.import_package(target)
        except ValueError as exc:
            raise UserError(str(exc))
    matching = [p.name for p in list_reports() if store.pdf_sha256(p) == result["pdf_sha256"]]
    result["matching_reports"] = matching
    return jsonify(result)


@bp.route("/page-image")
def page_image():
    """Any page of a report as a PNG (the scanned-page view uses /ocr-image)."""
    path = resolve_report(request.args.get("report", ""))
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        raise UserError("Page must be a number.")
    doc = open_pdf(path)
    try:
        if not 1 <= page <= doc.page_count:
            raise UserError(f"PDF page {page} is out of range (1–{doc.page_count})")
        png = doc[page - 1].get_pixmap(dpi=PAGE_IMAGE_DPI).tobytes("png")
    finally:
        doc.close()
    return send_file(io.BytesIO(png), mimetype="image/png")


# ---------------------------------------------------------------- model documents
# INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §5-7

def _gap_report_for(report_name):
    path = resolve_report(report_name)
    report = gap_report.path_for(path)
    if not report.exists():
        raise UserError(f"No gap report for {path.name}: it matched a model document.")
    return path, report


@bp.route("/gap-report")
def gap_report_page():
    """The gap report of a new model document, for the developer."""
    path, report = _gap_report_for(request.args.get("report", ""))
    return render_template("gap_report.html", report=path.name, logo_svg=logo_svg(),
                           body=render_markdown(report.read_text(encoding="utf-8")))


@bp.route("/api/models")
def api_models():
    reg = registry.load()
    return jsonify({
        "version": reg.version, "model_docs_dir": str(config.MODEL_DOCS_DIR),
        "models": [{"id": m.id, "short": m.short, "profile": m.profile, "pending": m.pending,
                    "description": m.description,
                    "pdf_present": (Path(config.MODEL_DOCS_DIR) / m.file).exists()} for m in reg.models],
    })


@bp.route("/api/models/propose", methods=["POST"])
def api_models_propose():
    """Q3: copy a new model document and its gap report to MODEL-DOCS/_candidates/.
    It becomes a model when a developer has written its profile and registered it."""
    data = request.get_json(silent=True) or {}
    path, report = _gap_report_for(data.get("report", ""))
    target = Path(config.MODEL_DOCS_DIR) / "_candidates" / path.stem
    try:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target / path.name)
        shutil.copy2(report, target / "GAP-REPORT.md")
    except OSError as exc:
        raise UserError(f"Could not copy to {target}: {exc}")
    log.info("Proposed %s as a model document: %s", path.name, target)
    return jsonify({"folder": str(target)})


def describe_job(report_name):
    path, report = _gap_report_for(report_name)
    pages = [int(n) for n in re.findall(r"\*\*PDF p\.(\d+)\*\*", report.read_text(encoding="utf-8"))]
    if not pages:
        raise UserError("The gap report names no evidence pages to show Claude.")
    gap_report.describe(path, pages[:4])
    return {"gap_report_url": f"/gap-report?report={path.name}"}


@bp.route("/api/models/describe", methods=["POST"])
def api_models_describe():
    """Q9: add Claude's description of a few evidence pages to the gap report (costs usage)."""
    data = request.get_json(silent=True) or {}
    _gap_report_for(data.get("report", ""))
    return jsonify({"job_id": start_job("describe", describe_job, data.get("report", ""))})

"""Annual Report Foot Notes Analyzer - Flask app.

One app, two parts (INFO/SPLIT-FUNCTIONALITY-PLAN.md):
  ingest/     the Report Ingester: PDF -> report package (format detection,
              notes and statements, clipped text, OCR for scanned reports)
  analyzer/   the Analyzer: report package -> questions to Claude, answers,
              history and HTML reports. It never reads a PDF.
  rptpkg/     the report package (SQLite) both of them use.

Flow (see INFO/ANN-RPT-NOTES-ANALYZER-PLAN.md):
  1. pick a report          GET  /api/reports, POST /api/index (ingests if needed)
  2. ask a question         POST /api/identify  -> job (scope + note picking)
  3. confirm/edit the notes (in the browser - always pauses here)
  4-6. extract, analyze     POST /api/analyze   -> job (claude -p)
  follow-ups on a thread    POST /api/followup  -> job
  job status                GET  /api/jobs/<id>
  history                   GET  /api/history, /api/history/<sno>
  model testing             GET  /model-testing (INFO/MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md)

This module wires the two parts together: it is the only place that uses both.
"""

import logging

from flask import Flask, jsonify, render_template, request

import config
from analyzer import routes as analyzer_routes
from analyzer import source
from ingest import pipeline, report_format
from ingest import model_test_routes
from ingest import routes as ingest_routes
from ingest.pdf_utils import PdfError
from webcommon import JOBS, JOBS_LOCK, UserError, list_reports, render_markdown, resolve_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_DIR / "app.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("app")
# The browser polls /api/jobs every 1.5s; keep those requests out of the log.
logging.getLogger("werkzeug").addFilter(
    lambda record: "/api/jobs/" not in record.getMessage())

app = Flask(__name__)
app.register_blueprint(analyzer_routes.bp)
app.register_blueprint(ingest_routes.bp)
app.register_blueprint(model_test_routes.bp)


def open_report_package(pdf_path, format_choice=None):
    """The analyzer's source of packages: ingest the report if it has no
    up-to-date package yet."""
    try:
        return pipeline.open_report(pdf_path, format_choice)
    except pipeline.NewModelDocument as exc:
        # blocked completely until its model is added (INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md, Q1)
        raise UserError(f"New model document: {exc}")
    except PdfError as exc:
        raise UserError(str(exc))


source.set_loader(open_report_package)


@app.errorhandler(UserError)
def handle_user_error(exc):
    return jsonify({"error": str(exc)}), 400


@app.route("/")
def index():
    return render_template("index.html", reports_dir=str(config.REPORTS_DIR))


@app.route("/api/reports")
def api_reports():
    reports = []
    for path in list_reports():
        reports.append({
            "name": path.name,
            "size_mb": round(path.stat().st_size / 1_048_576, 1),
            "indexed": pipeline.is_ingested(path),
        })
    return jsonify({"reports": reports, "reports_dir": str(config.REPORTS_DIR)})


@app.route("/api/index", methods=["POST"])
def api_index():
    """Step 1: load a report - ingest it if needed, then summarise its package."""
    data = request.get_json(silent=True) or {}
    format_choice = data.get("format") or None
    if format_choice not in (None, "auto", *report_format.FORMATS):
        raise UserError(f"Unknown report format: {format_choice}")
    path = resolve_report(data.get("report", ""))
    try:
        package = pipeline.open_report(path, format_choice)
    except pipeline.NeedsTranscription as exc:
        return jsonify(ingest_routes.needs_transcription(path, exc))
    except pipeline.NewModelDocument as exc:
        return jsonify(ingest_routes.new_model_document(path, exc))
    except PdfError as exc:
        raise UserError(str(exc))
    return jsonify(analyzer_routes.report_summary(package))


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown job."}), 404
        job["cancel"] = True
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>")
def api_job(job_id):
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id) or {})
    if not job:
        return jsonify({"error": "Unknown job (the server may have restarted)."}), 404
    partial = job.pop("partial", None)
    if partial:
        # Rendered (and HTML-escaped) exactly like the final answer.
        job["partial_html"] = render_markdown(partial)
    return jsonify(job)


if __name__ == "__main__":
    # Local tool only: bind to 127.0.0.1 (same as basic-chatbot).
    # use_reloader=False keeps in-memory jobs alive while analyses run.
    app.run(host=config.HOST, port=config.PORT, debug=True, use_reloader=False)

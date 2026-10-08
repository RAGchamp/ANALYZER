"""The Model testing screen (INFO/MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md §5):

  GET  /model-testing                       the screen (also /Model-testing.html)
  GET  /api/model-testing/docs              the model documents in MODEL-DOCS
  POST /api/model-testing/extract           test one page (a job: a first test may ingest)
  GET  /api/model-testing/page-image        the page as a PNG
  POST /api/model-testing/feedback          report a wrong extraction
  GET  /api/model-testing/feedback[/<file>] the audit trail, one report

The formatted document (INFO/MODEL-FORMATTED-REPORTS-PLAN.md §5):

  GET  /model-testing/formatted?doc=[&download=1]   the whole extraction as one HTML page
  POST /api/model-testing/formatted/prepare          ingest first (a job), then the page reloads
  GET  /api/model-testing/formatted/stored-text      a unit's text exactly as stored
"""

import io
import logging
import time
from urllib.parse import urlencode

from flask import Blueprint, Response, jsonify, render_template, request, send_file

import config
import rptpkg
from ingest import feedback, formatted_report, model_testing, pipeline
from ingest.models import registry
from ingest.pdf_utils import PdfError, clean_text, open_pdf
from webcommon import UserError, logo_svg, start_job

log = logging.getLogger("app")
bp = Blueprint("model_testing", __name__)


def _doc_and_page(data):
    path = model_testing.resolve_model_doc(data.get("doc", ""))
    page = model_testing.validate_page(data.get("page"), model_testing.page_count(path))
    return path, page


def _test_result(path, page):
    result = model_testing.extract_page(path, page)
    return {**result, "doc": path.name, "extracted_text": model_testing.format_extracted(result),
            "image_url": "/api/model-testing/page-image?" + urlencode({"doc": path.name, "page": page})}


@bp.route("/model-testing")
@bp.route("/Model-testing.html")
def model_testing_screen():
    return render_template("model_testing.html", model_docs_dir=str(config.MODEL_DOCS_DIR),
                           max_chars=config.FEEDBACK_MAX_CHARS)


@bp.route("/api/model-testing/docs")
def api_docs():
    folder = config.MODEL_DOCS_DIR
    if not folder.is_dir():
        return jsonify({"docs": [], "model_docs_dir": str(folder),
                        "error": f"The model documents folder was not found: {folder}"})
    return jsonify({"docs": [model_testing.doc_info(p) for p in model_testing.list_model_docs()],
                    "model_docs_dir": str(folder)})


@bp.route("/api/model-testing/extract", methods=["POST"])
def api_extract():
    path, page = _doc_and_page(request.get_json(silent=True) or {})   # checked now, not in the job
    log.info("Model test: %s page %d", path.name, page)
    return jsonify({"job_id": start_job("model-test", _test_result, path, page)})


@bp.route("/api/model-testing/page-image")
def api_page_image():
    """The page as a PNG; clip=x0,y0,x1,y1 (PDF points) gives one part of it, size=thumb a thumbnail."""
    path, page = _doc_and_page(request.args)
    clip = model_testing.parse_clip(request.args.get("clip"))
    dpi = 60 if request.args.get("size") == "thumb" else None
    return send_file(io.BytesIO(model_testing.page_png(path, page, dpi=dpi, clip=clip)), mimetype="image/png")


@bp.route("/api/model-testing/feedback", methods=["POST"])
def api_feedback_submit():
    data = request.get_json(silent=True) or {}
    path, page = _doc_and_page(data)
    comment = feedback.check_comment(data.get("comment"))
    # extracted again here: the file holds what the server produced, not what the browser sent
    result = model_testing.extract_page(path, page)
    saved = feedback.submit(path.name, result, comment, model_testing.page_png(path, page))
    log.info("Model feedback: %s page %d -> %s", path.name, page, saved["feedback_file"])
    return jsonify(saved)


@bp.route("/api/model-testing/feedback")
def api_feedback_list():
    return jsonify({"entries": feedback.entries(), "folder": str(config.MODEL_FEEDBACK_DIR)})


@bp.route("/api/model-testing/feedback/<name>")
def api_feedback_file(name):
    try:
        return jsonify(feedback.read_feedback(name))
    except UserError as exc:
        return jsonify({"error": str(exc)}), 404


# ---------------------------------------------------------------- the formatted document

def _render(document):
    return render_template("formatted_report.html", doc=document, logo_svg=logo_svg(),
                           is_figure=formatted_report._is_figure,
                           download_query=urlencode({"doc": document["doc_name"], "download": 1}))


def _cached(key, build):
    html = formatted_report.CACHE.get(key)
    if html is None:
        started = time.monotonic()
        html = _render(build())
        formatted_report.CACHE.put(key, html)
        log.info("Formatted document %s: %.1fs, %d KB", key[1], time.monotonic() - started, len(html) // 1024)
    return html


def _raw_html(path, notice):
    stat = path.stat()

    def build():
        doc = open_pdf(path)
        try:
            texts = [(n + 1, clean_text(doc[n].get_text("text")).strip()) for n in range(doc.page_count)]
        finally:
            doc.close()
        return formatted_report.build_raw_document(path.name, texts, notice)

    key = ("raw", path.name, stat.st_size, stat.st_mtime, registry.load().version,
           formatted_report.FORMATTER_VERSION, notice)
    return _cached(key, build)


def _formatted_html(path, prepared):
    """The page's HTML, or None when the package must be built first (the preparing page)."""
    model = model_testing.registry_model(path)
    if model is None:
        return _raw_html(path, "This PDF is not in the model registry, so the app has no extraction rules for it "
                               "(see MODEL-DOCS\\README.md). This is the raw PDF text, not the app's extraction.")
    if model.pending:
        return _raw_html(path, f"No extraction rules for {model.id} yet (pending). "
                               "This is the raw PDF text, not the app's extraction.")
    status = pipeline.package_status(path)["status"]
    if status == "new model document":
        return _raw_html(path, "The app's rules did not accept this model document. "
                               "This is the raw PDF text, not the app's extraction.")
    if status != "up to date" and not prepared:
        return None
    try:
        package = pipeline.open_report(path)
    except pipeline.NeedsTranscription:
        return _render(formatted_report.build_notice_document(
            path.name, "This scanned report has not been transcribed yet, so there is no extracted text. "
                       "Transcribe it on the Ingest screen (it costs Claude usage, so model testing never "
                       "starts it by itself)."))
    except pipeline.NewModelDocument as exc:
        return _raw_html(path, f"The app's rules did not accept this model document: {exc} "
                               "This is the raw PDF text, not the app's extraction.")
    except PdfError as exc:
        raise UserError(str(exc))
    meta = package.meta

    def build():
        document = formatted_report.build_document(package, path.name)
        if meta.get("model") and meta["model"] != model.id:
            document["notices"].append(
                f"This PDF matched {meta.get('model')}, not its own model {model.id} - worth reporting.")
        return document

    key = ("package", path.name, package.path.name, meta.get("built_at"), meta.get("ingested_at"),
           formatted_report.FORMATTER_VERSION)
    return _cached(key, build)


@bp.route("/model-testing/formatted")
def formatted_document():
    try:
        path = model_testing.resolve_model_doc(request.args.get("doc", ""))
    except UserError as exc:
        return Response(str(exc), status=404, mimetype="text/plain")
    html = _formatted_html(path, prepared=request.args.get("prepared") == "1")
    if html is None:
        return render_template("formatted_preparing.html", doc_name=path.name, logo_svg=logo_svg())
    response = Response(html, mimetype="text/html")
    if request.args.get("download") == "1":
        response.headers["Content-Disposition"] = f'attachment; filename="{path.stem}-formatted.html"'
    return response


def _prepare(path):
    """Ingest the model document; a scan without its transcription or a rejected
    model is not an error here - the formatted page explains it."""
    try:
        pipeline.open_report(path)
    except (pipeline.NeedsTranscription, pipeline.NewModelDocument):
        pass
    except PdfError as exc:
        raise UserError(str(exc))
    return {"ready": True}


@bp.route("/api/model-testing/formatted/prepare", methods=["POST"])
def api_formatted_prepare():
    path = model_testing.resolve_model_doc((request.get_json(silent=True) or {}).get("doc", ""))
    log.info("Formatted document: preparing %s", path.name)
    return jsonify({"job_id": start_job("formatted-prepare", _prepare, path)})


@bp.route("/api/model-testing/formatted/stored-text")
def api_formatted_stored_text():
    """A unit's text exactly as stored (section + unit), or the stored page text of pages=a-b."""
    path = model_testing.resolve_model_doc(request.args.get("doc", ""))
    package = pipeline.open_report(path)
    if request.args.get("pages"):
        try:
            first, last = (int(v) for v in request.args["pages"].split("-"))
        except ValueError:
            return jsonify({"error": "pages must be a-b"}), 400
        rows = package.query("SELECT pdf_page, printed, text FROM pages WHERE pdf_page BETWEEN ? AND ? "
                             "ORDER BY pdf_page", (first, min(last, first + 200)))
        parts = []
        for r in rows:
            label = f"PDF page {r['pdf_page']}" + (f" (printed page {r['printed']})" if r["printed"] else "")
            parts.append(f"--- {label} ---\n{r['text'] or ''}")
        return jsonify({"text": "\n\n".join(parts)})
    try:
        return jsonify({"text": package.unit_text(request.args.get("section", ""),
                                                   request.args.get("unit", ""))["text"]})
    except rptpkg.PackageError as exc:      # no such unit
        return jsonify({"error": str(exc)}), 404

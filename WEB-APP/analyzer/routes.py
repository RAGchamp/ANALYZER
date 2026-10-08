"""The Analyze screen's routes (see app.py for the flow).

  2. ask a question         POST /api/identify  -> job (scope + note picking)
  3. confirm/edit the notes (in the browser - always pauses here)
  4-6. extract, analyze     POST /api/analyze   -> job (claude -p)
  follow-ups on a thread    POST /api/followup  -> job
  statements page           GET  /statements
  history                   GET  /api/history, /api/history/<sno>, /api/threads/<id>
"""

import re

from flask import Blueprint, abort, jsonify, render_template, request, send_from_directory

import config
from analyzer import history, passages, statement_info, statements_page
from analyzer.context import is_legacy, is_transcribed
from analyzer.jobs import MODES, analyze_job, followup_job, identify_job, load_thread, thread_path
from analyzer.note_selector import all_note_choices
from analyzer.source import open_report
from rptpkg.model import model_line
from webcommon import UserError, logo_svg, parse_page_spec, render_markdown, resolve_report, start_job

bp = Blueprint("analyzer", __name__)


def report_summary(package):
    """What step 1 shows once a report is loaded, and the notes to pick from."""
    idx = package.index
    sections = {
        key: {"label": s["label"], "first_page": s["first_page"],
              "last_page": s["last_page"], "count": len(s["notes"]),
              "statements_summary": statement_info.summary(s.get("statements", [])),
              "statements": [
                  {"type": st["type"], "label": st["label"], "title": st["title"],
                   "pages": statement_info.pages_text(st),
                   "rotated": any(p["rotation"] for p in st["pages"]),
                   "text": st["text"]}
                  for st in s.get("statements", [])
              ],
              "missing_statements": idx.get("statements_missing", []) if is_legacy(idx) else []}
        for key, s in idx["sections"].items()
    }
    return {
        "report": idx["pdf_name"],
        "page_count": idx["page_count"],
        "summary": package.meta.get("summary", ""),
        "format": idx.get("format", "modern"),
        "format_label": package.meta.get("format_label") or idx.get("format"),
        "format_source": idx.get("format_source", "detected"),
        # the model document it matched (INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §5.1)
        "model": model_line(package.meta),
        "ocr": idx.get("ocr"),
        "review_url": f"/ocr-review?report={idx['pdf_name']}" if is_transcribed(idx) else None,
        "sections": sections,
        "notes": all_note_choices(idx, "both"),
        # what the ingester found and checked (figures, links, checks, warnings)
        "quality": [line for line in (package.meta.get("quality") or {}).get("lines_text", [])
                    if line.startswith(("Figures:", "Links:", "Checks:", "Warning:"))],
    }


@bp.route("/statements")
def view_statements():
    """"View the financial statements": the loaded statements as tables,
    consolidated first, then standalone. Opens in a new tab."""
    package = open_report(request.args.get("report", ""))
    idx = package.index
    return render_template(
        "statements_view.html",
        company=idx.get("company") or idx["pdf_name"],
        pdf_name=idx["pdf_name"],
        sections=statements_page.report_view(package),
        logo_svg=logo_svg(),
    )


@bp.route("/api/identify", methods=["POST"])
def api_identify():
    data = request.get_json(silent=True) or {}
    report = data.get("report", "")
    question = (data.get("question") or "").strip()
    if not question:
        raise UserError("Please type a question.")
    mode = data.get("mode") or "notes"
    if mode not in MODES:
        raise UserError(f"Unknown analysis mode: {mode}")
    resolve_report(report)
    return jsonify({"job_id": start_job("identify", identify_job, report, question, mode)})


@bp.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(silent=True) or {}
    report = data.get("report", "")
    question = (data.get("question") or "").strip()
    note_ids = [str(i) for i in data.get("notes") or []]
    passage_ids = [str(i) for i in data.get("passages") or []]
    page_spec = (data.get("pages") or "").strip()
    mode = data.get("mode") or "notes"
    if mode not in MODES:
        raise UserError(f"Unknown analysis mode: {mode}")
    if not question:
        raise UserError("Please type a question.")
    resolve_report(report)
    if page_spec:
        try:
            parse_page_spec(page_spec)  # validate now, fail fast
        except ValueError as exc:
            raise UserError(str(exc))
    return jsonify({"job_id": start_job(
        "analyze", analyze_job, report, question, note_ids, page_spec, passage_ids, mode)})


@bp.route("/api/passages/search")
def api_passages_search():
    """Step 3's "Add passage…": the report passages that best match the words typed."""
    words = (request.args.get("q") or "").strip()
    if not words:
        return jsonify({"passages": []})
    package = open_report(request.args.get("report", ""))
    return jsonify({"passages": passages.candidates(package, words, limit=10)})


@bp.route("/api/passages/<passage_id>")
def api_passage(passage_id):
    """Step 3's "Show": one passage's text, as it would be sent to Claude."""
    package = open_report(request.args.get("report", ""))
    found = package.passage(passage_id)
    if found is None:
        return jsonify({"error": f"Passage {passage_id} not found in this report."}), 404
    return jsonify({"id": found["id"], "path": found["path"], "start_page": found["start_page"],
                    "end_page": found["end_page"], "chars": found["chars"], "text": found["text"]})


@bp.route("/api/followup", methods=["POST"])
def api_followup():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    thread_id = data.get("thread_id", "")
    if not question:
        raise UserError("Please type a follow-up question.")
    thread_path(thread_id)
    return jsonify({"job_id": start_job("followup", followup_job, thread_id, question)})


@bp.route("/api/threads/<thread_id>")
def api_thread(thread_id):
    """Reopen a past analysis thread (from History) to continue follow-ups."""
    thread = load_thread(thread_id)
    for turn in thread["turns"]:
        turn["answer_html"] = render_markdown(turn["answer"])
    return jsonify(thread)


@bp.route("/analysis-history/<path:filename>")
def analysis_history_file(filename):
    """Open a saved HTML report from Analysis-history/."""
    if not filename.lower().endswith(".html"):
        abort(404)
    return send_from_directory(config.ANALYSIS_HISTORY_DIR, filename)


@bp.route("/api/history")
def api_history():
    return jsonify({"entries": history.list_entries()})


@bp.route("/api/history/<int:sno>")
def api_history_entry(sno):
    entry = history.read_entry(sno)
    if not entry:
        return jsonify({"error": f"History entry {sno} not found."}), 404
    entry["answer_html"] = render_markdown(entry["answer"])
    thread_id = entry["meta"].get("Thread", "")
    entry["thread_available"] = bool(
        re.fullmatch(r"[0-9a-f]{32}", thread_id)
        and (config.THREADS_DIR / f"{thread_id}.json").exists())
    return jsonify(entry)

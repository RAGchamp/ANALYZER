"""Annual Report Foot Notes Analyzer - Flask app.

Flow (see INFO/ANN-RPT-NOTES-ANALYZER-PLAN.md):
  1. pick a report          GET  /api/reports, POST /api/index
  2. ask a question         POST /api/identify  -> job (scope + note picking)
  3. confirm/edit the notes (in the browser - always pauses here)
  4-6. extract, analyze     POST /api/analyze   -> job (claude -p)
  follow-ups on a thread    POST /api/followup  -> job
  job status                GET  /api/jobs/<id>
  history                   GET  /api/history, /api/history/<sno>

The Claude calls follow basic-chatbot's pattern (a fresh `claude -p` per
request, prompt saved to claude-prompt-input.txt, reply to
Claude-prompt-output.txt, every Q&A numbered in Prompt-History/). Because
an analysis can take minutes, Claude calls run in a background thread and
the browser polls the job.
"""

import json
import logging
import re
import threading
import uuid
from datetime import datetime

import markdown
from flask import Flask, abort, jsonify, render_template, request, send_from_directory

import config
import history
import notes_index
import report_html
import statements
import statements_view
from claude_client import ClaudeError, fill_prompt, run_claude, system_prompt
from extractor import extract_note, extract_selection, parse_page_spec
from note_selector import NOTE_REF_RE, all_note_choices, select_notes
from pdf_utils import PdfError

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

JOBS = {}
JOBS_LOCK = threading.Lock()
THREAD_LOCK = threading.Lock()


class UserError(Exception):
    """A problem to show the user as-is (bad input, too much content...)."""


# ---------------------------------------------------------------- helpers

LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _separate_lists(text):
    """Python-Markdown only starts a list after a blank line, but Claude
    often writes "**Label:**" with "- item" lines right below it, which
    would otherwise render as one run-on paragraph. Add the blank line."""
    out, in_fence, prev = [], False, ""
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif (not in_fence and LIST_ITEM_RE.match(line) and prev.strip()
              and not LIST_ITEM_RE.match(prev) and not prev.startswith((" ", "\t", "|"))):
            out.append("")
        out.append(line)
        prev = line
    return "\n".join(out)


def render_markdown(text):
    # Escape raw HTML so nothing in Claude's reply is injected into the page.
    safe = _separate_lists(text).replace("&", "&amp;").replace("<", "&lt;")
    return markdown.markdown(safe, extensions=["tables", "fenced_code", "sane_lists"])


def list_reports():
    if not config.REPORTS_DIR.is_dir():
        return []
    return sorted(p for p in config.REPORTS_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() == ".pdf")


def resolve_report(name):
    """Only allow PDFs that are actually listed in REPORTS_DIR."""
    for path in list_reports():
        if path.name == name:
            return path
    raise UserError(f"Report not found in {config.REPORTS_DIR}: {name}")


def load_index(report_name):
    try:
        return notes_index.load_or_build(resolve_report(report_name))
    except PdfError as exc:
        raise UserError(str(exc))


def notes_label(notes):
    return ", ".join(
        f"{n['section_label']} Note {n['no']} – {n['title']} (PDF p.{n['start_page']}"
        + (f"–{n['end_page']}" if n["end_page"] != n["start_page"] else "") + ")"
        for n in notes
    )


def scope_label(notes, pages):
    if pages:
        return "Manually selected pages"
    labels = sorted({n["section_label"] for n in notes})
    return " and ".join(labels)


def pages_label(extracts):
    return ", ".join(str(p["pdf_page"]) for e in extracts for p in e["pages"])


def note_pages_label(notes, page_spec):
    """PDF page ranges of the notes, e.g. "405–407, 451–452"."""
    if page_spec:
        return page_spec
    return ", ".join(
        f"{n['start_page']}" if n["start_page"] == n["end_page"]
        else f"{n['start_page']}–{n['end_page']}" for n in notes)


def statement_sections(index, notes):
    """The notes sections whose primary statements accompany a question:
    the sections of the selected notes, or consolidated (the default) for
    manually entered pages."""
    sections = sorted({n["section"] for n in notes})
    if not sections:
        sections = ["consolidated"] if "consolidated" in index["sections"] else list(index["sections"])[:1]
    return sections


def save_report(index, *, question, answer, prompt_no, sent_at, notes, page_spec,
                statements_list="", original_question=None):
    """Write the styled HTML report to Analysis-history/. A failure here is
    logged but never fails the analysis itself."""
    try:
        return report_html.save_report(
            pdf_name=index["pdf_name"],
            company=index.get("company") or index["pdf_name"],
            question=question,
            answer_html=render_markdown(answer),
            prompt_no=prompt_no,
            sent_at=sent_at,
            scope=scope_label(notes, page_spec),
            notes_text=notes_label(notes) if notes else f"PDF pages {page_spec}",
            pages_text=note_pages_label(notes, page_spec),
            statements_text=statements_list,
            original_question=original_question,
        )
    except Exception:
        log.exception("Could not save the HTML report to %s", config.ANALYSIS_HISTORY_DIR)
        return None


def check_size(extract_text):
    if len(extract_text) > config.MAX_CONTEXT_CHARS:
        raise UserError(
            f"The selected content is {len(extract_text):,} characters, above the "
            f"{config.MAX_CONTEXT_CHARS:,} limit. Remove a note or narrow the pages."
        )


def call_claude(prompt, system, timeout):
    """basic-chatbot pattern: prompt to the input file, reply to the output file."""
    config.INPUT_FILE.write_text(prompt, encoding="utf-8")
    try:
        reply = run_claude(prompt, system, timeout)
    except ClaudeError as exc:
        config.OUTPUT_FILE.write_text(f"ERROR: {exc}", encoding="utf-8")
        raise
    config.OUTPUT_FILE.write_text(reply, encoding="utf-8")
    return reply


def thread_path(thread_id):
    if not re.fullmatch(r"[0-9a-f]{32}", thread_id or ""):
        raise UserError("Invalid thread id.")
    return config.THREADS_DIR / f"{thread_id}.json"


def load_thread(thread_id):
    path = thread_path(thread_id)
    if not path.exists():
        raise UserError("This analysis thread no longer exists. Start a new analysis.")
    return json.loads(path.read_text(encoding="utf-8"))


def save_thread(thread):
    thread_path(thread["id"]).write_text(json.dumps(thread, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- jobs

def start_job(kind, func, *args):
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "kind": kind, "status": "running",
                        "started": datetime.now().isoformat(timespec="seconds")}

    def runner():
        try:
            result = func(*args)
            update = {"status": "done", "result": result}
        except (UserError, ClaudeError, ValueError) as exc:
            update = {"status": "error", "error": str(exc)}
        except Exception as exc:  # keep the job from hanging on bugs
            log.exception("Job %s (%s) failed", job_id, kind)
            update = {"status": "error", "error": f"Unexpected error: {exc}"}
        with JOBS_LOCK:
            JOBS[job_id].update(update)

    threading.Thread(target=runner, daemon=True).start()
    return job_id


def identify_job(report_name, question):
    """STEP 3: scope + note selection, plus an extract-size estimate."""
    started = datetime.now()
    index = load_index(report_name)
    selection = select_notes(index, question)
    chars = 0
    if selection["notes"]:
        _, combined = extract_selection(index, notes=selection["notes"])
        chars = len(combined)
    log.info("Identify (%s, %.1fs): scope=%s method=%s notes=%s chars=%d",
             report_name, (datetime.now() - started).total_seconds(),
             selection["scope"], selection["method"],
             [n["id"] for n in selection["notes"]], chars)
    selection["chars"] = chars
    selection["max_chars"] = config.MAX_CONTEXT_CHARS
    return selection


def analyze_job(report_name, question, note_ids, page_spec):
    """STEPS 4-6: extract, send to Claude, save history, start a thread."""
    started = datetime.now()
    index = load_index(report_name)
    choices = {c["id"]: c for c in all_note_choices(index, "both")}

    pages = parse_page_spec(page_spec) if page_spec else None
    notes = []
    if not pages:
        notes = [choices[i] for i in note_ids if i in choices]
        if not notes:
            raise UserError("Select at least one note (or enter PDF pages) to analyze.")
    extracts, extract_text = extract_selection(index, notes=notes, page_numbers=pages)
    # The primary statements (loaded with the report) always go with the notes.
    sections = statement_sections(index, notes)
    statements_text = statements.sections_text(index, sections)
    statements_list = statements.label(index, sections)
    check_size(statements_text + extract_text)

    notes_text = notes_label(notes) if notes else f"PDF pages {page_spec}"
    prompt = fill_prompt(
        "analysis_template.txt",
        REPORT=report_name,
        SCOPE=scope_label(notes, pages),
        NOTES=notes_text,
        STATEMENTS_LIST=statements_list or "none found in this report",
        STATEMENTS=statements_text or "(No primary financial statements were found in this report.)",
        QUESTION=question,
        EXTRACT=extract_text,
    )
    sent_at = datetime.now()
    answer = call_claude(prompt, system_prompt("analysis_system.txt"),
                         config.ANALYSIS_TIMEOUT)
    saved_html = save_report(index, question=question, answer=answer, prompt_no=1,
                             sent_at=sent_at, notes=notes, page_spec=page_spec,
                             statements_list=statements_list)

    thread = {
        "id": uuid.uuid4().hex,
        "report": report_name,
        "notes": notes,
        "page_spec": page_spec,
        "extract": extract_text,
        "statement_sections": sections,
        "statements": statements_text,
        "statements_list": statements_list,
        "turns": [],
    }
    sno = history.next_sno()
    history.save_history(sno, question, answer, sent_at, {
        "Kind": "analysis",
        "Report": report_name,
        "Scope": scope_label(notes, pages),
        "Notes": notes_text,
        "PDF pages": pages_label(extracts),
        "Financial statements": statements_list,
        "Thread": thread["id"],
        "Saved HTML": saved_html or "",
    }, prompt)
    thread["turns"].append({"sno": sno, "question": question, "answer": answer,
                            "timestamp": sent_at.strftime(history.TIMESTAMP_FORMAT),
                            "saved_html": saved_html})
    save_thread(thread)
    log.info("Analyze (%s, %.1fs): notes=%s extract=%d chars statements=%d chars "
             "answer=%d chars sno=%d",
             report_name, (datetime.now() - started).total_seconds(),
             [n["id"] for n in notes] or page_spec, len(extract_text),
             len(statements_text), len(answer), sno)
    return {
        "thread_id": thread["id"],
        "sno": sno,
        "question": question,
        "answer": answer,
        "answer_html": render_markdown(answer),
        "notes": notes,
        "pages": pages_label(extracts),
        "extract": extract_text,
        "statements": statements_text,
        "statements_list": statements_list,
        "timestamp": sent_at.strftime(history.TIMESTAMP_FORMAT),
        "saved_html": saved_html,
    }


def followup_job(thread_id, question):
    """Follow-up on the same notes; "Note 41" in the question adds that note."""
    with THREAD_LOCK:
        thread = load_thread(thread_id)
    index = load_index(thread["report"])
    added = []
    if not thread.get("page_spec"):
        sections = sorted({n["section"] for n in thread["notes"]}) or ["consolidated"]
        have = {n["id"] for n in thread["notes"]}
        choices = {c["id"]: c for c in all_note_choices(index, "both")}
        for match in NOTE_REF_RE.finditer(question):
            for section in sections:
                note_id = ("C" if section == "consolidated" else "S") + match.group(1)
                if note_id in choices and note_id not in have:
                    extra = extract_note(index, section, int(match.group(1)))
                    thread["notes"].append(choices[note_id])
                    thread["extract"] += "\n\n" + extra["text"]
                    have.add(note_id)
                    added.append(choices[note_id])
    # Threads started before statements were added don't carry them yet.
    if "statements" not in thread:
        sections = statement_sections(index, thread["notes"])
        thread["statement_sections"] = sections
        thread["statements"] = statements.sections_text(index, sections)
        thread["statements_list"] = statements.label(index, sections)
    check_size(thread["statements"] + thread["extract"])

    recent = thread["turns"][-config.MAX_FOLLOWUP_TURNS:]
    conversation = "\n\n".join(
        f"User asked: {t['question']}\nYou answered: {t['answer']}" for t in recent)
    notes_text = (notes_label(thread["notes"]) if thread["notes"]
                  else f"PDF pages {thread['page_spec']}")
    prompt = fill_prompt(
        "followup_template.txt",
        REPORT=thread["report"],
        SCOPE=scope_label(thread["notes"], thread.get("page_spec")),
        NOTES=notes_text,
        STATEMENTS_LIST=thread["statements_list"] or "none found in this report",
        STATEMENTS=thread["statements"] or "(No primary financial statements were found in this report.)",
        EXTRACT=thread["extract"],
        CONVERSATION=conversation,
        QUESTION=question,
    )
    sent_at = datetime.now()
    answer = call_claude(prompt, system_prompt("analysis_system.txt"),
                         config.ANALYSIS_TIMEOUT)
    # Prompt 1 is the thread's first question, 2 its first follow-up, ...
    prompt_no = len(thread["turns"]) + 1
    saved_html = save_report(
        index, question=question, answer=answer, prompt_no=prompt_no, sent_at=sent_at,
        notes=thread["notes"], page_spec=thread.get("page_spec"),
        statements_list=thread["statements_list"],
        original_question=thread["turns"][0]["question"] if thread["turns"] else None)

    sno = history.next_sno()
    history.save_history(sno, question, answer, sent_at, {
        "Kind": "follow-up",
        "Report": thread["report"],
        "Scope": scope_label(thread["notes"], thread.get("page_spec")),
        "Notes": notes_text,
        "Financial statements": thread["statements_list"],
        "Thread": thread["id"],
        "Saved HTML": saved_html or "",
    }, prompt)
    with THREAD_LOCK:
        thread["turns"].append({"sno": sno, "question": question, "answer": answer,
                                "timestamp": sent_at.strftime(history.TIMESTAMP_FORMAT),
                                "saved_html": saved_html})
        save_thread(thread)
    return {
        "thread_id": thread["id"],
        "sno": sno,
        "question": question,
        "answer": answer,
        "answer_html": render_markdown(answer),
        "added_notes": added,
        "notes": thread["notes"],
        "extract": thread["extract"],
        "statements": thread["statements"],
        "statements_list": thread["statements_list"],
        "timestamp": sent_at.strftime(history.TIMESTAMP_FORMAT),
        "saved_html": saved_html,
    }


# ---------------------------------------------------------------- routes

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
            "indexed": notes_index.cache_path(path).exists(),
        })
    return jsonify({"reports": reports, "reports_dir": str(config.REPORTS_DIR)})


@app.route("/api/index", methods=["POST"])
def api_index():
    data = request.get_json(silent=True) or {}
    idx = load_index(data.get("report", ""))
    sections = {
        key: {"label": s["label"], "first_page": s["first_page"],
              "last_page": s["last_page"], "count": len(s["notes"]),
              "statements_summary": statements.summary(s.get("statements", [])),
              "statements": [
                  {"type": st["type"], "label": st["label"], "title": st["title"],
                   "pages": statements.pages_text(st),
                   "rotated": any(p["rotation"] for p in st["pages"]),
                   "text": st["text"]}
                  for st in s.get("statements", [])
              ]}
        for key, s in idx["sections"].items()
    }
    return jsonify({
        "report": idx["pdf_name"],
        "page_count": idx["page_count"],
        "summary": notes_index.summary(idx),
        "sections": sections,
        "notes": all_note_choices(idx, "both"),
    })


@app.route("/statements")
def view_statements():
    """"View the financial statements": the loaded statements as tables,
    consolidated first, then standalone. Opens in a new tab."""
    idx = load_index(request.args.get("report", ""))
    logo_svg = config.LOGO_FILE.read_text(encoding="utf-8") if config.LOGO_FILE.exists() else ""
    return render_template(
        "statements_view.html",
        company=idx.get("company") or idx["pdf_name"],
        pdf_name=idx["pdf_name"],
        sections=statements_view.report_view(idx),
        logo_svg=logo_svg,
    )


@app.route("/api/identify", methods=["POST"])
def api_identify():
    data = request.get_json(silent=True) or {}
    report = data.get("report", "")
    question = (data.get("question") or "").strip()
    if not question:
        raise UserError("Please type a question.")
    resolve_report(report)
    return jsonify({"job_id": start_job("identify", identify_job, report, question)})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(silent=True) or {}
    report = data.get("report", "")
    question = (data.get("question") or "").strip()
    note_ids = [str(i) for i in data.get("notes") or []]
    page_spec = (data.get("pages") or "").strip()
    if not question:
        raise UserError("Please type a question.")
    resolve_report(report)
    if page_spec:
        try:
            parse_page_spec(page_spec)  # validate now, fail fast
        except ValueError as exc:
            raise UserError(str(exc))
    return jsonify({"job_id": start_job(
        "analyze", analyze_job, report, question, note_ids, page_spec)})


@app.route("/api/followup", methods=["POST"])
def api_followup():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    thread_id = data.get("thread_id", "")
    if not question:
        raise UserError("Please type a follow-up question.")
    thread_path(thread_id)
    return jsonify({"job_id": start_job("followup", followup_job, thread_id, question)})


@app.route("/api/jobs/<job_id>")
def api_job(job_id):
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id) or {})
    if not job:
        return jsonify({"error": "Unknown job (the server may have restarted)."}), 404
    return jsonify(job)


@app.route("/api/threads/<thread_id>")
def api_thread(thread_id):
    """Reopen a past analysis thread (from History) to continue follow-ups."""
    thread = load_thread(thread_id)
    for turn in thread["turns"]:
        turn["answer_html"] = render_markdown(turn["answer"])
    return jsonify(thread)


@app.route("/analysis-history/<path:filename>")
def analysis_history_file(filename):
    """Open a saved HTML report from Analysis-history/."""
    if not filename.lower().endswith(".html"):
        abort(404)
    return send_from_directory(config.ANALYSIS_HISTORY_DIR, filename)


@app.route("/api/history")
def api_history():
    return jsonify({"entries": history.list_entries()})


@app.route("/api/history/<int:sno>")
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


if __name__ == "__main__":
    # Local tool only: bind to 127.0.0.1 (same as basic-chatbot).
    # use_reloader=False keeps in-memory jobs alive while analyses run.
    app.run(host=config.HOST, port=config.PORT, debug=True, use_reloader=False)

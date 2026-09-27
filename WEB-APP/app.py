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
from pathlib import Path

import markdown
from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory

import config
import history
import note_selector
import notes_index
import ocr_quality
import ocr_transcribe
import report_format
import report_html
import statements
import statements_view
from claude_client import ClaudeError, fill_prompt, run_claude, system_prompt
from extractor import extract_note, extract_selection, parse_page_spec
from note_selector import all_note_choices, select_notes
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
# Which job the current worker thread is running, so call_claude can publish
# the answer-so-far to it (see _job_progress).
CURRENT_JOB = threading.local()
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


def load_index(report_name, format_choice=None):
    try:
        return notes_index.load_or_build(resolve_report(report_name), format_choice)
    except PdfError as exc:
        raise UserError(str(exc))


def notes_label(notes):
    def name(n):
        if n.get("kind", "note") == "note" and isinstance(n["no"], int):
            return f"{n['section_label']} Note {n['no']} – {n['title']}"
        return n["label"]  # old-format schedule / note / report section
    return ", ".join(
        f"{name(n)} (PDF p.{n['start_page']}"
        + (f"–{n['end_page']}" if n["end_page"] != n["start_page"] else "") + ")"
        for n in notes
    )


# ---------------------------------------------------------------- old-format reports
# The prompts carry a REPORT PROFILE and name only the statements the report
# has. For modern reports these render exactly the text used before.

MODERN_STATEMENT_SECTIONS = ("### Statement of Profit and Loss\n### Balance Sheet\n"
                             "### Cash Flow Statement\n### Statement of Changes in Equity")
MODERN_STATEMENT_NAMES = "Profit and Loss, Balance Sheet, Cash Flow, Changes in Equity"


def is_legacy(index):
    return index.get("format") == "legacy"


def is_transcribed(index):
    return index.get("format") == "transcribed"


def _section_statements(index, sections):
    keys = sections or list(index["sections"])[:1]
    return [st for k in keys for st in index["sections"].get(k, {}).get("statements", [])]


def ocr_checks_text(index, sections):
    """TRANSCRIPTION CHECKS for the pages of these sections: totals that don't
    add up and figures nothing confirmed (ocr_transcribe / ocr_quality)."""
    pages = sorted({p for k in sections or list(index["sections"])[:1]
                    for u in index["sections"].get(k, {}).get("notes", []) + [
                        {"start_page": st["pages"][0]["pdf_page"], "end_page": st["pages"][-1]["pdf_page"]}
                        for st in index["sections"].get(k, {}).get("statements", [])]
                    for p in range(u["start_page"], u["end_page"] + 1)})
    lines = []
    for pno in pages:
        header = ocr_transcribe.page_header(Path(index["pdf"]), pno)
        bits = []
        if header.get("unreadable"):
            bits.append(f"{header['unreadable']} unreadable character(s) [?]")
        for item in header.get("untied", []):
            label = "" if item["label"] == "(total)" else f" ({item['label']})"
            bits.append(f"total {item['figure']}{label} does not equal the figures above it")
        for item in header.get("unchecked", []):
            bits.append(f"total {item['figure']} can't be checked (a figure above it is unreadable)")
        if header.get("figures_to_check"):
            bits.append("figures not confirmed by the cross-check: " + ", ".join(header["figures_to_check"][:12]))
        if bits:
            lines.append(f"- p.{pno}: " + "; ".join(bits))
    return "TRANSCRIPTION CHECKS (automatic)\n" + ("\n".join(lines) if lines else "- no issues found")


def report_profile(index, sections=None):
    if is_transcribed(index):
        statements_here = _section_statements(index, sections)
        missing = sorted({m for k in (sections or list(index["sections"])[:1])
                          for m in index["sections"].get(k, {}).get("statements_missing", [])})
        ocr = index.get("ocr", {})
        lines = [
            "REPORT PROFILE",
            f"Format: Scanned report, transcribed by AI from page images ({index.get('document_type') or 'report'})",
            "Companies in this report: " + "; ".join(index.get("entities") or []),
            f"Period: {index.get('fiscal_year_end_label') or 'see the statements'}"
            " (each company's statements give its own date)",
            f"Currency: {index.get('currency') or 'see the statements'}",
            "Statements provided: " + ("; ".join(st["title"] for st in statements_here) or "none found"),
        ]
        if missing:
            lines.append(f"Statement types NOT in the report for these companies: {', '.join(missing)}")
        lines.append(f"Source quality: AI transcription of {ocr.get('pages', '?')} scanned pages; [?] marks "
                     f"unreadable characters ({ocr.get('unreadable', 0)} in the report)")
        return "\n".join(lines) + "\n\n" + ocr_checks_text(index, sections) + "\n\n"
    if not is_legacy(index):
        return ""
    present = index.get("statements_present") or []
    missing = index.get("statements_missing") or []
    lines = [
        "REPORT PROFILE",
        "Format: Old Indian GAAP (Companies Act 1956, old Schedule VI) - standalone accounts only",
        f"Year ended: {index.get('fiscal_year_end_label') or 'see the statements'}"
        " (check the notes for the length of the previous period)",
        "Currency/units: Rs. with Indian digit grouping (1,00,000 = 1 lakh; 1,00,00,000 = 1 crore)",
        f"Statements in this report: {', '.join(present) or 'none found'}",
    ]
    if missing:
        lines.append(f"Statements NOT in this report: {', '.join(missing)} (not required at the time)")
    lines.append("Source quality: may be re-typed from the printed report; figures may contain "
                 "transcription errors")
    return "\n".join(lines) + "\n\n"


def statement_sections_text(index, sections=None):
    if is_transcribed(index):
        titles = list(dict.fromkeys(st["title"] for st in _section_statements(index, sections)))
        text = "\n".join(f"### {t}" for t in titles) or "### (no statements found)"
        if not any(st["type"] == "cash_flow" for st in _section_statements(index, sections)):
            text += ("\n(This report has no funds or cash flow statement. If cash movements matter to "
                     "the question, add a short derived funds-flow statement, clearly labelled as derived "
                     "by the analyst and not published.)")
        return text
    if not is_legacy(index):
        return MODERN_STATEMENT_SECTIONS
    present = index.get("statements_present") or ["Profit and Loss Account", "Balance Sheet"]
    text = "\n".join(f"### {name}" for name in present)
    if "Cash Flow Statement" not in present:
        text += ("\n(This report has no Cash Flow Statement. If cash movements matter to the "
                 "question, add a short derived funds-flow statement, clearly labelled as "
                 "derived by the analyst and not published.)")
    return text


def statement_names(index, sections=None):
    if is_transcribed(index):
        return "; ".join(dict.fromkeys(st["title"] for st in _section_statements(index, sections))) \
            or "the statements provided"
    if not is_legacy(index):
        return MODERN_STATEMENT_NAMES
    return ", ".join(index.get("statements_present") or []) or "the statements provided"


def analysis_system(index):
    text = system_prompt("analysis_system.txt")
    if is_legacy(index):
        text += "\n\n" + system_prompt("analysis_system_legacy.txt")
    if is_transcribed(index):
        text += "\n\n" + system_prompt("analysis_system_transcribed.txt")
    return text


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


def _job_progress(phase, text):
    """Stream callback: the browser's job poll shows `text` while Claude writes."""
    job_id = getattr(CURRENT_JOB, "id", None)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job and job["status"] == "running":
            job["phase"] = phase
            job["partial"] = text


def call_claude(prompt, system, timeout):
    """basic-chatbot pattern: prompt to the input file, reply to the output file.
    The analysis/follow-up reply is streamed into the running job as it arrives."""
    config.INPUT_FILE.write_text(prompt, encoding="utf-8")
    try:
        reply = run_claude(prompt, system, timeout, effort=config.CLAUDE_EFFORT,
                           on_progress=_job_progress)
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
        CURRENT_JOB.id = job_id
        try:
            result = func(*args)
            update = {"status": "done", "result": result}
        except (UserError, ClaudeError, ValueError) as exc:
            update = {"status": "error", "error": str(exc)}
        except Exception as exc:  # keep the job from hanging on bugs
            log.exception("Job %s (%s) failed", job_id, kind)
            update = {"status": "error", "error": f"Unexpected error: {exc}"}
        with JOBS_LOCK:
            JOBS[job_id].pop("partial", None)
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
        notes = note_selector.drop_covered(notes)
    extracts, extract_text = extract_selection(index, notes=notes, page_numbers=pages)
    # The primary statements (loaded with the report) always go with the notes.
    sections = statement_sections(index, notes)
    statements_text = statements.sections_text(index, sections)
    statements_list = statements.label(index, sections)
    check_size(statements_text + extract_text)

    notes_text = notes_label(notes) if notes else f"PDF pages {page_spec}"
    prompt = fill_prompt(
        "analysis_template.txt",
        PROFILE=report_profile(index, sections),
        STATEMENT_SECTIONS=statement_sections_text(index, sections),
        REPORT=report_name,
        SCOPE=scope_label(notes, pages),
        NOTES=notes_text,
        STATEMENTS_LIST=statements_list or "none found in this report",
        STATEMENTS=statements_text or "(No primary financial statements were found in this report.)",
        QUESTION=question,
        EXTRACT=extract_text,
    )
    sent_at = datetime.now()
    answer = call_claude(prompt, analysis_system(index), config.ANALYSIS_TIMEOUT)
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
        for unit_id in note_selector.explicit_unit_ids(question, index, sections):
            choice = choices[unit_id]
            if unit_id in have or (choice.get("parent") and choice["parent"] in have):
                continue
            extra = extract_note(index, choice["section"], choice["no"])
            thread["notes"].append(choice)
            thread["extract"] += "\n\n" + extra["text"]
            have.add(unit_id)
            added.append(choice)
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
        PROFILE=report_profile(index, thread.get("statement_sections")),
        STATEMENT_NAMES=statement_names(index, thread.get("statement_sections")),
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
    answer = call_claude(prompt, analysis_system(index), config.ANALYSIS_TIMEOUT)
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
    format_choice = data.get("format") or None
    if format_choice not in (None, "auto", *report_format.FORMATS):
        raise UserError(f"Unknown report format: {format_choice}")
    path = resolve_report(data.get("report", ""))
    try:
        idx = notes_index.load_or_build(path, format_choice)
    except notes_index.NeedsTranscription as exc:
        pages = list(range(1, exc.page_count + 1))
        minutes, usd, todo = ocr_transcribe.estimate(path, pages)
        return jsonify({
            "report": path.name, "page_count": exc.page_count, "needs_transcription": True,
            "status": exc.status, "tesseract": bool(ocr_quality.tesseract_path()),
            "estimate": {"minutes": minutes, "usd": usd, "pages": todo},
            "review_url": f"/ocr-review?report={path.name}",
        })
    except PdfError as exc:
        raise UserError(str(exc))
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
              ],
              "missing_statements": idx.get("statements_missing", []) if is_legacy(idx) else []}
        for key, s in idx["sections"].items()
    }
    return jsonify({
        "report": idx["pdf_name"],
        "page_count": idx["page_count"],
        "summary": notes_index.summary(idx),
        "format": idx.get("format", "modern"),
        "format_label": report_format.FORMAT_LABELS.get(idx.get("format", "modern"), idx.get("format")),
        "format_source": idx.get("format_source", "detected"),
        "ocr": idx.get("ocr"),
        "review_url": f"/ocr-review?report={idx['pdf_name']}" if is_transcribed(idx) else None,
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


# ---------------------------------------------------------------- scanned reports (OCR)

def ocr_job(report_name, page_spec, force, dpi=None):
    """Transcribe a scanned report (or some of its pages) with Claude."""
    path = resolve_report(report_name)
    pages = parse_page_spec(page_spec) if page_spec else None
    job_id = CURRENT_JOB.id

    def progress(done, total, text):
        with JOBS_LOCK:
            JOBS[job_id].update(progress=text, done=done, total=total)

    def cancelled():
        with JOBS_LOCK:
            return bool(JOBS.get(job_id, {}).get("cancel"))

    try:
        status = ocr_transcribe.run(path, pages, progress=progress, cancel=cancelled, force=force, dpi=dpi)
    except ocr_transcribe.Cancelled:
        raise UserError("Transcription cancelled. Pages already transcribed are kept: "
                        "click Transcribe to continue where it stopped.")
    log.info("Transcribed %s: %s", report_name, status)
    return status


@app.route("/api/ocr/start", methods=["POST"])
def api_ocr_start():
    data = request.get_json(silent=True) or {}
    report = data.get("report", "")
    resolve_report(report)
    pages = (data.get("pages") or "").strip()
    if pages:
        parse_page_spec(pages)   # validate now, not in the job
    return jsonify({"job_id": start_job("ocr", ocr_job, report, pages, False)})


@app.route("/api/ocr/page", methods=["POST"])
def api_ocr_page():
    """Re-transcribe one page (the review page's button)."""
    data = request.get_json(silent=True) or {}
    report = data.get("report", "")
    resolve_report(report)
    page = int(data.get("page") or 0)
    dpi = int(data["dpi"]) if data.get("dpi") else None
    return jsonify({"job_id": start_job("ocr", ocr_job, report, str(page), True, dpi)})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown job."}), 404
        job["cancel"] = True
    return jsonify({"ok": True})


@app.route("/ocr-image")
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


@app.route("/ocr-review")
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
    logo_svg = config.LOGO_FILE.read_text(encoding="utf-8") if config.LOGO_FILE.exists() else ""
    return render_template(
        "ocr_review.html", report=path.name, page=page, page_count=page_count, pages=pages,
        record=record, header=header, logo_svg=logo_svg,
        transcription_html=_highlight(render_markdown(markdown_text), header.get("figures_to_check", []))
        if markdown_text else "",
        tesseract=bool(ocr_quality.tesseract_path()),
    )


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

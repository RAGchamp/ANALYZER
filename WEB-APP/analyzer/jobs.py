"""The analyzer's background jobs: identify notes, analyze, follow up.

The Claude calls follow basic-chatbot's pattern (a fresh `claude -p` per
request, prompt saved to claude-prompt-input.txt, reply to
Claude-prompt-output.txt, every Q&A numbered in Prompt-History/). Because an
analysis can take minutes, they run as jobs the browser polls.

Two flows share these jobs (INFO/ANALYZE-NON-FIN-DATA-PLAN.md §16):
  mode "notes"     "Identify notes": the notes to the financial statements and the
                   primary statements, a financial-statement analysis (the original flow);
  mode "business"  "Analyze non notes": passages from the report's own sections (MD&A,
                   Board's report, risk factors …) with at most two notes to confirm their
                   figures, a business analysis.

Everything comes from the report package (analyzer.source); no PDF is read.
"""

import json
import logging
import re
import threading
import uuid
from datetime import datetime

import config
from analyzer import history, linked, note_selector, passages, report_html, statement_info
from analyzer.context import (analysis_system, check_size, notes_label, note_pages_label, pages_label,
                              report_profile, scope_label, statement_names, statement_sections,
                              statement_sections_text)
from analyzer.extract import extract_note, extract_passages, extract_selection
from analyzer.note_selector import all_note_choices, select_for_business, select_notes
from analyzer.source import open_report
from claude_client import ClaudeError, fill_prompt, run_claude, system_prompt
from webcommon import UserError, parse_page_spec, render_markdown, update_current_job

log = logging.getLogger("app")

THREAD_LOCK = threading.Lock()
MODES = ("notes", "business")


def passages_label(passages):
    """"Management Discussion & Analysis › Aerospace Business (PDF 80); …" for prompts, history, reports."""
    def short(p):
        parts = p["path"].split(" › ")
        return " › ".join(parts[-2:]) if len(parts) > 1 else p["path"]
    return "; ".join(f"{short(p)} (PDF {p['pages']})" for p in passages)


def question_scope(index, notes, page_spec, sections):
    """The scope shown for a question; with report passages only, the statements' sections."""
    label = scope_label(notes, page_spec)
    if label:
        return label
    return " and ".join(index["sections"][k].get("label") or k for k in sections if k in index["sections"])


def save_report(index, *, question, answer, prompt_no, sent_at, notes, page_spec,
                statements_list="", original_question=None, passages=(), scope=None):
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
            scope=scope or scope_label(notes, page_spec),
            notes_text=(notes_label(notes) if notes else f"PDF pages {page_spec}" if page_spec
                        else "none (report sections only)"),
            pages_text=note_pages_label(notes, page_spec),
            statements_text=statements_list,
            original_question=original_question,
            sections_text=passages_label(passages) if passages else "",
        )
    except Exception:
        log.exception("Could not save the HTML report to %s", config.ANALYSIS_HISTORY_DIR)
        return None


def _job_progress(phase, text):
    """Stream callback: the browser's job poll shows `text` while Claude writes."""
    update_current_job(phase=phase, partial=text)


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


# ---------------------------------------------------------------- threads

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


def _check_mode(mode):
    if mode not in MODES:
        raise UserError(f"Unknown analysis mode: {mode}")
    return mode


# ---------------------------------------------------------------- step 3: identify

def identify_job(report_name, question, mode="notes"):
    """STEP 3: scope + note selection ("notes"), or report passages + confirming notes
    ("business"), plus an extract-size estimate."""
    started = datetime.now()
    package = open_report(report_name)
    hints = linked.statement_hints(package, question)
    if _check_mode(mode) == "business":
        return _identify_business(package, report_name, question, hints, started)
    # the notes behind the statement lines the question is about (a hint)
    selection = select_notes(package.index, question, hints=hints)
    chars = 0
    if selection["notes"]:
        _, combined = extract_selection(package, notes=selection["notes"])
        chars = len(combined)
    log.info("Identify (%s, %.1fs): scope=%s method=%s notes=%s chars=%d",
             report_name, (datetime.now() - started).total_seconds(),
             selection["scope"], selection["method"],
             [n["id"] for n in selection["notes"]], chars)
    selection["chars"] = chars
    selection["max_chars"] = config.MAX_CONTEXT_CHARS
    return selection


def _identify_business(package, report_name, question, hints, started):
    # the report passages most related to the question (INFO/ANALYZE-NON-FIN-DATA-PLAN.md §4)
    candidates = passages.candidates(package, question, config.PASSAGE_CANDIDATES)
    if not candidates and not package.passages():
        raise UserError("This report's sections outside the notes and statements have not been read into "
                        "passages (re-ingest it on the Ingest screen), so there is nothing to analyze here. "
                        "Use Identify notes instead.")
    if not candidates:
        raise UserError("None of this report's passages outside the notes and statements uses the words of "
                        "the question. Try other words, or use Identify notes instead.")
    selection = select_for_business(package.index, question, candidates, hints=hints)
    # a section the question names ("what does the MD&A say …") comes first
    named = passages.explicit(package, question)
    chosen = named + [p for p in selection["passages"] if p["id"] not in {n["id"] for n in named}]
    selection["passages"] = chosen[:config.MAX_PASSAGES]
    picked = {p["id"] for p in selection["passages"]}
    selection["passage_suggestions"] = [c for c in candidates if c["id"] not in picked][:6]
    chars = sum(p["chars"] for p in selection["passages"])
    if selection["notes"]:
        _, combined = extract_selection(package, notes=selection["notes"])
        chars += len(combined)
    log.info("Identify business (%s, %.1fs): method=%s passages=%s notes=%s chars=%d",
             report_name, (datetime.now() - started).total_seconds(), selection["method"],
             [p["id"] for p in selection["passages"]], [n["id"] for n in selection["notes"]], chars)
    selection["mode"] = "business"
    selection["chars"] = chars
    selection["max_chars"] = config.MAX_CONTEXT_CHARS
    return selection


# ---------------------------------------------------------------- analysis

def analyze_job(report_name, question, note_ids, page_spec, passage_ids=None, mode="notes"):
    """STEPS 4-6: extract, send to Claude, save history, start a thread."""
    if _check_mode(mode) == "business" and not page_spec:
        return _analyze_business(report_name, question, note_ids, passage_ids or [])
    started = datetime.now()
    package = open_report(report_name)
    index = package.index
    choices = {c["id"]: c for c in all_note_choices(index, "both")}

    pages = parse_page_spec(page_spec) if page_spec else None
    notes = []
    if not pages:
        notes = [choices[i] for i in note_ids if i in choices]
        if not notes:
            raise UserError("Select at least one note (or enter PDF pages) to analyze.")
        notes = note_selector.drop_covered(notes)
    extracts, extract_text = extract_selection(package, notes=notes, page_numbers=pages)
    # The primary statements (loaded with the report) always go with the notes.
    sections = statement_sections(index, notes)
    statements_text = statement_info.sections_text(index, sections)
    statements_list = statement_info.label(index, sections)
    check_size(statements_text + extract_text)

    notes_text = notes_label(notes) if notes else f"PDF pages {page_spec}"
    prompt = fill_prompt(
        "analysis_template.txt",
        PROFILE=report_profile(package, sections),
        STATEMENT_SECTIONS=statement_sections_text(index, sections),
        REPORT=report_name,
        SCOPE=scope_label(notes, pages),
        NOTES=notes_text,
        STATEMENTS_LIST=statements_list or "none found in this report",
        STATEMENTS=statements_text or "(No primary financial statements were found in this report.)",
        LINKED=linked.linked_block(package, notes, sections) if notes else "",
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
        # the package the thread was built from (INFO/SPLIT-FUNCTIONALITY-PLAN.md §8.2)
        "pdf_sha256": package.meta.get("pdf_sha256"),
        "schema_version": package.meta.get("schema_version"),
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


def _business_prompt(template, package, report_name, question, thread, conversation=None):
    """The "Analyze non notes" prompt: report sections first, narrative figures, the confirming
    notes, then the statements for reference."""
    sections = thread["statement_sections"]
    values = dict(
        PROFILE=report_profile(package, sections),
        REPORT=report_name,
        SECTIONS_LIST=passages_label(thread["passages"]),
        NOTES=notes_label(thread["notes"]) if thread["notes"] else "none",
        STATEMENTS_LIST=thread["statements_list"] or "none found in this report",
        QUESTION=question,
        SECTIONS=thread["sections_text"],
        NARRATIVE=linked.narrative_figures_block(package, thread["passages"], sections, thread["extract"]),
        EXTRACT=thread["extract"] or "(No notes were chosen to confirm figures.)",
        STATEMENTS=thread["statements"] or "(No primary financial statements were found in this report.)",
    )
    if conversation is not None:
        values["CONVERSATION"] = conversation
    return fill_prompt(template, **values)


def _analyze_business(report_name, question, note_ids, passage_ids):
    started = datetime.now()
    package = open_report(report_name)
    index = package.index
    if not passage_ids:
        raise UserError("Select at least one report passage to analyze.")
    choices = {c["id"]: c for c in all_note_choices(index, "both")}
    notes = note_selector.drop_covered([choices[i] for i in note_ids if i in choices])
    extracts, extract_text = extract_selection(package, notes=notes) if notes else ([], "")
    sections = statement_sections(index, notes)
    statements_text = statement_info.sections_text(index, sections)
    statements_list = statement_info.label(index, sections)
    # passages fill what is left of the budget; the lowest-ranked are left out first
    room = config.MAX_CONTEXT_CHARS - len(statements_text) - len(extract_text) - 3000
    sent_passages, dropped, sections_text = extract_passages(package, passage_ids, room)
    if not sent_passages:
        raise UserError("None of the selected report passages was found in this report.")
    check_size(statements_text + extract_text + sections_text)
    scope = question_scope(index, notes, None, sections)

    thread = {
        "id": uuid.uuid4().hex,
        "mode": "business",
        "report": report_name,
        "pdf_sha256": package.meta.get("pdf_sha256"),
        "schema_version": package.meta.get("schema_version"),
        "notes": notes,
        "page_spec": "",
        "extract": extract_text,
        "statement_sections": sections,
        "statements": statements_text,
        "statements_list": statements_list,
        "passages": sent_passages,
        "sections_text": sections_text,
        "turns": [],
    }
    prompt = _business_prompt("business_template.txt", package, report_name, question, thread)
    sent_at = datetime.now()
    answer = call_claude(prompt, system_prompt("business_system.txt"), config.ANALYSIS_TIMEOUT)
    saved_html = save_report(index, question=question, answer=answer, prompt_no=1, sent_at=sent_at,
                             notes=notes, page_spec="", statements_list=statements_list,
                             passages=sent_passages, scope=scope)
    sno = history.next_sno()
    history.save_history(sno, question, answer, sent_at, {
        "Kind": "business analysis (non notes)",
        "Report": report_name,
        "Scope": scope,
        "Report passages": passages_label(sent_passages),
        "Notes": notes_label(notes) if notes else "none",
        "PDF pages": ", ".join(p["pages"] for p in sent_passages),
        "Financial statements": statements_list,
        "Thread": thread["id"],
        "Saved HTML": saved_html or "",
    }, prompt)
    thread["turns"].append({"sno": sno, "question": question, "answer": answer,
                            "timestamp": sent_at.strftime(history.TIMESTAMP_FORMAT),
                            "saved_html": saved_html})
    save_thread(thread)
    log.info("Analyze business (%s, %.1fs): passages=%s notes=%s prompt=%d chars answer=%d chars sno=%d",
             report_name, (datetime.now() - started).total_seconds(), [p["id"] for p in sent_passages],
             [n["id"] for n in notes], len(prompt), len(answer), sno)
    return {
        "thread_id": thread["id"],
        "mode": "business",
        "sno": sno,
        "question": question,
        "answer": answer,
        "answer_html": render_markdown(answer),
        "notes": notes,
        "passages": sent_passages,
        "dropped_passages": dropped,
        "pages": ", ".join(p["pages"] for p in sent_passages),
        "extract": "\n\n".join(t for t in (sections_text, extract_text) if t),
        "statements": statements_text,
        "statements_list": statements_list,
        "timestamp": sent_at.strftime(history.TIMESTAMP_FORMAT),
        "saved_html": saved_html,
    }


# ---------------------------------------------------------------- follow-ups

def _add_named_notes(package, thread, question):
    """"Note 41" in a follow-up adds that note to the thread."""
    index = package.index
    added = []
    sections = sorted({n["section"] for n in thread["notes"]}) or ["consolidated"]
    have = {n["id"] for n in thread["notes"]}
    choices = {c["id"]: c for c in all_note_choices(index, "both")}
    for unit_id in note_selector.explicit_unit_ids(question, index, sections):
        choice = choices[unit_id]
        if unit_id in have or (choice.get("parent") and choice["parent"] in have):
            continue
        extra = extract_note(package, choice["section"], choice["no"])
        thread["notes"].append(choice)
        thread["extract"] = "\n\n".join(t for t in (thread["extract"], extra["text"]) if t)
        have.add(unit_id)
        added.append(choice)
    return added


def _followup_question(thread, question):
    # "what about last year?" means little alone: the selector also sees the thread's first question
    first = thread["turns"][0]["question"] if thread["turns"] else ""
    return f"{question}\n\n(A follow-up to the earlier question: {first})" if first else question


def _thread_chars(thread):
    return len(thread.get("statements") or "") + len(thread["extract"]) + len(thread.get("sections_text") or "")


def _add_notes(package, thread, notes, limit):
    """Add these notes (best first) that the thread does not have yet, at most `limit`, skipping
    any that would take the thread over MAX_CONTEXT_CHARS."""
    added = []
    have = {n["id"] for n in thread["notes"]}
    for choice in notes:
        if len(added) >= limit:
            break
        if choice["id"] in have or (choice.get("parent") and choice["parent"] in have):
            continue
        extra = extract_note(package, choice["section"], choice["no"])
        if _thread_chars(thread) + len(extra["text"]) + 2 > config.MAX_CONTEXT_CHARS:
            log.info("Follow-up rescan: %s left out (size limit)", choice["id"])
            continue
        thread["notes"].append(choice)
        thread["extract"] = "\n\n".join(t for t in (thread["extract"], extra["text"]) if t)
        have.add(choice["id"])
        added.append(choice)
    return added


def _rescan_notes(package, thread, question):
    """A follow-up searches the whole report again: the notes the selector picks for it that the
    thread does not have yet are added. Keyword guesses (selector unavailable) add nothing."""
    selection = select_notes(package.index, _followup_question(thread, question),
                             hints=linked.statement_hints(package, question))
    if selection["method"] != "claude":
        return []
    added = _add_notes(package, thread, selection["notes"], config.MAX_FOLLOWUP_NEW_NOTES)
    if added:
        # a note from the other section (standalone / consolidated) brings its statements
        sections = statement_sections(package.index, thread["notes"])
        if sections != thread.get("statement_sections"):
            thread["statement_sections"] = sections
            thread["statements"] = statement_info.sections_text(package.index, sections)
            thread["statements_list"] = statement_info.label(package.index, sections)
    log.info("Follow-up rescan (notes): picked=%s added=%s", [n["id"] for n in selection["notes"]],
             [n["id"] for n in added])
    return added


def _rescan_business(package, thread, question):
    """The same for an "Analyze non notes" thread: the passages (and confirming notes) the business
    selector picks for the follow-up that the thread does not have yet."""
    candidates = passages.candidates(package, question, config.PASSAGE_CANDIDATES)
    if not candidates:
        return [], []
    selection = select_for_business(package.index, _followup_question(thread, question), candidates,
                                    hints=linked.statement_hints(package, question))
    have = {p["id"] for p in thread["passages"]}
    added_passages = []
    for found in selection["passages"]:
        if len(added_passages) >= config.MAX_FOLLOWUP_NEW_PASSAGES:
            break
        if found["id"] in have:
            continue
        sent, _, text = extract_passages(package, [found["id"]])
        if not sent or _thread_chars(thread) + len(text) + 2 > config.MAX_CONTEXT_CHARS:
            continue
        thread["passages"] += sent
        thread["sections_text"] = "\n\n".join(t for t in (thread["sections_text"], text) if t)
        have.add(found["id"])
        added_passages += sent
    added_notes = _add_notes(package, thread, selection["notes"], config.MAX_BUSINESS_NOTES)
    log.info("Follow-up rescan (business): passages=%s notes=%s", [p["id"] for p in added_passages],
             [n["id"] for n in added_notes])
    return added_passages, added_notes


def followup_job(thread_id, question):
    """Follow-up question in a thread. The report is searched again for it and the notes it needs
    are added to the thread's (earlier ones stay); "Note 41" in the question adds that note.
    A thread on manual PDF pages keeps its pages."""
    with THREAD_LOCK:
        thread = load_thread(thread_id)
    if thread.get("mode") == "business":
        return _followup_business(thread, question)
    package = open_report(thread["report"])
    index = package.index
    added = []
    if not thread.get("page_spec"):
        added = _add_named_notes(package, thread, question)
    # Threads started before statements were added don't carry them yet.
    if "statements" not in thread:
        sections = statement_sections(index, thread["notes"])
        thread["statement_sections"] = sections
        thread["statements"] = statement_info.sections_text(index, sections)
        thread["statements_list"] = statement_info.label(index, sections)
    if not thread.get("page_spec"):
        added += _rescan_notes(package, thread, question)
    check_size(thread["statements"] + thread["extract"])

    recent = thread["turns"][-config.MAX_FOLLOWUP_TURNS:]
    conversation = "\n\n".join(
        f"User asked: {t['question']}\nYou answered: {t['answer']}" for t in recent)
    notes_text = (notes_label(thread["notes"]) if thread["notes"]
                  else f"PDF pages {thread['page_spec']}")
    prompt = fill_prompt(
        "followup_template.txt",
        PROFILE=report_profile(package, thread.get("statement_sections")),
        STATEMENT_NAMES=statement_names(index, thread.get("statement_sections")),
        REPORT=thread["report"],
        SCOPE=scope_label(thread["notes"], thread.get("page_spec")),
        NOTES=notes_text,
        STATEMENTS_LIST=thread["statements_list"] or "none found in this report",
        STATEMENTS=thread["statements"] or "(No primary financial statements were found in this report.)",
        LINKED=(linked.linked_block(package, thread["notes"], thread.get("statement_sections")
                                    or statement_sections(index, thread["notes"]))
                if thread["notes"] else ""),
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
        "rescanned": not thread.get("page_spec"),
        "notes": thread["notes"],
        "extract": thread["extract"],
        "statements": thread["statements"],
        "statements_list": thread["statements_list"],
        "timestamp": sent_at.strftime(history.TIMESTAMP_FORMAT),
        "saved_html": saved_html,
    }


def _followup_business(thread, question):
    """A follow-up in an "Analyze non notes" thread: the report is searched again and the passages
    and notes it needs are added to the thread's; naming a section ("what does the Board's report
    say …") adds its best passage, "Note 49" adds that note."""
    package = open_report(thread["report"])
    index = package.index
    added = _add_named_notes(package, thread, question)
    added_passages = []
    have = {p["id"] for p in thread["passages"]}
    for found in passages.explicit(package, question, have)[:1]:
        sent, _, text = extract_passages(package, [found["id"]])
        if sent:
            thread["passages"] += sent
            thread["sections_text"] = "\n\n".join(t for t in (thread["sections_text"], text) if t)
            added_passages += sent
    more_passages, more_notes = _rescan_business(package, thread, question)
    added_passages += more_passages
    added += more_notes
    check_size(thread["statements"] + thread["extract"] + thread["sections_text"])
    recent = thread["turns"][-config.MAX_FOLLOWUP_TURNS:]
    conversation = "\n\n".join(f"User asked: {t['question']}\nYou answered: {t['answer']}" for t in recent)
    prompt = _business_prompt("business_followup_template.txt", package, thread["report"], question, thread,
                              conversation)
    sent_at = datetime.now()
    answer = call_claude(prompt, system_prompt("business_system.txt"), config.ANALYSIS_TIMEOUT)
    prompt_no = len(thread["turns"]) + 1
    scope = question_scope(index, thread["notes"], None, thread["statement_sections"])
    saved_html = save_report(
        index, question=question, answer=answer, prompt_no=prompt_no, sent_at=sent_at,
        notes=thread["notes"], page_spec="", statements_list=thread["statements_list"],
        original_question=thread["turns"][0]["question"] if thread["turns"] else None,
        passages=thread["passages"], scope=scope)
    sno = history.next_sno()
    history.save_history(sno, question, answer, sent_at, {
        "Kind": "business follow-up (non notes)",
        "Report": thread["report"],
        "Scope": scope,
        "Report passages": passages_label(thread["passages"]),
        "Notes": notes_label(thread["notes"]) if thread["notes"] else "none",
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
        "mode": "business",
        "sno": sno,
        "question": question,
        "answer": answer,
        "answer_html": render_markdown(answer),
        "added_notes": added,
        "added_passages": added_passages,
        "rescanned": True,
        "notes": thread["notes"],
        "passages": thread["passages"],
        "extract": "\n\n".join(t for t in (thread["sections_text"], thread["extract"]) if t),
        "statements": thread["statements"],
        "statements_list": thread["statements_list"],
        "timestamp": sent_at.strftime(history.TIMESTAMP_FORMAT),
        "saved_html": saved_html,
    }

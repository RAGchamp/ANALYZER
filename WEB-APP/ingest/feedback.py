"""Model feedback (INFO/MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md §3, §7): a report
that a page was extracted wrongly, and the audit trail of reports and fixes.

    Model-Feedback/<pdf stem>-<YYYYMMDD-HHMMSS>.json   one per report, never edited
    Model-Feedback/feedback-status.json               one entry per report: status
                                                      and its full history
    Model-Testing/<feedback stem>-p<page>.png         the page the user looked at

Statuses: "Reported" -> "Fixed by Claude code". Fixing stays manual: in a
Claude Code session, which records the fix with

    python -m ingest.feedback list [--open]
    python -m ingest.feedback show <feedback file>     re-extract the page now, diff with the report
    python -m ingest.feedback fixed <feedback file> --summary "..." [--changed f.py ...] [--test ...]
"""

import argparse
import difflib
import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

import config
from webcommon import UserError

STATUS_FILE = "feedback-status.json"
SCHEMA_VERSION = 1
REPORTED = "Reported"
FIXED = "Fixed by Claude code"

_lock = threading.Lock()


def _now():
    """Local time with its UTC offset."""
    return datetime.now().astimezone().replace(microsecond=0)


def _status_path():
    return Path(config.MODEL_FEEDBACK_DIR) / STATUS_FILE


def _relative(path):
    """A path as stored in the files: relative to WEB-APP when it is inside it."""
    try:
        return Path(path).relative_to(config.BASE_DIR).as_posix()
    except ValueError:
        return str(path)


def check_comment(comment):
    comment = (comment or "").strip()
    if not comment:
        raise UserError("Describe what is wrong before submitting.")
    if len(comment) > config.FEEDBACK_MAX_CHARS:
        raise UserError(f"The comment is {len(comment)} characters; the limit is {config.FEEDBACK_MAX_CHARS}.")
    return comment


# ---------------------------------------------------------------- the status file

def load_status():
    """The audit trail. A missing file is an empty trail; an unreadable one is an
    error - starting a new file would lose the trail."""
    path = _status_path()
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("entries"), list):
            raise ValueError("no 'entries' list")
    except (ValueError, OSError) as exc:
        raise UserError(f"{path} can't be read ({exc}). Fix it by hand; nothing was written.")
    return data


def _write_json(path, data):
    """Atomic: a crash leaves the old file, never half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _entry(status, name):
    entry = next((e for e in status["entries"] if e["feedback_file"] == name), None)
    if entry is None:
        raise UserError(f"{name} is not in {STATUS_FILE}.")
    return entry


# ---------------------------------------------------------------- reporting

def _new_name(stem, when):
    """<stem>-<YYYYMMDD-HHMMSS>.json, with -2, -3 ... for the same second."""
    base = f"{stem}-{when:%Y%m%d-%H%M%S}"
    folder = Path(config.MODEL_FEEDBACK_DIR)
    n = 1
    while True:
        name = f"{base}.json" if n == 1 else f"{base}-{n}.json"
        if not (folder / name).exists():
            return name
        n += 1


def submit(pdf_name, result, comment, png=None):
    """Write the feedback file (and the page image) and add it to the trail as
    Reported. result: model_testing.extract_page() of the page, made by the
    server - never data sent back by the browser."""
    comment = check_comment(comment)
    folder = Path(config.MODEL_FEEDBACK_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    with _lock:
        status = load_status()                     # fails before anything is written
        when = _now()
        name = _new_name(Path(pdf_name).stem, when)
        context = dict(result["context"])
        if png is not None:
            images = Path(config.MODEL_TESTING_DIR)
            images.mkdir(parents=True, exist_ok=True)
            image = images / f"{Path(name).stem}-p{result['page']}.png"
            image.write_bytes(png)
            context["page_image"] = _relative(image)
        record = {
            "schema_version": SCHEMA_VERSION,
            "feedback_file": name,
            "pdf_file": pdf_name,
            "page": result["page"],
            "printed_page": result.get("printed"),
            "reported_at": when.isoformat(),
            "user_feedback": comment,
            "extracted_data": result["extracted"],
            "notices": result.get("notices", []),
            "context": context,
        }
        with open(folder / name, "x", encoding="utf-8") as handle:
            handle.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        status["entries"].append({
            "feedback_file": name,
            "pdf_file": pdf_name,
            "page": result["page"],
            "date_reported": when.isoformat(),
            "status": REPORTED,
            "date_fixed": None,
            "history": [{"status": REPORTED, "at": when.isoformat()}],
        })
        _write_json(_status_path(), status)
    return {"feedback_file": name, "status": REPORTED, "page_image": context.get("page_image")}


def mark_fixed(name, summary, changed=None, test=None):
    """Record that Claude Code fixed the extraction reported in `name`."""
    summary = (summary or "").strip()
    if not summary:
        raise UserError("Say what was fixed (--summary).")
    with _lock:
        status = load_status()
        entry = _entry(status, name)
        if entry["status"] == FIXED:
            raise UserError(f"{name} is already fixed ({entry['date_fixed']}).")
        when = _now().isoformat()
        event = {"status": FIXED, "at": when, "summary": summary}
        if changed:
            event["changed"] = list(changed)
        if test:
            event["test"] = test
        entry["history"].append(event)
        entry["status"] = FIXED
        entry["date_fixed"] = when
        _write_json(_status_path(), status)
    return entry


def entries():
    """Newest first."""
    return list(reversed(load_status()["entries"]))


def read_feedback(name):
    """One feedback file; only names in the trail (no other paths)."""
    _entry(load_status(), name)
    path = Path(config.MODEL_FEEDBACK_DIR) / name
    if not re.fullmatch(r"[\w.\- ]+\.json", name) or not path.is_file():
        raise UserError(f"Feedback file not found: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- command line

def _show(name):
    from ingest import model_testing
    record = read_feedback(name)
    print(f"{record['feedback_file']}\n  {record['pdf_file']}, PDF page {record['page']} "
          f"(printed {record.get('printed_page')}), reported {record['reported_at']}")
    print(f"  Feedback: {record['user_feedback']}")
    for notice in record.get("notices", []):
        print(f"  Notice: {notice}")
    print(f"  Made with: {json.dumps(record['context'], ensure_ascii=False)}")
    path = model_testing.resolve_model_doc(record["pdf_file"])
    now = model_testing.extract_page(path, record["page"])
    before = model_testing.format_extracted({"page": record["page"], "printed": record.get("printed_page"),
                                             "extracted": record["extracted_data"]})
    after = model_testing.format_extracted(now)
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                     "extracted when reported", "extracted now", lineterm=""))
    print("\n".join(diff) if diff else "\nThe extraction is unchanged since the report:\n\n" + after)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m ingest.feedback", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="the audit trail")
    listing.add_argument("--open", action="store_true", help="only the reports not fixed yet")
    sub.add_parser("show", help="a report, and the page extracted again now").add_argument("file")
    fixed = sub.add_parser("fixed", help=f"mark a report '{FIXED}'")
    fixed.add_argument("file")
    fixed.add_argument("--summary", required=True)
    fixed.add_argument("--changed", nargs="*")
    fixed.add_argument("--test")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")    # ₹ on a Windows console
    try:
        if args.command == "list":
            rows = [e for e in entries() if not args.open or e["status"] != FIXED]
            for e in rows:
                fixed_at = f"  fixed {e['date_fixed']}" if e.get("date_fixed") else ""
                print(f"{e['status']:<22} {e['date_reported']}  p.{e['page']:<4} {e['feedback_file']}{fixed_at}")
            print(f"{len(rows)} report(s)")
        elif args.command == "show":
            _show(Path(args.file).name)
        else:
            entry = mark_fixed(Path(args.file).name, args.summary, args.changed, args.test)
            print(f"{entry['feedback_file']}: {entry['status']} at {entry['date_fixed']}")
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Web plumbing shared by the Analyze and Ingest screens: user errors, the
background job runner the browser polls, Markdown rendering and the list of
reports. Nothing here reads a PDF or talks to Claude."""

import logging
import re
import threading
import uuid
from datetime import datetime

import markdown

import config
from claude_client import ClaudeError

log = logging.getLogger("app")


class UserError(Exception):
    """A problem to show the user as-is (bad input, too much content...)."""


# ---------------------------------------------------------------- Markdown

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


def logo_svg():
    return config.LOGO_FILE.read_text(encoding="utf-8") if config.LOGO_FILE.exists() else ""


# ---------------------------------------------------------------- reports

def list_reports():
    if not config.REPORTS_DIR.is_dir():
        return []
    return sorted(p for p in config.REPORTS_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() == ".pdf")


def parse_page_spec(spec):
    """"405-407, 410" -> [405, 406, 407, 410]"""
    pages = []
    spec = re.sub(r"\s*[-–—]\s*", "-", spec.strip())
    for part in re.split(r"[,\s]+", spec):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(f"Can't read page range '{part}'. Use e.g. 405-407, 410")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start or end - start > 100:
            raise ValueError(f"Invalid page range '{part}'")
        pages.extend(range(start, end + 1))
    return sorted(dict.fromkeys(pages))


def resolve_report(name):
    """Only allow PDFs that are actually listed in REPORTS_DIR."""
    for path in list_reports():
        if path.name == name:
            return path
    raise UserError(f"Report not found in {config.REPORTS_DIR}: {name}")


# ---------------------------------------------------------------- jobs

JOBS = {}
JOBS_LOCK = threading.Lock()
# Which job the current worker thread is running, so a job can publish its
# progress (the answer so far, OCR pages done) to it.
CURRENT_JOB = threading.local()


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


def current_job_id():
    """The job this worker thread runs. Capture it before handing work to other
    threads (the OCR thread pool): CURRENT_JOB is per thread."""
    return getattr(CURRENT_JOB, "id", None)


def update_job(job_id, **values):
    """Merge values into a running job."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job and job["status"] == "running":
            job.update(values)


def update_current_job(**values):
    update_job(current_job_id(), **values)


def job_cancelled(job_id):
    with JOBS_LOCK:
        return bool(JOBS.get(job_id, {}).get("cancel"))

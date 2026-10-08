"""Transcribe scanned (image-only) reports page by page with Claude.

INFO/OCR-CAPABILITY-PLAN.md §4. Each page is rendered to a greyscale PNG and
read by `claude -p --tools Read` (claude_client.run_claude_with_image), which
returns a JSON description of the page plus the page as Markdown. Everything
is cached in cache\\<pdf stem>.ocr\\:

  manifest.json      pdf size/mtime, prompt version, revision, per-page status
  page-007.png       the image Claude read (shown on the review page)
  page-007.md        the transcription
  page-007.json      the page description + quality checks

Transcription is resumable (done pages are skipped), runs OCR_WORKERS pages
in parallel, retries a page once at OCR_RETRY_DPI when it has many [?]
marks, and never changes figures. The optional Tesseract pass only
cross-checks figures (ocr_quality).
"""

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pymupdf

import config
from claude_client import ClaudeError, fill_prompt, run_claude_with_image
from ingest import ocr_quality
from ingest.pdf_utils import open_pdf

log = logging.getLogger(__name__)

PAGE_TYPES = ("statement", "notes", "schedule", "auditors_report", "narrative", "cover",
              "index", "exhibit_cover", "blank", "other")
JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
SCANNED_TEXT_SHARE = 0.1          # the existing "looks scanned" rule

_pdf_lock = threading.Lock()      # PyMuPDF is not thread-safe
_manifest_locks = {}
_locks_lock = threading.Lock()


class Cancelled(Exception):
    pass


# ------------------------------------------------------------------ paths & manifest

def ocr_dir(pdf_path):
    return config.CACHE_DIR / f"{Path(pdf_path).stem}.ocr"


def page_file(pdf_path, pno, ext):
    return ocr_dir(pdf_path) / f"page-{pno:03d}.{ext}"


def _lock(pdf_path):
    with _locks_lock:
        return _manifest_locks.setdefault(str(pdf_path), threading.Lock())


def is_scanned(doc):
    text_pages = sum(1 for page in doc if page.get_text().strip())
    return text_pages < max(1, int(doc.page_count * SCANNED_TEXT_SHARE))


def load_manifest(pdf_path):
    """The manifest for this PDF; a fresh one if the PDF or prompt changed."""
    pdf_path = Path(pdf_path)
    stat = pdf_path.stat()
    path = ocr_dir(pdf_path) / "manifest.json"
    fresh = {"pdf_name": pdf_path.name, "size": stat.st_size, "mtime": stat.st_mtime,
             "prompt_version": config.OCR_PROMPT_VERSION, "revision": 0,
             "page_count": None, "requested": [], "pages": {}}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fresh
    if (manifest.get("size"), manifest.get("mtime")) != (stat.st_size, stat.st_mtime):
        return fresh
    if manifest.get("prompt_version") != config.OCR_PROMPT_VERSION:
        manifest.update(prompt_version=config.OCR_PROMPT_VERSION, pages={},
                        revision=manifest.get("revision", 0) + 1)
    return manifest


def save_manifest(pdf_path, manifest):
    folder = ocr_dir(pdf_path)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    tmp.replace(folder / "manifest.json")


def status(pdf_path):
    """Summary for the UI: how much of the report is transcribed."""
    manifest = load_manifest(pdf_path)
    requested = manifest["requested"]
    pages = manifest["pages"]
    done = [p for p in requested if pages.get(str(p), {}).get("status") == "done"]
    failed = [p for p in requested if pages.get(str(p), {}).get("status") == "failed"]
    return {
        "page_count": manifest["page_count"],
        "requested": requested,
        "done": len(done),
        "failed": failed,
        "complete": bool(requested) and len(done) + len(failed) == len(requested) and bool(done),
        "revision": manifest["revision"],
        "unreadable": sum(pages.get(str(p), {}).get("unreadable", 0) for p in done),
        "to_check": sum(len(pages.get(str(p), {}).get("figures_to_check", [])) for p in done),
        "untied": sum(len(pages.get(str(p), {}).get("untied", [])) for p in done),
        "seconds": round(sum(pages.get(str(p), {}).get("seconds", 0) for p in done), 1),
        "cost_usd": round(sum(pages.get(str(p), {}).get("cost_usd", 0) for p in done), 2),
    }


def estimate(pdf_path, pages):
    """(minutes, dollars) for transcribing `pages`, from the running average."""
    manifest = load_manifest(pdf_path)
    done = [v for v in manifest["pages"].values() if v.get("status") == "done"]
    per_page_s = (sum(v["seconds"] for v in done) / len(done)) if done else config.OCR_EST_SECONDS_PER_PAGE
    per_page_usd = (sum(v["cost_usd"] for v in done) / len(done)) if done else config.OCR_EST_COST_PER_PAGE
    todo = [p for p in pages if manifest["pages"].get(str(p), {}).get("status") != "done"]
    minutes = len(todo) * per_page_s / max(1, config.OCR_WORKERS) / 60
    return round(max(minutes, 0.1 if todo else 0), 1), round(len(todo) * per_page_usd, 2), len(todo)


# ------------------------------------------------------------------ one page

def render_page(pdf_path, pno, dpi, filename=None):
    target = page_file(pdf_path, pno, "png") if filename is None else ocr_dir(pdf_path) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    with _pdf_lock:
        doc = open_pdf(Path(pdf_path))
        try:
            doc[pno - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).save(str(target))
        finally:
            doc.close()
    return target


def parse_reply(text):
    """(page description dict, Markdown) from Claude's two-part reply."""
    match = JSON_BLOCK_RE.search(text)
    header = {}
    markdown = text
    if match:
        try:
            header = json.loads(match.group(1))
        except ValueError:
            header = {}
        markdown = text[match.end():]
    markdown = markdown.strip()
    if header.get("page_type") not in PAGE_TYPES:
        header["page_type"] = "other"
    header["unreadable"] = markdown.count("[?]")
    header.setdefault("headings", [])
    return header, markdown


def _ask_claude(pdf_path, pno, dpi, model):
    image = render_page(pdf_path, pno, dpi)
    prompt = fill_prompt("transcribe_page.txt", IMAGE=image.name)
    last_error = None
    for attempt in range(2):                     # one retry on errors / timeouts
        try:
            reply, meta = run_claude_with_image(prompt, image.parent, config.OCR_TIMEOUT,
                                                model=model, effort=config.OCR_EFFORT)
            header, markdown = parse_reply(reply)
            return header, markdown, meta
        except ClaudeError as exc:
            last_error = exc
            log.warning("Page %s attempt %s failed: %s", pno, attempt + 1, exc)
    raise last_error


def transcribe_page(pdf_path, pno, model=None, dpi=None):
    """Transcribe one page, run the checks, write its files. Returns its manifest record."""
    started = time.monotonic()
    dpi = dpi or config.OCR_DPI
    header, markdown, meta = _ask_claude(pdf_path, pno, dpi, model)
    cost = meta["cost_usd"]
    if header["unreadable"] >= config.OCR_RETRY_UNREADABLE and dpi < config.OCR_RETRY_DPI:
        # Many unreadable characters: look again at the scan's native resolution.
        h2, m2, meta2 = _ask_claude(pdf_path, pno, config.OCR_RETRY_DPI, model)
        cost += meta2["cost_usd"]
        if h2["unreadable"] < header["unreadable"]:
            header, markdown, meta, dpi = h2, m2, meta2, config.OCR_RETRY_DPI
        else:
            render_page(pdf_path, pno, dpi)       # keep the image that matches the text

    tie, figures_to_check, tesseract_used = _checks(pdf_path, pno, markdown, header["page_type"])

    header.update({"pdf_page": pno, "dpi": dpi, "model": meta["model"],
                   "tied": tie["tied"], "untied": tie["untied"], "unchecked": tie["unchecked"],
                   "figures_to_check": figures_to_check, "tesseract": tesseract_used})
    page_file(pdf_path, pno, "md").write_text(markdown + "\n", encoding="utf-8")
    page_file(pdf_path, pno, "json").write_text(json.dumps(header, indent=1), encoding="utf-8")
    return {
        "status": "done", "dpi": dpi, "model": meta["model"],
        "seconds": round(time.monotonic() - started, 1), "cost_usd": round(cost, 4),
        "unreadable": header["unreadable"], "page_type": header["page_type"],
        "figures_to_check": figures_to_check, "untied": tie["untied"],
        "tesseract": tesseract_used,
    }


# ------------------------------------------------------------------ whole report

def run(pdf_path, pages=None, progress=None, cancel=None, force=False, model=None, dpi=None,
        transcriber=None):
    """Transcribe `pages` (default: all) of a scanned PDF. Resumable.

    progress(done, total, text) is called as pages finish; cancel() -> True
    stops scheduling new pages (pages already running finish). `transcriber`
    replaces transcribe_page (tests)."""
    pdf_path = Path(pdf_path)
    transcriber = transcriber or transcribe_page
    with _pdf_lock:
        doc = open_pdf(pdf_path)
        page_count = doc.page_count
        doc.close()
    pages = sorted(set(pages or range(1, page_count + 1)))
    if any(not 1 <= p <= page_count for p in pages):
        raise ValueError(f"Pages must be between 1 and {page_count}.")

    lock = _lock(pdf_path)
    with lock:
        manifest = load_manifest(pdf_path)
        manifest["page_count"] = page_count
        manifest["requested"] = sorted(set(manifest["requested"]) | set(pages))
        save_manifest(pdf_path, manifest)
    todo = [p for p in pages if force or manifest["pages"].get(str(p), {}).get("status") != "done"]
    total, finished = len(pages), len(pages) - len(todo)
    if progress:
        progress(finished, total, f"{finished} of {total} pages already transcribed")

    def work(pno):
        if cancel and cancel():
            raise Cancelled()
        try:
            record = transcriber(pdf_path, pno, model=model, dpi=dpi)
        except ClaudeError as exc:
            record = {"status": "failed", "error": str(exc)[:500]}
        return pno, record

    with ThreadPoolExecutor(max_workers=max(1, config.OCR_WORKERS)) as pool:
        futures = [pool.submit(work, p) for p in todo]
        cancelled = False
        for future in as_completed(futures):
            try:
                pno, record = future.result()
            except Cancelled:
                cancelled = True
                continue
            with lock:
                manifest = load_manifest(pdf_path)
                manifest["pages"][str(pno)] = record
                manifest["revision"] = manifest.get("revision", 0) + 1
                save_manifest(pdf_path, manifest)
            finished += 1
            if progress:
                state = "transcribed" if record["status"] == "done" else "FAILED"
                progress(finished, total, f"Page {pno} {state} ({finished} of {total})")
        if cancelled:
            raise Cancelled()
    return status(pdf_path)


def retranscribe_page(pdf_path, pno, dpi=None, model=None):
    """The review page's "Re-transcribe this page" button."""
    return run(pdf_path, pages=[pno], force=True, dpi=dpi, model=model)


# ------------------------------------------------------------------ reading the cache

def page_markdown(pdf_path, pno):
    try:
        return page_file(pdf_path, pno, "md").read_text(encoding="utf-8")
    except OSError:
        return ""


def page_header(pdf_path, pno):
    try:
        return json.loads(page_file(pdf_path, pno, "json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def transcribed_pages(pdf_path):
    """[(pno, header, markdown)] for the requested pages that are done."""
    manifest = load_manifest(pdf_path)
    out = []
    for pno in manifest["requested"]:
        if manifest["pages"].get(str(pno), {}).get("status") == "done":
            out.append((pno, page_header(pdf_path, pno), page_markdown(pdf_path, pno)))
    return out


def _checks(pdf_path, pno, markdown, page_type):
    """Tie-outs and (when Tesseract is installed) the figure cross-check."""
    tie = ocr_quality.tie_outs(markdown)
    figures_to_check, tesseract_used = [], False
    if ocr_quality.tesseract_path() and page_type != "blank":
        hi_res = render_page(pdf_path, pno, 300, filename=f"page-{pno:03d}.tess.png")
        text = ocr_quality.tesseract_text(hi_res)
        hi_res.unlink(missing_ok=True)
        if text is not None:
            tesseract_used = True
            figures_to_check = ocr_quality.figure_cross_check(markdown, text, tie["confirmed"])
    return tie, figures_to_check, tesseract_used


def recheck(pdf_path, workers=None):
    """Re-run the automatic checks on every transcribed page (no Claude calls),
    e.g. after the checks themselves were improved."""
    pdf_path = Path(pdf_path)
    manifest = load_manifest(pdf_path)
    done = [int(p) for p, r in manifest["pages"].items() if r.get("status") == "done"]

    def work(pno):
        header = page_header(pdf_path, pno)
        tie, figures_to_check, used = _checks(pdf_path, pno, page_markdown(pdf_path, pno),
                                              header.get("page_type"))
        header.update({"tied": tie["tied"], "untied": tie["untied"], "unchecked": tie["unchecked"],
                       "figures_to_check": figures_to_check, "tesseract": used})
        page_file(pdf_path, pno, "json").write_text(json.dumps(header, indent=1), encoding="utf-8")
        return pno, tie, figures_to_check, used

    with ThreadPoolExecutor(max_workers=workers or max(1, config.OCR_WORKERS)) as pool:
        results = list(pool.map(work, done))
    with _lock(pdf_path):
        manifest = load_manifest(pdf_path)
        for pno, tie, figures_to_check, used in results:
            manifest["pages"][str(pno)].update(untied=tie["untied"], figures_to_check=figures_to_check,
                                               tesseract=used)
        manifest["revision"] = manifest.get("revision", 0) + 1
        save_manifest(pdf_path, manifest)
    return status(pdf_path)

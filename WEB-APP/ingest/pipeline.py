"""The Report Ingester (INFO/SPLIT-FUNCTIONALITY-PLAN.md §6): PDF -> report package.

    ensure_package(pdf)  the package for this PDF, (re)built when it is missing
                         or stale; the only entry point the rest of the app uses
    open_report(pdf)     the same, opened (an rptpkg.Package)

Steps: fingerprint (SHA-256) -> format and index (notes_index: modern,
us-10k, legacy, or scanned via the OCR transcription) -> the clipped text of
every note, schedule and statement -> the text of every page -> the
statement tables -> write the package.

Everything the analyzer needs is in the package, so no PDF is read at
question time. A package is rebuilt when the ingester changes
(notes_index.INDEX_VERSION: bump it whenever what goes into a package
changes), when another format is chosen by hand, or, for scanned reports,
when a page is re-transcribed.
"""

import logging
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import config
import rptpkg
from ingest import clip, figures, links, narrative, notes_index, ocr_transcribe, profiles, quality, report_format, tables
from ingest.models import match as matcher
from ingest.models import registry
from ingest.pdf_utils import open_pdf, reuse_page_text
from rptpkg import store
from rptpkg.model import model_line, note_unit_id
from rptpkg.writer import update_meta, write_package

log = logging.getLogger(__name__)

INGESTER_VERSION = notes_index.INDEX_VERSION
NeedsTranscription = notes_index.NeedsTranscription
NewModelDocument = matcher.NewModelDocument

_locks = {}
_locks_guard = threading.Lock()


def _lock_for(pdf_path):
    with _locks_guard:
        return _locks.setdefault(str(Path(pdf_path).resolve()).lower(), threading.Lock())


# ---------------------------------------------------------------- is a package usable?

def _format_matches(meta, format_choice):
    """None keeps whatever format the package has (detected or set by hand);
    "auto" re-detects; a format name forces that format."""
    if format_choice is None:
        return True
    if format_choice == "auto":
        return meta.get("format_source") != "manual"
    return meta.get("format") == format_choice and meta.get("format_source") == "manual"


def _usable(package, pdf_path, format_choice):
    meta = package.meta
    if meta.get("format") == "transcribed":
        local = ocr_transcribe.status(pdf_path)
        if not local["done"]:
            # transcribed on another PC and imported: rebuilding it here would
            # mean paying for the transcription again, so it is kept as it is
            return format_choice in (None, "auto")
        if package.index.get("ocr_revision") != local["revision"]:
            return False             # a page was (re-)transcribed since
    profile = profiles.PROFILES.get(meta.get("profile"))
    return (meta.get("ingester_version") == INGESTER_VERSION and _format_matches(meta, format_choice)
            and profile is not None and meta.get("profile_version") == profile.version)


def _imported_scan(package, pdf_path):
    """A scanned report's package transcribed on another PC: it can't be re-matched here."""
    return package.meta.get("format") == "transcribed" and not ocr_transcribe.status(pdf_path)["done"]


def package_status(pdf_path):
    """For the Ingest screen: "up to date", "stale" (it will be rebuilt when
    opened) or "not ingested", with the package's facts."""
    try:
        found = store.find_package(pdf_path)
    except OSError:
        found = None
    if found is None:
        verdict = matcher.known_verdict(pdf_path)
        if verdict is not None:
            return {"status": "new model document", "new_model": verdict.to_json()}
        return {"status": "not ingested"}
    try:
        package = rptpkg.open_package(found)
    except rptpkg.PackageError as exc:
        return {"status": "stale", "reason": str(exc)}
    meta = package.meta
    usable = _usable(package, pdf_path, None) and (
        meta.get("registry_version") == registry.load().version or _imported_scan(package, pdf_path))
    return {
        "status": "up to date" if usable else "stale",
        "format": meta.get("format"), "format_label": meta.get("format_label"),
        "model": model_line(meta),
        "ingested_at": meta.get("ingested_at"), "ingest_seconds": meta.get("ingest_seconds"),
        "quality": (meta.get("quality") or {}).get("lines_text", []),
        "package": found.name,
    }


def _open_if_usable(path, pdf_path, format_choice):
    try:
        package = rptpkg.open_package(path)
    except rptpkg.PackageError as exc:
        log.info("Re-ingesting %s: %s", Path(pdf_path).name, exc)
        return None
    return package if _usable(package, pdf_path, format_choice) else None


# ---------------------------------------------------------------- building

def _package_content(index, pdf_path):
    """Everything the analyzer needs besides the index itself (run inside the
    profile's context: its switches apply to the clipping and the tables)."""
    transcribed = index.get("format") == "transcribed"
    doc = None if transcribed else open_pdf(pdf_path)
    try:
        unit_texts = {}
        for key, section in index["sections"].items():
            for note in section["notes"]:
                uid = note_unit_id(key, note)
                if (key, uid) in unit_texts:
                    continue
                extract = clip.extract_note(index, key, note["no"], doc)
                unit_texts[(key, uid)] = {"text": extract["text"], "pages": extract["pages"]}
        pages = {}
        with reuse_page_text():
            for pno in range(1, index["page_count"] + 1):
                printed, text = clip.page_record(index, doc, pno)
                pages[pno] = {"printed": printed, "text": text,
                              "text_source": "ocr" if transcribed else "pdf"}
                if transcribed:
                    pages[pno]["ocr"] = ocr_transcribe.page_header(pdf_path, pno)
        # the pages outside the notes and statements, read into sections and blocks (profile switch)
        narrative_content = narrative.build(index, doc, pages)
    finally:
        if doc is not None:
            doc.close()
    views = tables.report_views(index)
    lines, values, fiscal_md = figures.all_lines(index, views)
    content = {"unit_texts": unit_texts, "pages": pages, "statement_views": views,
               "lines": lines, "values": values, "fiscal_month_day": fiscal_md,
               "units": figures.report_units(index), "narrative": narrative_content}
    links.build(index, content)
    return content


def build_package(pdf_path, format_choice=None, result=None):
    """Match the PDF to a model document, then write its package with that
    model's profile. Raises NewModelDocument when no model matches,
    NeedsTranscription for a scanned report that isn't transcribed yet, and
    PdfError if it can't be read. result: a match already made."""
    pdf_path = Path(pdf_path)
    started = time.monotonic()
    result = result or matcher.match(pdf_path, format_choice)
    index = result.index
    with profiles.using(result.profile):
        content = _package_content(index, pdf_path)
    content["model"] = model_line(result.meta(0))
    sha = store.pdf_sha256(pdf_path)
    path = store.package_path(pdf_path, sha)
    fmt = index.get("format", "modern")
    format_label = report_format.FORMAT_LABELS.get(fmt, fmt)
    seconds = time.monotonic() - started
    report = quality.build(index, content, seconds)
    report["lines_text"] = quality.report_lines(report, format_label)   # shown in step 1 and the Ingest screen
    write_package(path, index, content, {
        **result.meta(registry.load().version),
        "ingester_version": INGESTER_VERSION,
        "pdf_sha256": sha,
        "format_label": format_label,
        "summary": notes_index.summary(index),
        "units": content["units"],
        "fiscal_month_day": content["fiscal_month_day"],
        "quality": report,
        "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ingest_seconds": round(seconds, 1),
    })
    for old in store.other_versions(pdf_path, path):
        old.unlink(missing_ok=True)
    # the JSON index cache of earlier versions of the app
    (Path(config.CACHE_DIR) / f"{pdf_path.stem}.notes-index.json").unlink(missing_ok=True)
    log.info("Ingested %s in %.1fs -> %s", pdf_path.name, time.monotonic() - started, path.name)
    return path


# ---------------------------------------------------------------- entry points

def ensure_package(pdf_path, format_choice=None):
    """Path of an up-to-date package for this PDF, building it if needed.

    format_choice: None keeps the package's format, "auto" re-detects, a
    format name ("modern", "us-10k", "legacy") forces it."""
    pdf_path = Path(pdf_path)
    with _lock_for(pdf_path):
        found = store.find_package(pdf_path)
        if found is not None:
            own = store.package_path(pdf_path)
            if found != own:
                # the same PDF under another name: give this name its own copy
                own.write_bytes(found.read_bytes())
                found = own
            package = _open_if_usable(found, pdf_path, format_choice)
            if package is not None:
                _refresh_name(found, pdf_path)
                return _rematch_if_registry_changed(found, package, pdf_path, format_choice)
        return build_package(pdf_path, format_choice)


def _rematch_if_registry_changed(path, package, pdf_path, format_choice):
    """Q7: when models were added or changed since the package was built, the
    report is matched again. Same model and profile: only the meta changes."""
    reg = registry.load()
    meta = package.meta
    if meta.get("registry_version") == reg.version or _imported_scan(package, pdf_path):
        return path
    own = reg.by_sha(meta.get("pdf_sha256"))
    if own and own.id == meta.get("model") and own.profile == meta.get("profile"):
        update_meta(path, registry_version=reg.version)
        return path
    result = matcher.match(pdf_path, format_choice)          # may raise NewModelDocument
    if result.model.id == meta.get("model") and result.profile.id == meta.get("profile"):
        update_meta(path, registry_version=reg.version, model_score=round(result.score, 2),
                    model_checks=result.checks)
        return path
    return build_package(pdf_path, format_choice, result)


def _refresh_name(path, pdf_path):
    package = rptpkg.open_package(path)
    if package.meta.get("pdf_name") == pdf_path.name and package.index.get("pdf") == str(pdf_path):
        return
    facts = dict(package.meta["index"], pdf=str(pdf_path), pdf_name=pdf_path.name)
    update_meta(path, pdf_name=pdf_path.name, index=facts)


SQLITE_HEADER = b"SQLite format 3\x00"


def import_package(upload_path):
    """Add an exported package (from another PC) to the packages folder.
    It is used for any PDF with the same content, whatever its name."""
    upload_path = Path(upload_path)
    with open(upload_path, "rb") as handle:
        if handle.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise ValueError("This is not a report package (.rptpkg.db) file.")
    try:
        package = rptpkg.open_package(upload_path)
    except rptpkg.PackageError as exc:
        raise ValueError(f"This package can't be used: {exc}")
    sha = str(package.meta.get("pdf_sha256") or "")
    name = Path(str(package.meta.get("pdf_name") or "report.pdf")).name
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("This package doesn't say which PDF it belongs to.")
    stem = re.sub(r"[^\w.\- ]", "_", Path(name).stem) or "report"
    dest = store.packages_dir() / f"{stem}-{sha[:16]}{store.SUFFIX}"
    shutil.copyfile(upload_path, dest)
    log.info("Imported package %s (%s)", dest.name, name)
    return {"pdf_name": name, "package": dest.name, "pdf_sha256": sha,
            "format_label": package.meta.get("format_label")}


def open_report(pdf_path, format_choice=None):
    return rptpkg.open_package(ensure_package(pdf_path, format_choice))


def load_index(pdf_path, format_choice=None):
    """The report's index (as the analyzer sees it), ingesting if needed."""
    return open_report(pdf_path, format_choice).index


def is_ingested(pdf_path):
    """A package exists for this PDF (it may still be rebuilt when opened)."""
    try:
        return store.find_package(pdf_path) is not None
    except OSError:
        return False

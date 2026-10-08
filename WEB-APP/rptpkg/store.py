"""Where report packages live, and which package belongs to which PDF.

Packages are kept in the app cache (INFO/SPLIT-FUNCTIONALITY-PLAN.md, Q3):
<CACHE_DIR>/packages/<pdf stem>-<first 16 hex of the PDF's SHA-256>.rptpkg.db.
The hash in the name means a renamed or copied PDF still finds its package,
and a PDF whose content changed gets a new one.
"""

import hashlib
import re
import threading
from pathlib import Path

import config

SUFFIX = ".rptpkg.db"
_hashes = {}
_hash_lock = threading.Lock()


def packages_dir():
    folder = Path(config.CACHE_DIR) / "packages"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def pdf_sha256(pdf_path):
    """SHA-256 of the PDF, remembered while its size and mtime are unchanged."""
    pdf_path = Path(pdf_path)
    stat = pdf_path.stat()
    key = (str(pdf_path.resolve()), stat.st_size, stat.st_mtime)
    with _hash_lock:
        if key in _hashes:
            return _hashes[key]
    digest = hashlib.sha256()
    with open(pdf_path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    with _hash_lock:
        _hashes[key] = digest.hexdigest()
    return _hashes[key]


def package_path(pdf_path, sha=None):
    """Where this PDF's package is (or will be) written."""
    pdf_path = Path(pdf_path)
    sha = sha or pdf_sha256(pdf_path)
    return packages_dir() / f"{pdf_path.stem}-{sha[:16]}{SUFFIX}"


def find_package(pdf_path):
    """This PDF's package, also under another file name; None if not ingested."""
    sha = pdf_sha256(pdf_path)
    own = package_path(pdf_path, sha)
    if own.exists():
        return own
    return next(iter(sorted(packages_dir().glob(f"*-{sha[:16]}{SUFFIX}"))), None)


def other_versions(pdf_path, keep):
    """Older packages of a PDF with this name (its content has changed since)."""
    name_re = re.compile(re.escape(Path(pdf_path).stem) + r"-[0-9a-f]{16}" + re.escape(SUFFIX))
    return [p for p in packages_dir().glob(f"{Path(pdf_path).stem}-*{SUFFIX}")
            if p != keep and name_re.fullmatch(p.name)]

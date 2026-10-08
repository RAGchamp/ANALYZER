"""The model registry (ingest/models/registry.json): one entry per model
document in MODEL-DOCS (INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §9).

Each document is its own model, named by its file name
(TYPE-<n>-<COUNTRY>-<FORM>-<YEAR>-<Company>.pdf). An entry has the PDF's
SHA-256 (the PDFs stay outside git and outside the exe), the profile that
reads it (None while its rules are not written yet: "pending"), a
description, and the fingerprint.

`version` goes up with every change; packages remember it and are re-matched
when it changes. Edit the registry only through `python -m ingest.models`.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REGISTRY_FILE = Path(__file__).with_name("registry.json")
NAME_RE = re.compile(r"^TYPE-(\d+)-([A-Z]+)-(.+?)-((?:19|20)\d{2})-(.+)$")


@dataclass
class Model:
    id: str                              # the file name without ".pdf"
    file: str
    sha256: str
    profile: str | None                  # None: pending (no extraction rules yet)
    description: str = ""
    fingerprint: dict = field(default_factory=dict)

    @property
    def pending(self):
        return self.profile is None

    @property
    def type_no(self):
        match = NAME_RE.match(self.id)
        return int(match.group(1)) if match else 0

    @property
    def short(self):
        """"TYPE-3 USA 10-K 2025 Chubb"."""
        match = NAME_RE.match(self.id)
        if not match:
            return self.id
        n, country, form, year, company = match.groups()
        return f"TYPE-{n} {country} {form.replace('-', ' ') if form.startswith('Ann') else form} {year} {company}"

    def to_json(self):
        return {"id": self.id, "file": self.file, "sha256": self.sha256, "profile": self.profile,
                "description": self.description, "fingerprint": self.fingerprint}


@dataclass
class Registry:
    version: int
    fingerprint_version: int
    models: list

    def by_sha(self, sha):
        return next((m for m in self.models if m.sha256 == sha), None)

    def by_id(self, model_id):
        return next((m for m in self.models if m.id == model_id or m.id.startswith(model_id + "-")), None)

    def supported(self):
        return [m for m in self.models if not m.pending]


_cache = {"mtime": None, "registry": None}


def load(path=REGISTRY_FILE):
    """The registry (re-read when the file changes)."""
    path = Path(path)
    if not path.exists():
        return Registry(version=0, fingerprint_version=0, models=[])
    mtime = path.stat().st_mtime
    if path == REGISTRY_FILE and _cache["mtime"] == mtime:
        return _cache["registry"]
    data = json.loads(path.read_text(encoding="utf-8"))
    registry = Registry(version=data["version"], fingerprint_version=data["fingerprint_version"],
                        models=[Model(**m) for m in data["models"]])
    if path == REGISTRY_FILE:
        _cache.update(mtime=mtime, registry=registry)
    return registry


def save(registry, path=REGISTRY_FILE):
    registry.models.sort(key=lambda m: (m.type_no, m.id))
    data = {"version": registry.version, "fingerprint_version": registry.fingerprint_version,
            "models": [m.to_json() for m in registry.models]}
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

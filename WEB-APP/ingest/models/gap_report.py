"""The gap report: what a developer needs to add a new format
(INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §6).

Written for every "new model document" to <CACHE_DIR>/gap-reports/, shown in
the app, and copied to MODEL-DOCS/_candidates/ when the report is proposed as
a model document. Deterministic; the optional "Describe with Claude" section
is appended by describe() (Q9).
"""

import re
from datetime import datetime
from pathlib import Path

import config
from ingest.models import registry
from ingest.models.fingerprint import NOTES_HEADER_RE, STATEMENT_TITLE_RE
from ingest.pdf_utils import open_pdf
from rptpkg import store

MAX_EVIDENCE_PAGES = 25
EVIDENCE_RE = re.compile(r"\bITEM\s+(?:8|18)\b|\bFinancial\s+Statements\b", re.I)

ROUTINE = """## Adding this format (MODEL-DOCS/README.md)

1. **Model document:** copy the PDF into `MODEL-DOCS\\` as `TYPE-<next>-<COUNTRY>-<FORM>-<YEAR>-<Company>.pdf`
   ("Propose as a model document" puts it in `MODEL-DOCS\\_candidates\\` with this report).
2. **Profile:** in `WEB-APP\\ingest\\profiles\\`, reuse a profile with different settings, `derive()` one,
   or add a new rule behind a new switch - never an `if` in another format's code path.
3. **Golden test:** notes (count, a known note's pages), statements (pages), a figure with its period;
   two or three hand-checked narrative pages in `tests\\fixtures\\narrative_gold\\gold.json`.
4. **Register:** `python -m ingest.models register "MODEL-DOCS\\TYPE-<n>-….pdf" --profile <id> --description "…"`
5. **Verify:** `python -m ingest.models verify` and `python -m pytest` (every model's baseline unchanged).
6. **Ship:** rebuild the exe.
"""


def _folder():
    folder = Path(config.CACHE_DIR) / "gap-reports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def path_for(pdf_path):
    pdf_path = Path(pdf_path)
    return _folder() / f"{pdf_path.stem}-{store.pdf_sha256(pdf_path)[:16]}.md"


def _cell(value):
    if isinstance(value, list):
        return ", ".join(value) or "—"
    if isinstance(value, dict):
        return ", ".join(f"{k} {v}" for k, v in value.items() if v) or "—"
    return "—" if value is None else str(value)


def _fingerprint_table(fp, nearest):
    models = [registry.load().by_id(n["model"]) for n in nearest]
    models = [m for m in models if m]
    head = "| Feature | This report | " + " | ".join(m.short for m in models) + " |"
    rows = [head, "|" + "---|" * (2 + len(models))]
    for group in ("physical", "typography", "document", "era", "structure"):
        for key, value in fp[group].items():
            others = [m.fingerprint.get(group, {}).get(key) for m in models]
            mark = " **≠**" if any(o != value for o in others) else ""
            rows.append(f"| {group}.{key} | {_cell(value)}{mark} | " + " | ".join(_cell(o) for o in others) + " |")
    return "\n".join(rows)


def _evidence(pdf_path):
    """Pages where the notes and statements probably are, with their first lines."""
    doc = open_pdf(pdf_path)
    out = []
    try:
        for i, page in enumerate(doc):
            text = page.get_text()
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            first = lines[:6]
            reasons = []
            if any(NOTES_HEADER_RE.search(ln) for ln in lines[:12]):
                reasons.append("notes wording")
            if any(STATEMENT_TITLE_RE.match(ln) for ln in lines[:12]):
                reasons.append("statement title")
            if EVIDENCE_RE.search("\n".join(lines[:12])):
                reasons.append("financial statements / Item 8 or 18")
            if not reasons:
                continue
            fonts = sorted({f"{s['font']} {s['size']:.1f}{' bold' if s['flags'] & 16 else ''}"
                            for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])[:2]
                            for s in ln["spans"][:1]})[:4]
            out.append(f"- **PDF p.{i + 1}** ({', '.join(reasons)}): "
                       + " / ".join(ln[:70] for ln in first) + f"  \n  fonts: {', '.join(fonts)}")
            if len(out) >= MAX_EVIDENCE_PAGES:
                break
    finally:
        doc.close()
    return "\n".join(out) or "- (no page names notes, statements or Item 8/18 in its first lines)"


def _narrative_clues(pdf_path):
    """Which section sources the new format offers for its pages outside notes and statements
    (the `narrative_sections` switch of its profile, INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md)."""
    from ingest.narrative import geometry, sections        # only needed when a gap report is written

    doc = open_pdf(pdf_path)
    try:
        toc = doc.get_toc(simple=True)
        geos = {n + 1: geometry.capture(doc[n]) for n in range(doc.page_count)}
    finally:
        doc.close()
    lines = []
    marks = sections.bookmark_entries(toc)
    lines.append(f"- **bookmarks**: {len(toc)} in the PDF outline"
                 + (f"; {len(marks)} usable as sections, e.g. " + ", ".join(e["title"] for e in marks[:4])
                    if marks else ""))
    contents = next(((p, sections.parse_contents(geos[p])) for p in range(1, min(len(geos), 15) + 1)
                     if sections.parse_contents(geos[p])), None)
    lines.append(f"- **contents**: PDF p.{contents[0]}, {len(contents[1])} entries, e.g. "
                 + ", ".join(f"{e['title']} ({e['printed']})" for e in contents[1][:4])
                 if contents else "- **contents**: no contents page found in the first 15 pages")
    items = sections.item_headings(geos, set())
    lines.append(f"- **form_items**: {len(items)} bold `Item N.` headings"
                 + (", e.g. " + ", ".join(t for _, _, t in items[:3]) if items else ""))
    headers = {p: [s["t"] for s in g["segments"] if s["y1"] <= 0.12 * g["height"]] for p, g in geos.items()}
    runs = sections.header_runs(headers)
    lines.append(f"- **running_headers**: {len(runs)} runs of a title at the top of the pages"
                 + (", e.g. " + ", ".join(f"{r['title']} (p.{r['first']}–{r['last']})" for r in runs[:3])
                    if runs else ""))
    heads = sections.heading_entries(geos, set())
    lines.append(f"- **headings** (last resort): {len(heads)} large bold headings, e.g. "
                 + ", ".join(e["title"] for e in heads[:3]))
    return "\n".join(lines)


def write(pdf_path, fp, nearest, pending=None):
    """Write the gap report; returns its path."""
    pdf_path = Path(pdf_path)
    reg = registry.load()
    parts = [f"# Gap report — {pdf_path.name}", "",
             f"- Written {datetime.now():%Y-%m-%d %H:%M} by the model matcher (registry version {reg.version}, "
             f"{len(reg.models)} model documents, {len(reg.supported())} with extraction rules)",
             f"- SHA-256 {store.pdf_sha256(pdf_path)}", ""]
    if pending:
        parts += ["## Verdict", "",
                  f"This PDF **is** model document **{pending.short}** (`{pending.id}`), but no extraction "
                  "rules (profile) are registered for it yet. Write its profile and register it with "
                  f"`python -m ingest.models register … --profile <id>`.", ""]
        fp = pending.fingerprint
    else:
        parts += ["## Verdict", "", "No model document matches this report: none of the nearest models' rules "
                  "passed their acceptance checks (or two formats were equally close). "
                  "The document data extraction logic needs an update for this format.", ""]
    if fp:
        parts += ["## Fingerprint compared with the nearest models", "", _fingerprint_table(fp, nearest), ""]
    parts += ["## What the nearest models' rules found", ""]
    for n in nearest:
        line = f"- **{n['short']}** — similarity {n['score']:.2f}"
        if n.get("pending"):
            line += "; its extraction rules are not written yet"
        elif n.get("conflict"):
            line += f"; not tried: {n['conflict']}"
        elif not n.get("trial"):
            line += "; not tried (too different)"
        else:
            line += f"; profile `{n['profile']}`: " + ("**passed**" if n["trial"]["passed"] else "failed")
            for check in n["trial"]["checks"]:
                line += f"\n  - {'ok' if check['ok'] else '**failed**'}: {check['name']} — {check['detail']}"
        parts.append(line)
    parts += ["", "## Evidence pages", "", _evidence(pdf_path), ""]
    parts += ["## Pages outside the notes and statements: section clues", "",
              "Turn on the sources that fit in the new profile's `narrative_sections` switch "
              "(and `narrative_designed_pages` for designed pages).", "", _narrative_clues(pdf_path), "", ROUTINE]
    path = path_for(pdf_path)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


DESCRIBE_PROMPT = """You are helping a developer add a new annual-report layout to a PDF extraction tool.
Read the page images {files} from one annual report. For each, and overall, describe precisely:
- where the primary financial statements are and how their titles look (size, bold, capitals, numbering);
- how the notes to the financial statements are headed (running header? note headings' style and numbering);
- how tables are laid out (columns, a Notes column, units, how negative numbers and nil are shown);
- anything unusual (landscape pages, two columns, statements in separate exhibits, scanned pages).
Answer in Markdown, concisely, with the page each observation comes from."""


def describe(pdf_path, pages, run=None):
    """Append Claude's description of a few evidence pages to the gap report
    (the optional "Describe with Claude" button, Q9; it costs usage)."""
    from claude_client import run_claude_with_image
    run = run or run_claude_with_image
    pdf_path = Path(pdf_path)
    folder = _folder() / f"{pdf_path.stem}-pages"
    folder.mkdir(exist_ok=True)
    doc = open_pdf(pdf_path)
    names = []
    try:
        for pno in pages[:4]:
            if 1 <= pno <= doc.page_count:
                name = f"page-{pno}.png"
                doc[pno - 1].get_pixmap(dpi=100).save(str(folder / name))
                names.append(name)
    finally:
        doc.close()
    reply, _ = run(DESCRIBE_PROMPT.format(files=", ".join(names)), folder, config.ANALYSIS_TIMEOUT)
    path = path_for(pdf_path)
    text = path.read_text(encoding="utf-8") if path.exists() else f"# Gap report — {pdf_path.name}\n"
    path.write_text(text + f"\n\n## Claude's description (pages {', '.join(map(str, pages[:4]))})\n\n{reply}\n",
                    encoding="utf-8")
    return path

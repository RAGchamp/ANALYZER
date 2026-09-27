"""Checks on Claude's page transcriptions (INFO/OCR-CAPABILITY-PLAN.md §6).

Nothing here changes a transcription; it only produces flags for the review
page and for the analysis prompt.

- tie_outs(): in each Markdown table column, a row that equals the sum of
  the figures directly above it (or the difference of the two above it)
  "ties", which confirms the total and its components. A "Total" or
  unlabelled row that doesn't tie is reported (a misread figure, or an
  error in the original) - unless a figure above it is unreadable ([?]),
  in which case it "can't be checked".
- figure_cross_check(): figures Claude read that neither local Tesseract
  OCR (allowing one character of difference) nor a tie-out confirms are
  flagged "check".
"""

import re
import shutil
import subprocess
from pathlib import Path

import config

FIGURE_RE = re.compile(r"\(?-?\$?\s?\d{1,3}(?:[,.]\d{3})+(?:\.\d{1,2})?\)?|\(?-?\$?\d{4,}(?:\.\d{1,2})?\)?")
# Only "Total ..." or unlabelled rows are expected to add up ("Net income" is
# often an ordinary line, e.g. in a surplus roll-forward).
TOTAL_LABEL_RE = re.compile(r"^(?:\*\*)?\s*total\b", re.I)
MAX_RUN = 20


# ------------------------------------------------------------------ figures

def digits(figure):
    return re.sub(r"\D", "", figure)


def figures(text):
    """Figures with at least 4 digits, as digit strings ("2,654,399" -> "2654399")."""
    return [d for d in (digits(m.group(0)) for m in FIGURE_RE.finditer(text)) if len(d) >= 4]


def parse_amount(cell):
    """"$ 46,002,417" -> 46002417; "(332,319)" -> -332319; "34.456" -> 34456
    (a dot where a comma belongs); a lone dash ("-", "–", "—") -> 0 (nil);
    "" or words -> None. [?] makes it unknown (None)."""
    text = cell.replace("*", "").strip()
    if re.fullmatch(r"\$?\s*[-–—]{1,2}", text):
        return 0.0
    if not text or "[?]" in text or not re.search(r"\d", text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    core = re.sub(r"[()$\s]", "", text)
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", core):   # dots as thousand separators
        core = core.replace(".", "")
    core = core.replace(",", "")
    try:
        value = float(core)
    except ValueError:
        return None
    return -value if negative else value


# ------------------------------------------------------------------ tables

def tables(markdown):
    """Markdown tables as lists of rows (lists of cell strings)."""
    out, current = [], []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue   # the |---| separator row
            current.append(cells)
        elif current:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out


def tie_outs(markdown):
    """{"tied", "untied", "unchecked", "confirmed"}: totals in the page's tables.

    tied / untied / unchecked items: {"label", "figure"}. confirmed: digit
    strings of every figure that took part in a tie (total and components)."""
    tied, untied, unchecked, confirmed = [], [], [], set()
    for rows in tables(markdown):
        width = max(len(r) for r in rows)
        cross = _cross_column_ties(rows)
        for col in range(1, width):
            run = []           # (cell, value) of figures above in this column since the last tie
            unknown = False    # an unreadable figure is among them
            for row in rows:
                if col >= len(row) or not row[col].strip():
                    continue
                cell = row[col]
                value = parse_amount(cell)
                if value is None:
                    unknown = unknown or "[?]" in cell
                    continue
                label = row[0].replace("*", "").strip()
                is_total = not label or bool(TOTAL_LABEL_RE.match(label))
                parts = None
                k_used = 0
                if len(run) >= 2:
                    for k in range(2, min(len(run), MAX_RUN) + 1):
                        if abs(sum(v for _, v in run[-k:]) - value) < 0.5:
                            parts, k_used = run[-k:], k
                            break
                    if parts is None and abs(run[-2][1] - run[-1][1] - value) < 0.5:
                        parts, k_used = run[-2:], 2
                item = {"label": label or "(total)", "figure": cell}
                if parts is None and (id(row), col) in cross:
                    parts, k_used = cross[(id(row), col)], 0
                if parts is not None:
                    tied.append(item)
                    confirmed.update(digits(c) for c, _ in parts + [(cell, value)])
                    # the subtotal replaces its parts; lines above them stay for a grand total
                    run, unknown = (run[:-k_used] if k_used else run) + [(cell, value)], False
                    continue
                if is_total and len(run) >= 2:
                    (unchecked if unknown else untied).append(item)
                run.append((cell, value))
    return {"tied": tied, "untied": untied, "unchecked": unchecked, "confirmed": sorted(confirmed)}


def _cross_column_ties(rows):
    """Totals printed in another column than their parts: "Total stockholders'
    equity" (outer column) = 36,782,441 - 577,170 (inner column, rows above).
    Returns {(id(row), col): [(cell, value), (cell, value)]}."""
    out, recent = {}, []     # recent: (cell, value) of the last figures, row by row
    for row in rows:
        label = row[0].replace("*", "").strip()
        figures_in_row = [(c, parse_amount(row[c]), row[c]) for c in range(1, len(row))]
        figures_in_row = [(c, v, cell) for c, v, cell in figures_in_row if v is not None]
        if (not label or TOTAL_LABEL_RE.match(label)) and len(recent) >= 2 and figures_in_row:
            (a_cell, a), (b_cell, b) = recent[-2], recent[-1]
            for col, value, cell in figures_in_row:
                if abs(a - b - value) < 0.5 or abs(a + b - value) < 0.5:
                    out[(id(row), col)] = [(a_cell, a), (b_cell, b)]
        recent.extend((cell, v) for _, v, cell in figures_in_row)
    return out


# ------------------------------------------------------------------ Tesseract

def tesseract_path():
    exe = Path(config.TESSERACT_EXE)
    if exe.is_file():
        return exe
    found = shutil.which("tesseract")
    return Path(found) if found else None


def tesseract_text(png_path, timeout=120):
    """Local OCR of one page image, or None when Tesseract isn't installed."""
    exe = tesseract_path()
    if not exe:
        return None
    try:
        result = subprocess.run([str(exe), str(png_path), "stdout", "--psm", "6"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _close(a, b):
    """Equal, or one substitution / insertion / deletion apart."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def figure_cross_check(claude_markdown, tesseract_output, confirmed=()):
    """Figures Claude read that neither Tesseract (±1 character) nor a tie-out confirms."""
    ocr = set(figures(tesseract_output))
    confirmed = set(confirmed)
    flagged = []
    for match in FIGURE_RE.finditer(claude_markdown):
        d = digits(match.group(0))
        if len(d) < 4 or d in confirmed or d in ocr or any(_close(d, o) for o in ocr):
            continue
        if re.fullmatch(r"(18|19|20)\d\d", match.group(0).strip()):
            continue   # a year, not an amount
        if match.group(0).strip() not in flagged:
            flagged.append(match.group(0).strip())
    return flagged

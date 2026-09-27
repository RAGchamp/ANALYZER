"""Central settings for the Annual Report Foot Notes Analyzer.

Anything that differs between machines or reports lives here, and the
path-like settings can be overridden with environment variables.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

REPORTS_DIR = Path(os.environ.get(
    "REPORTS_DIR", r"C:\Daya\RAGchamp\ANN-RPT-ANALYZER\Annual-reports"
))
CACHE_DIR = BASE_DIR / "cache"
THREADS_DIR = CACHE_DIR / "threads"
HISTORY_DIR = BASE_DIR / "Prompt-History"
# One styled, self-contained HTML report per question / follow-up.
ANALYSIS_HISTORY_DIR = BASE_DIR / "Analysis-history"
LOGO_FILE = BASE_DIR / "static" / "logo.svg"
LOG_DIR = BASE_DIR / "logs"
PROMPTS_DIR = BASE_DIR / "prompts"
INPUT_FILE = BASE_DIR / "claude-prompt-input.txt"
OUTPUT_FILE = BASE_DIR / "Claude-prompt-output.txt"

for _d in (CACHE_DIR, THREADS_DIR, HISTORY_DIR, ANALYSIS_HISTORY_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Claude Code CLI (same pattern as basic-chatbot) ---
CLAUDE_EXE = os.environ.get("CLAUDE_EXE", r"C:\Users\dayam\.local\bin\claude.exe")
# None = use whatever model Claude Code is configured with (no --model flag).
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or None
# `--tools ""` disables every built-in tool, so Claude only analyzes the
# text we send it (verified with `claude --help`, v2.1.282).
CLAUDE_EXTRA_FLAGS = ["--tools", "", "--no-session-persistence"]
# Set CLAUDE_VIA_POWERSHELL=1 to use basic-chatbot's original
# powershell -Command invocation instead of stdin.
CLAUDE_VIA_POWERSHELL = os.environ.get("CLAUDE_VIA_POWERSHELL") == "1"
# Effort for the analysis and follow-up calls. "low" measured 2026-09-26 on a
# real PPE analysis: 79s vs 139s at Claude Code's default, with the same
# figures and nearly all the same red flags. Set ANN_RPT_CLAUDE_EFFORT=default to use
# Claude Code's own default, or medium/high for deeper (slower) answers.
# (Not named CLAUDE_EFFORT: Claude Code sets that variable in its own terminals.)
CLAUDE_EFFORT = os.environ.get("ANN_RPT_CLAUDE_EFFORT", "low")
if CLAUDE_EFFORT.lower() == "default":
    CLAUDE_EFFORT = None
SELECTOR_TIMEOUT = 120
ANALYSIS_TIMEOUT = 600

# --- Scanned reports: page transcription by Claude (INFO/OCR-CAPABILITY-PLAN.md) ---
# Pages are rendered to greyscale PNG and read by `claude -p --tools Read`.
OCR_DPI = 150                 # enough for typewritten pages, incl. two-page spreads
OCR_RETRY_DPI = 200           # second try for pages with many unreadable characters
OCR_RETRY_UNREADABLE = 5      # [?] marks on a page that trigger the retry
OCR_WORKERS = int(os.environ.get("ANN_RPT_OCR_WORKERS", "4"))
OCR_TIMEOUT = 300             # seconds per page
OCR_EFFORT = os.environ.get("ANN_RPT_OCR_EFFORT", "low")
OCR_MODEL = os.environ.get("ANN_RPT_OCR_MODEL") or None     # None = Claude Code default
OCR_PROMPT_VERSION = 1        # bump when prompts/transcribe_page.txt changes meaningfully
# Starting estimates for "Transcribe", measured on BRK-1968 (39 pages: 5.8 min with
# 4 workers, $5.36): per page, including retries and the Tesseract check. After
# that the running average of the report's own pages is used.
OCR_EST_SECONDS_PER_PAGE = 35
OCR_EST_COST_PER_PAGE = 0.14
# Optional local Tesseract, used only to cross-check figures (never as the text).
TESSERACT_EXE = os.environ.get("TESSERACT_EXE", r"C:\Program Files\Tesseract-OCR\tesseract.exe")

# --- Analysis limits ---
MAX_NOTES = 5
# Old-format reports: schedules are short, and a good answer often needs a
# schedule + a note + a Directors' Report passage.
MAX_UNITS_LEGACY = 6
MAX_CONTEXT_CHARS = 150_000
MAX_FOLLOWUP_TURNS = 10

# --- Notes detection ---
# Running headers that mark a page as belonging to a notes section. Other
# companies may word these differently; add variants here.
NOTES_HEADERS = {
    "consolidated": [
        "Notes to Consolidated Financial Statements",
        "Notes forming part of the Consolidated Financial Statements",
        "Notes to the Consolidated Financial Statements",
    ],
    "standalone": [
        "Notes to Standalone Financial Statements",
        "Notes forming part of the Standalone Financial Statements",
        "Notes to the Standalone Financial Statements",
    ],
}
SECTION_LABELS = {"consolidated": "Consolidated", "standalone": "Standalone"}

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "5000"))

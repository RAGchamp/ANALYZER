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
SELECTOR_TIMEOUT = 120
ANALYSIS_TIMEOUT = 600

# --- Analysis limits ---
MAX_NOTES = 5
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

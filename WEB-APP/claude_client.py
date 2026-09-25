"""Run one fresh, stateless Claude Code CLI call (`claude -p`).

Same pattern as basic-chatbot/app.py: a new `claude -p` process per
request with a custom --system-prompt, using the local Claude Code login,
so no API key is needed.

One deliberate change: the prompt goes to the CLI on **stdin** instead of
inside a PowerShell -Command string. Extracted notes can be tens of
thousands of characters, which would exceed the Windows command-line limit
(~32K chars), and stdin also needs no quote escaping. Set
CLAUDE_VIA_POWERSHELL=1 to use basic-chatbot's original invocation.
"""

import logging
import re
import subprocess
import time

import config

log = logging.getLogger(__name__)

POWERSHELL_MAX_PROMPT_CHARS = 25_000
# Used by the PowerShell path: PowerShell 5.1 drops empty-string arguments,
# so `--tools ""` can't be passed through it. Block tools by name instead.
POWERSHELL_DISALLOWED_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Agent"


PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class ClaudeError(Exception):
    pass


def fill_prompt(name, **values):
    """Load prompts/<name> and replace its {{KEY}} placeholders in one pass,
    so values that contain placeholder-like text are never substituted again."""
    template = (config.PROMPTS_DIR / name).read_text(encoding="utf-8")
    return PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), template)


def system_prompt(name):
    return (config.PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def _base_args(system_prompt):
    args = [config.CLAUDE_EXE, "-p", "--system-prompt", system_prompt]
    if config.CLAUDE_MODEL:
        args += ["--model", config.CLAUDE_MODEL]
    return args


def _run_stdin(prompt, system_prompt, timeout):
    args = _base_args(system_prompt) + config.CLAUDE_EXTRA_FLAGS
    return subprocess.run(
        args,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        cwd=config.BASE_DIR,
    )


def _run_powershell(prompt, system_prompt, timeout):
    """basic-chatbot's original invocation. Single-quoted PowerShell strings
    never interpolate; the only escape needed is doubling single quotes."""
    if len(prompt) > POWERSHELL_MAX_PROMPT_CHARS:
        raise ClaudeError(
            f"Prompt is {len(prompt):,} chars - too long for the PowerShell "
            "command line. Unset CLAUDE_VIA_POWERSHELL to send it via stdin."
        )
    escaped_prompt = prompt.replace("'", "''")
    escaped_system_prompt = system_prompt.replace("'", "''")
    model = f" --model '{config.CLAUDE_MODEL}'" if config.CLAUDE_MODEL else ""
    ps_command = (
        f"& '{config.CLAUDE_EXE}' -p '{escaped_prompt}' "
        f"--system-prompt '{escaped_system_prompt}'{model} "
        f"--disallowedTools '{POWERSHELL_DISALLOWED_TOOLS}' --no-session-persistence"
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ps_command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        cwd=config.BASE_DIR,
    )


def run_claude(prompt, system_prompt, timeout):
    """Return Claude's reply text, or raise ClaudeError with a readable
    message (same error cases basic-chatbot handles)."""
    started = time.monotonic()
    runner = _run_powershell if config.CLAUDE_VIA_POWERSHELL else _run_stdin
    try:
        result = runner(prompt, system_prompt, timeout)
    except FileNotFoundError:
        raise ClaudeError(
            f"Claude Code CLI not found at {config.CLAUDE_EXE}. "
            "Set the CLAUDE_EXE environment variable to its path."
        )
    except subprocess.TimeoutExpired:
        raise ClaudeError(f"Claude Code took longer than {timeout}s to respond.")

    elapsed = time.monotonic() - started
    log.info("claude -p finished in %.1fs (prompt %d chars, exit %s)",
             elapsed, len(prompt), result.returncode)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        message = "Error running Claude Code CLI."
        if detail:
            message += f"\n\n{detail[:2000]}"
        raise ClaudeError(message)

    reply = (result.stdout or "").strip()
    if not reply:
        raise ClaudeError("Claude Code returned an empty response.")
    return reply

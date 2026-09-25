"""Prompt-History, reused from basic-chatbot.

Each analysis or follow-up is saved as numbered files that carry on from
whatever is already on disk:
  Claude-code-prompt-<n>-question.txt  metadata header + the question
  Claude-code-prompt-<n>-answer.txt    Claude's reply
  Claude-code-prompt-<n>-prompt.txt    the full prompt sent (with the
                                       extracted notes), so any past answer
                                       can be reproduced from its inputs
"""

import re

import config

HISTORY_SNO_RE = re.compile(r"^Claude-code-prompt-(\d+)-question\.txt$")
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
META_LINE_RE = re.compile(r"^([A-Za-z ]+): (.*)$")


def next_sno():
    """Next history sequence number, resuming from whatever is on disk."""
    existing = [
        int(match.group(1))
        for f in config.HISTORY_DIR.glob("Claude-code-prompt-*-question.txt")
        if (match := HISTORY_SNO_RE.match(f.name))
    ]
    return max(existing, default=0) + 1


def save_history(sno, question, answer, asked_at, meta, full_prompt):
    """meta: ordered dict of header fields (Report, Scope, Notes, ...)."""
    header = [f"Asked at: {asked_at.strftime(TIMESTAMP_FORMAT)}"]
    header += [f"{key}: {value}" for key, value in meta.items()]
    (config.HISTORY_DIR / f"Claude-code-prompt-{sno}-question.txt").write_text(
        "\n".join(header) + "\n\n" + question, encoding="utf-8")
    (config.HISTORY_DIR / f"Claude-code-prompt-{sno}-answer.txt").write_text(
        answer, encoding="utf-8")
    (config.HISTORY_DIR / f"Claude-code-prompt-{sno}-prompt.txt").write_text(
        full_prompt, encoding="utf-8")


def read_entry(sno):
    question_file = config.HISTORY_DIR / f"Claude-code-prompt-{sno}-question.txt"
    if not question_file.exists():
        return None
    raw = question_file.read_text(encoding="utf-8")
    head, _, question = raw.partition("\n\n")
    meta = {}
    for line in head.splitlines():
        if match := META_LINE_RE.match(line):
            meta[match.group(1)] = match.group(2)
    answer_file = config.HISTORY_DIR / f"Claude-code-prompt-{sno}-answer.txt"
    answer = answer_file.read_text(encoding="utf-8") if answer_file.exists() else ""
    return {"sno": sno, "meta": meta, "question": question, "answer": answer}


def list_entries(limit=100):
    snos = sorted(
        (int(m.group(1)) for f in config.HISTORY_DIR.glob("Claude-code-prompt-*-question.txt")
         if (m := HISTORY_SNO_RE.match(f.name))),
        reverse=True,
    )[:limit]
    entries = []
    for sno in snos:
        entry = read_entry(sno)
        if entry:
            entries.append({
                "sno": sno,
                "asked_at": entry["meta"].get("Asked at", ""),
                "report": entry["meta"].get("Report", ""),
                "kind": entry["meta"].get("Kind", ""),
                "notes": entry["meta"].get("Notes", ""),
                "question": entry["question"][:140],
            })
    return entries

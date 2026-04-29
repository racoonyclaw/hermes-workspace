"""log — Append entries to the wiki ingest log (reports/ingest-log.md).

The log is chronological and append-only. Each entry starts with a
parseable prefix: ## [YYYY-MM-DD] ingest | Title

Format is designed to be greppable:
    grep "^## \[" reports/ingest-log.md | tail -5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Log entry types
# ---------------------------------------------------------------------------

LOG_HEADER = "## [{date}] {action} | {title}"


@dataclass
class LogEntry:
    """A single entry in the ingest log."""

    action: str  # "ingest", "query", "lint", "compile"
    title: str
    date: Optional[str] = None  # ISO date, defaults to now
    source_url: Optional[str] = None
    source_id: Optional[str] = None
    touched_pages: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class LogResult:
    """Result of a log append operation."""

    written: bool
    path: str
    entry_header: str
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Entry formatting
# ---------------------------------------------------------------------------

def _format_entry(entry: LogEntry) -> str:
    """Format a LogEntry as a markdown section."""
    if entry.date is None:
        entry.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        LOG_HEADER.format(
            date=entry.date,
            action=entry.action,
            title=entry.title,
        ),
        "",
    ]

    if entry.source_url:
        lines.append(f"- Source URL: {entry.source_url}")

    if entry.source_id:
        lines.append(f"- ID: {entry.source_id}")

    if entry.touched_pages:
        page_links = ", ".join(f"[[{p}]]" for p in entry.touched_pages)
        lines.append(f"- Touched pages: {page_links}")

    if entry.notes:
        lines.append(f"- {entry.notes}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main: append entry
# ---------------------------------------------------------------------------

def append_log_entry(
    vault_path: Path,
    entry: LogEntry,
    dry_run: bool = False,
) -> LogResult:
    """Append a chronologically sorted entry to the ingest log.

    The log lives at reports/ingest-log.md. If the file doesn't exist,
    it's created with a header. Entries are prepended (newest first).
    """
    log_path = vault_path / "reports" / "ingest-log.md"

    # Build the new entry text
    new_entry = _format_entry(entry)

    # Read existing log
    if log_path.exists():
        try:
            existing = log_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return LogResult(
                written=False,
                path=str(log_path),
                entry_header=new_entry.splitlines()[0] if new_entry else "",
                error=str(e),
            )
    else:
        existing = (
            "# Ingest Log\n\n"
            "Chronological record of wiki operations.\n"
            "Entries sorted newest first.\n"
            'Use `grep "^## \\[" | tail -5` for recent activity.\n\n'
        )

    if dry_run:
        return LogResult(
            written=False,
            path=str(log_path),
            entry_header=new_entry.splitlines()[0] if new_entry else "",
        )

    # Prepend the new entry (newest first)
    # Find the first log entry header (## [...] ) to insert before it,
    # or append after the preamble if no entries exist yet
    import re
    first_entry_match = re.search(r"^## \[", existing, re.MULTILINE)

    if first_entry_match:
        # Insert before the first existing entry
        body = (
            existing[:first_entry_match.start()]
            + new_entry
            + "\n\n"
            + existing[first_entry_match.start():]
        )
    else:
        # No existing entries — append after preamble
        body = existing.rstrip() + "\n\n" + new_entry + "\n"

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(body, encoding="utf-8")
    except OSError as e:
        return LogResult(
            written=False,
            path=str(log_path),
            entry_header=new_entry.splitlines()[0] if new_entry else "",
            error=str(e),
        )

    return LogResult(
        written=True,
        path=str(log_path),
        entry_header=new_entry.splitlines()[0] if new_entry else "",
    )

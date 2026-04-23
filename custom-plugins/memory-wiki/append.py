"""append — Append content to an existing wiki page.

Supports two modes:
  - heading mode: insert new content after the last occurrence of a heading
  - end mode: append content at the very end of the page
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .markdown_utils import parse_frontmatter, render_frontmatter
from .query import get_wiki_page


def append_to_page(
    vault_path: Path,
    lookup: str,
    content: str,
    heading: Optional[str] = None,
    dry_run: bool = False,
) -> AppendResult:
    """Append *content* to a wiki page identified by *lookup*.

    If *heading* is provided (e.g. "## Spark Plugs"), the content is inserted
    after the last occurrence of that heading. If the heading already has content
    immediately following it, the new content replaces up to the next heading
    (prevents duplicates).

    If *heading* is None, content is appended at the end of the page.

    *content* is the raw markdown body to insert (no leading/trailing newlines needed).

    Returns an AppendResult with the path written (or would be written) and any errors.
    """
    # Find the page
    page_meta = get_wiki_page(vault_path, lookup)
    if page_meta is None:
        return AppendResult(success=False, error=f"Page not found: {lookup}")

    rel_path = page_meta["path"]
    abs_path = vault_path / rel_path

    if not abs_path.exists():
        return AppendResult(success=False, error=f"File not found on disk: {abs_path}")

    try:
        raw = abs_path.read_text(encoding="utf-8")
    except OSError as e:
        return AppendResult(success=False, error=f"Could not read file: {e}")

    frontmatter, body = parse_frontmatter(raw)

    if heading:
        new_body = _insert_after_heading(body, heading, content)
    else:
        new_body = body.rstrip() + "\n\n" + content

    if dry_run:
        return AppendResult(
            success=True,
            would_write=True,
            path=rel_path,
            preview=new_body[-500:] if len(new_body) > 500 else new_body,
        )

    new_raw = render_frontmatter(frontmatter, new_body)

    try:
        abs_path.write_text(new_raw, encoding="utf-8")
    except OSError as e:
        return AppendResult(success=False, error=f"Could not write file: {e}")

    return AppendResult(success=True, path=rel_path)


def _insert_after_heading(body: str, heading: str, content: str) -> str:
    """Insert new content after the last occurrence of *heading* in body.

    If the heading exists, content is inserted after it, before the next ## heading
    (or at end of page). If the heading does not exist, content is appended at the end.
    """
    # Normalize: strip leading whitespace from heading pattern
    heading_stripped = heading.strip()
    # Escape special regex chars in heading (## is already literal)
    escaped = re.escape(heading_stripped)

    # Find all occurrences of the heading (must be at start of line or after newline)
    pattern = re.compile(rf"(?<=\n)(#+\s+{escaped})\b", re.MULTILINE)
    matches = list(pattern.finditer(body))

    if not matches:
        # Heading not found — append at end
        return body.rstrip() + "\n\n" + heading + "\n\n" + content

    last_match = matches[-1]
    insert_start = last_match.end()

    # Find the next heading after our target heading
    next_heading_match = re.search(r"\n## ", body[insert_start:])
    if next_heading_match:
        # Insert before the next heading
        cut = insert_start + next_heading_match.start()
        return body[:insert_start] + "\n\n" + content + "\n" + body[cut:]
    else:
        # No next heading — append to end of body
        return body[:insert_start] + "\n\n" + content


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field


@dataclass
class AppendResult:
    success: bool
    path: Optional[str] = None
    error: Optional[str] = None
    would_write: bool = False
    preview: str = ""

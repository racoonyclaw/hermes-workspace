"""markdown_utils — Wiki markdown parsing, rendering, and managed block utilities.

Ported from OpenClaw's memory-wiki extension (markdown.ts + memory-host-markdown.ts).
"""

from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Managed block markers (match OpenClaw convention)
# ---------------------------------------------------------------------------

GENERATED_START = "<!-- openclaw:wiki:generated:start -->"
GENERATED_END = "<!-- openclaw:wiki:generated:end -->"
HUMAN_START = "<!-- openclaw:human:start -->"
HUMAN_END = "<!-- openclaw:human:end -->"
RELATED_START = "<!-- openclaw:wiki:related:start -->"
RELATED_END = "<!-- openclaw:wiki:related:end -->"
LINT_START = "<!-- openclaw:wiki:lint:start -->"
LINT_END = "<!-- openclaw:wiki:lint:end -->"


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_FRONTMATTER_PATTERN = re.compile(r"^---\n([\s\S]*?)\n---\n?")
_OBSIDIAN_LINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Public aliases for external consumers (e.g. ingest.py)
OBSIDIAN_LINK_PATTERN = _OBSIDIAN_LINK_PATTERN
MARKDOWN_LINK_PATTERN = _MARKDOWN_LINK_PATTERN
_RELATED_BLOCK_PATTERN = re.compile(
    r"<!-- openclaw:wiki:related:start -->[\s\S]*?<!-- openclaw:wiki:related:end -->",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Frontmatter parsing / rendering
# ---------------------------------------------------------------------------

def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split wiki markdown into (frontmatter_dict, body).

    If no frontmatter is found, returns ({}, raw).
    """
    match = _FRONTMATTER_PATTERN.match(raw)
    if not match:
        return {}, raw

    import yaml

    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        frontmatter = {}

    if not isinstance(frontmatter, dict):
        frontmatter = {}

    body = raw[match.end() :]
    return frontmatter, body


def render_frontmatter(frontmatter: dict, body: str) -> str:
    """Render frontmatter + body into wiki markdown.

    Uses yaml.safe_dump for stable, clean output.
    """
    import yaml

    fm = yaml.safe_dump(frontmatter, default_flow_style=False, sort_keys=False)
    return f"---\n{fm.rstrip()}\n---\n\n{body.lstrip()}"


# ---------------------------------------------------------------------------
# Managed block replacement
# ---------------------------------------------------------------------------

def replace_managed_block(
    original: str,
    heading: str,
    start_marker: str,
    end_marker: str,
    body: str,
) -> str:
    """Replace or insert a managed block inside *original*.

    The block is identified by *start_marker* and *end_marker*. If both exist,
    the content between them is replaced. If only start exists, the block is
    closed at the end of the managed region. If neither exists, the block is
    appended after the last ``## heading`` in the document.

    *heading* is the markdown heading that precedes the block (e.g. ``## Notes``).
    """
    start_idx = original.find(start_marker)
    end_idx = original.find(end_marker)

    if start_idx != -1 and end_idx != -1:
        # Replace existing block
        return (
            original[:start_idx]
            + start_marker
            + "\n"
            + body
            + "\n"
            + end_marker
            + original[end_idx + len(end_marker) :]
        )

    if start_idx != -1:
        # Has start, no end — close at next heading or end of content
        rest = original[start_idx + len(start_marker) :]
        next_heading = re.search(r"\n## ", rest)
        if next_heading:
            cut = start_idx + len(start_marker) + next_heading.start()
            return original[:start_idx] + start_marker + "\n" + body + "\n" + original[cut:]
        return original[:start_idx] + start_marker + "\n" + body + "\n" + end_marker + rest

    if end_idx != -1:
        # Has end, no start — insert before end marker
        return original[:end_idx] + start_marker + "\n" + body + "\n" + end_marker + original[end_idx + len(end_marker) :]

    # Neither marker exists — find last heading and insert after it
    heading_match = re.search(r"(^## .+)$", original, re.MULTILINE)
    if heading_match:
        insert_pos = heading_match.end()
        return (
            original[:insert_pos]
            + "\n\n"
            + heading
            + "\n"
            + start_marker
            + "\n"
            + body
            + "\n"
            + end_marker
            + original[insert_pos:]
        )

    # Fallback: append at end
    return original.rstrip() + "\n\n" + start_marker + "\n" + body + "\n" + end_marker + "\n"


# ---------------------------------------------------------------------------
# Wikilink extraction
# ---------------------------------------------------------------------------

def extract_wikilinks(markdown: str) -> list[str]:
    """Extract all [[wikilink]] targets and markdown link targets.

    Returns a list of link target strings (without the surrounding brackets/
    parens). Excludes heading anchors (#), external URLs (scheme: prefix),
    and empty targets.
    """
    # Remove related blocks to avoid extracting links from managed sections
    searchable = _RELATED_BLOCK_PATTERN.sub("", markdown)

    links: list[str] = []

    # Obsidian wikilinks [[target]] or [[target|alias]]
    for match in _OBSIDIAN_LINK_PATTERN.finditer(searchable):
        target = match.group(1)
        if target:
            links.append(target.strip())

    # Markdown links [text](target)
    for match in _MARKDOWN_LINK_PATTERN.finditer(searchable):
        raw_target = match.group(1)
        if not raw_target or raw_target.startswith("#") or re.match(r"^[a-z]+:", raw_target, re.I):
            continue
        # Strip heading anchors and query strings
        clean = raw_target.split("#")[0].split("?")[0].replace("\\", "/").strip()
        if clean:
            links.append(clean)

    return links


# ---------------------------------------------------------------------------
# Page kind inference
# ---------------------------------------------------------------------------

_PAGE_KINDS = ("entity", "concept", "source", "synthesis", "report")


def infer_wiki_page_kind(relative_path: str) -> Optional[str]:
    """Infer page kind from its directory path.

    Returns one of: entity, concept, source, synthesis, report, or None.
    """
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("entities/"):
        return "entity"
    if normalized.startswith("concepts/"):
        return "concept"
    if normalized.startswith("sources/"):
        return "source"
    if normalized.startswith("syntheses/"):
        return "synthesis"
    if normalized.startswith("reports/"):
        return "report"
    return None


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------

def extract_title_from_markdown(body: str) -> Optional[str]:
    """Extract page title from first ``# heading`` in body."""
    match = re.match(r"^#\s+(.+?)\s*$", body.strip(), re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_string(value):
    """Strip whitespace, return None if empty. Handles non-string types (YAML-parsed dates, etc.)."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def normalize_string_list(value: Optional[list]) -> list[str]:
    """Normalize a list of strings: strip, filter empty."""
    if not isinstance(value, list):
        return []
    return [s.strip() for s in value if isinstance(s, str) and s.strip()]


def slugify(text: str) -> str:
    """Slugify a string for use in filenames/ids.

    Lowercase, spaces to hyphens, remove non-alphanumeric (except hyphens).
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[_\s]+", "-", text)
    return text.strip("-")

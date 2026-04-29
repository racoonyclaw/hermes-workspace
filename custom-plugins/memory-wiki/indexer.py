"""indexer — Regenerate the wiki index (index.md) from vault contents.

Produces a content-oriented catalog: each page listed with a wikilink,
one-line summary, and optional metadata, organized by category.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .markdown_utils import (
    INDEX_END,
    INDEX_START,
    parse_frontmatter,
    replace_managed_block,
)
from .query import read_wiki_pages


# ---------------------------------------------------------------------------
# Index page structure
# ---------------------------------------------------------------------------

INDEX_KINDS = ("sources", "entities", "concepts", "syntheses", "reports")

KIND_LABELS = {
    "sources": "Sources",
    "entities": "Entities",
    "concepts": "Concepts",
    "syntheses": "Syntheses",
    "reports": "Reports",
}


@dataclass
class IndexResult:
    """Result of an index regeneration."""

    written: bool
    path: str
    pages_cataloged: int
    categories: int
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# One-line summary extraction
# ---------------------------------------------------------------------------

def _extract_summary(body: str, max_chars: int = 120) -> str:
    """Extract a one-line summary from markdown body text.

    Tries: first non-heading, non-empty paragraph after frontmatter.
    Falls back to first sentence.
    """
    lines = body.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Remove wikilinks for cleaner display
        import re
        cleaned = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", stripped)
        if len(cleaned) > max_chars:
            # Truncate at word boundary
            cleaned = cleaned[:max_chars].rsplit(" ", 1)[0] + "…"
        return cleaned
    return "(no summary)"


# ---------------------------------------------------------------------------
# Index block builder
# ---------------------------------------------------------------------------

def _build_index_block(vault_path: Path) -> str:
    """Build the managed block content for index.md."""
    pages = read_wiki_pages(vault_path)

    # Group by kind
    by_kind: dict[str, list] = {k: [] for k in INDEX_KINDS}
    for page in pages:
        kind = page.kind
        if kind not in by_kind:
            by_kind[kind] = []
        by_kind[kind].append(page)

    # Sort each group by title
    for kind in by_kind:
        by_kind[kind].sort(key=lambda p: p.title.lower())

    # Build block
    lines = [
        f"- Render mode: `obsidian`",
        f"- Total pages: {len(pages)}",
    ]

    # Count claims and sources
    total_claims = sum(len(p.claims) for p in pages)
    lines.append(f"- Claims: {total_claims}")

    for kind in INDEX_KINDS:
        group = by_kind[kind]
        label = KIND_LABELS[kind]
        lines.append(f"- {label}: {len(group)}")

    lines.append("")

    for kind in INDEX_KINDS:
        group = by_kind[kind]
        label = KIND_LABELS[kind]
        lines.append(f"### {label}")

        if not group:
            lines.append(f"- No {kind.lower()} yet.")
            lines.append("")
            continue

        for page in group:
            # Build page link
            link = page.relative_path.replace(".md", "")
            display = page.title

            # Read file body for summary extraction
            summary = ""
            page_path = vault_path / page.relative_path
            if page_path.exists():
                try:
                    raw = page_path.read_text(encoding="utf-8")
                    fm, body = parse_frontmatter(raw)
                    summary = _extract_summary(body)
                except Exception:
                    pass

            # Build metadata suffix
            meta_parts = []
            if page.updated_at:
                meta_parts.append(page.updated_at[:10])  # YYYY-MM-DD
            if page.claims:
                meta_parts.append(f"{len(page.claims)} claims")

            suffix = f" — {', '.join(meta_parts)}" if meta_parts else ""

            if summary:
                lines.append(f"- [[{link}|{display}]]{suffix}")
                lines.append(f"  {summary}")
            else:
                lines.append(f"- [[{link}|{display}]]{suffix}")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main: regenerate index
# ---------------------------------------------------------------------------

def regenerate_index(vault_path: Path, dry_run: bool = False) -> IndexResult:
    """Regenerate the managed block in index.md from vault contents.

    Reads all wiki pages, groups by kind, and writes an organized catalog
    with wikilinks, one-line summaries, and metadata.

    If *dry_run* is True, nothing is written but the result describes what
    would be written.
    """
    index_path = vault_path / "index.md"

    # Ensure index.md exists
    if not index_path.exists():
        if dry_run:
            return IndexResult(
                written=False,
                path="index.md",
                pages_cataloged=0,
                categories=0,
                error="index.md does not exist (would create)",
            )
        # Create a minimal index.md
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            "# Wiki Index\n\n"
            "## Generated\n"
            f"{INDEX_START}\n{INDEX_END}\n",
            encoding="utf-8",
        )

    try:
        original = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return IndexResult(
            written=False,
            path="index.md",
            pages_cataloged=0,
            categories=0,
            error=str(e),
        )

    # Build new block content
    new_block = _build_index_block(vault_path)

    # Replace managed block
    updated = replace_managed_block(
        original=original,
        heading="## Generated",
        start_marker=INDEX_START,
        end_marker=INDEX_END,
        body=new_block,
    )

    if dry_run:
        pages = read_wiki_pages(vault_path)
        by_kind = len(set(p.kind for p in pages))
        return IndexResult(
            written=False,
            path="index.md",
            pages_cataloged=len(pages),
            categories=by_kind,
        )

    try:
        index_path.write_text(updated, encoding="utf-8")
    except OSError as e:
        return IndexResult(
            written=False,
            path="index.md",
            pages_cataloged=0,
            categories=0,
            error=str(e),
        )

    pages = read_wiki_pages(vault_path)
    by_kind = len(set(p.kind for p in pages))

    return IndexResult(
        written=True,
        path="index.md",
        pages_cataloged=len(pages),
        categories=by_kind,
    )

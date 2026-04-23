"""query — Wiki page reading, search, and WikiPageSummary construction.

Ported from OpenClaw's memory-wiki extension (query.ts + markdown.ts to_page_summary).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .claim_health import WikiClaim, WikiClaimEvidence, WikiPageSummary
from .markdown_utils import (
    extract_title_from_markdown,
    extract_wikilinks,
    infer_wiki_page_kind,
    normalize_string,
    normalize_string_list,
    parse_frontmatter,
)


# ---------------------------------------------------------------------------
# Page reading
# ---------------------------------------------------------------------------

def read_wiki_pages(vault_path: Path) -> list[WikiPageSummary]:
    """Recursively read all wiki pages under *vault_path*.

    Only reads files under entities/, concepts/, sources/, syntheses/, reports/.
    Skips hidden files and directories (starting with .).
    """
    pages: list[WikiPageSummary] = []
    kinds = ("entities", "concepts", "sources", "syntheses", "reports")

    for kind in kinds:
        kind_dir = vault_path / kind
        if not kind_dir.is_dir():
            continue

        for md_file in _iter_wiki_files(kind_dir):
            page = _read_page_summary(md_file, vault_path)
            if page is not None:
                pages.append(page)

    return pages


def _iter_wiki_files(root: Path):
    """Yield all .md files under root, skipping hidden dirs/files."""
    try:
        for item in root.rglob("*.md"):
            # Skip hidden paths
            if any(part.startswith(".") for part in item.parts):
                continue
            yield item
    except OSError:
        pass


def _read_page_summary(file_path: Path, vault_root: Path) -> Optional[WikiPageSummary]:
    """Read a single wiki page and return its WikiPageSummary, or None if skipped."""
    try:
        raw = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        relative = file_path.relative_to(vault_root)
    except ValueError:
        # Not under vault_root — use absolute path segments
        relative = file_path

    relative_str = str(relative).replace("\\", "/")

    return to_page_summary(
        absolute_path=str(file_path),
        relative_path=relative_str,
        raw=raw,
    )


def to_page_summary(
    absolute_path: str,
    relative_path: str,
    raw: str,
) -> Optional[WikiPageSummary]:
    """Parse a wiki page's raw markdown into a WikiPageSummary.

    Returns None if the page kind cannot be inferred from the path.
    """
    kind = infer_wiki_page_kind(relative_path)
    if kind is None:
        return None

    frontmatter, body = parse_frontmatter(raw)

    # Title: frontmatter title > h1 heading > filename
    title: str = ""
    if isinstance(frontmatter.get("title"), str) and frontmatter["title"].strip():
        title = frontmatter["title"].strip()
    else:
        extracted = extract_title_from_markdown(body)
        title = extracted if extracted else Path(relative_path).stem

    # Parse claims from frontmatter
    claims = _parse_claims(frontmatter.get("claims"))

    # Parse contradictions and questions
    contradictions = normalize_string_list(frontmatter.get("contradictions"))
    questions = normalize_string_list(frontmatter.get("questions"))

    # Confidence
    confidence: Optional[float] = None
    if isinstance(frontmatter.get("confidence"), (int, float)):
        val = float(frontmatter["confidence"])
        if val <= 1.0:  # tolerate 0-1 floats
            confidence = val
        elif val > 1:  # also tolerate 0-100 percentages
            confidence = val / 100

    return WikiPageSummary(
        absolute_path=absolute_path,
        relative_path=relative_path.replace("\\", "/"),
        kind=kind,
        title=title,
        id=normalize_string(frontmatter.get("id")),
        page_type=normalize_string(frontmatter.get("pageType")),
        source_ids=normalize_string_list(frontmatter.get("sourceIds")),
        link_targets=extract_wikilinks(raw),
        claims=claims,
        contradictions=contradictions,
        questions=questions,
        confidence=confidence,
        source_type=normalize_string(frontmatter.get("sourceType")),
        provenance_mode=normalize_string(frontmatter.get("provenanceMode")),
        source_path=normalize_string(frontmatter.get("sourcePath")),
        bridge_relative_path=normalize_string(frontmatter.get("bridgeRelativePath")),
        bridge_workspace_dir=normalize_string(frontmatter.get("bridgeWorkspaceDir")),
        unsafe_local_configured_path=normalize_string(frontmatter.get("unsafeLocalConfiguredPath")),
        unsafe_local_relative_path=normalize_string(frontmatter.get("unsafeLocalRelativePath")),
        updated_at=normalize_string(frontmatter.get("updatedAt")),
    )


def _parse_claims(value: Optional[list]) -> list[WikiClaim]:
    """Parse claims from the claims frontmatter list."""
    if not isinstance(value, list):
        return []

    claims: list[WikiClaim] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue

        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        claim = WikiClaim(
            id=normalize_string(entry.get("id")),
            text=text.strip(),
            status=normalize_string(entry.get("status")) or "supported",
            confidence=float(entry["confidence"]) if isinstance(entry.get("confidence"), (int, float)) else None,
            evidence=_parse_claim_evidence(entry.get("evidence")),
            updated_at=normalize_string(entry.get("updatedAt")),
        )
        claims.append(claim)

    return claims


def _parse_claim_evidence(value: Optional[list]) -> list[WikiClaimEvidence]:
    """Parse evidence list from a claim's evidence field."""
    if not isinstance(value, list):
        return []

    evidence: list[WikiClaimEvidence] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        e = WikiClaimEvidence(
            source_id=normalize_string(item.get("sourceId")),
            path=normalize_string(item.get("path")),
            lines=normalize_string(item.get("lines")),
            weight=float(item["weight"]) if isinstance(item.get("weight"), (int, float)) else None,
            note=normalize_string(item.get("note")),
            updated_at=normalize_string(item.get("updatedAt")),
        )
        # Only add if it has at least something
        if any(getattr(e, f) for f in ("source_id", "path", "lines", "note")):
            evidence.append(e)

    return evidence


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_wiki_pages(
    vault_path: Path,
    query: str,
    max_results: int = 10,
) -> list[dict]:
    """Search wiki pages by query string.

    Searches across titles, body text, and claim text.
    Returns up to *max_results* ranked results.
    """
    pages = read_wiki_pages(vault_path)
    query_lower = query.lower().strip()

    scored: list[tuple[int, dict]] = []
    for page in pages:
        score = 0
        snippet_parts: list[str] = []

        # Title match — highest weight
        if query_lower in page.title.lower():
            score += 10
            snippet_parts.append(page.title)

        # ID match
        if page.id and query_lower in page.id.lower():
            score += 8

        # Claim text match
        for claim in page.claims:
            if query_lower in claim.text.lower():
                score += 5
                if claim.text not in snippet_parts:
                    snippet_parts.append(claim.text[:100])
                break

        # Body text match — need to re-read body from file
        body_match = _body_contains(str(page.absolute_path), query_lower)
        if body_match:
            score += 3
            if body_match not in snippet_parts:
                snippet_parts.append(body_match[:100])

        if score > 0:
            snippet = snippet_parts[0] if snippet_parts else page.title
            scored.append((score, {
                "title": page.title,
                "path": page.relative_path,
                "kind": page.kind,
                "id": page.id,
                "score": score,
                "snippet": snippet,
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:max_results]]


def _body_contains(file_path: str, query: str) -> Optional[str]:
    """Check if file body contains query, return snippet or None."""
    try:
        content = Path(file_path).read_text(encoding="utf-8")
        _, body = parse_frontmatter(content)
        idx = body.lower().find(query)
        if idx != -1:
            start = max(0, idx - 40)
            end = min(len(body), idx + len(query) + 40)
            return body[start:end].strip()
    except (OSError, UnicodeDecodeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Get single page
# ---------------------------------------------------------------------------

def get_wiki_page(
    vault_path: Path,
    lookup: str,
    from_line: Optional[int] = None,
    line_count: Optional[int] = None,
) -> Optional[dict]:
    """Get a wiki page by id or relative path.

    *lookup* is tried as:
      1. An exact relative path match (with/without .md extension)
      2. A page id
      3. A basename match

    Returns None if not found.
    """
    pages = read_wiki_pages(vault_path)

    # Try exact relative path
    for page in pages:
        if page.relative_path == lookup or page.relative_path == lookup + ".md":
            return _page_to_result(page, from_line, line_count)

    # Try id
    for page in pages:
        if page.id == lookup:
            return _page_to_result(page, from_line, line_count)

    # Try basename
    lookup_lower = lookup.lower()
    for page in pages:
        if Path(page.relative_path).stem.lower() == lookup_lower:
            return _page_to_result(page, from_line, line_count)

    return None


def _page_to_result(
    page: WikiPageSummary,
    from_line: Optional[int],
    line_count: Optional[int],
) -> dict:
    """Read a page's raw content and return it with metadata."""
    content = ""
    try:
        raw = Path(page.absolute_path).read_text(encoding="utf-8")
        _, content = parse_frontmatter(raw)
        lines = content.splitlines()

        if from_line is not None:
            start = max(0, from_line - 1)
            end = len(lines) if line_count is None else min(start + line_count, len(lines))
            content = "\n".join(lines[start:end])
        elif line_count is not None:
            content = "\n".join(lines[:line_count])
    except (OSError, UnicodeDecodeError):
        content = ""

    return {
        "title": page.title,
        "path": page.relative_path,
        "kind": page.kind,
        "id": page.id,
        "content": content,
        "claims": [
            {"id": c.id, "text": c.text, "status": c.status,
             "confidence": c.confidence, "evidenceCount": len(c.evidence)}
            for c in page.claims
        ],
    }

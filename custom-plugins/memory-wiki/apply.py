"""apply — Apply mutations to the wiki vault for the memory-wiki plugin.

Implements three apply modes:
  - synthesis: regenerate synthesis pages from source pages
  - metadata: bulk-update frontmatter fields across pages
  - lint-fix: auto-fix common lint issues (broken wikilinks, missing IDs, etc.)

Inspired by OpenClaw's apply.ts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import compile as _compile
from .markdown_utils import (
    GENERATED_END,
    GENERATED_START,
    HUMAN_END,
    HUMAN_START,
    LINT_END,
    LINT_START,
    extract_wikilinks,
    infer_wiki_page_kind,
    normalize_string,
    normalize_string_list,
    parse_frontmatter,
    render_frontmatter,
    replace_managed_block,
    slugify,
)
from .query import read_wiki_pages
from .wiki_lint import (
    LintIssue,
    collect_page_issues,
    group_issues_by_category,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ApplyResult:
    mode: str
    changed: int
    errors: int
    details: List[dict]


# ---------------------------------------------------------------------------
# Apply: synthesis
# ---------------------------------------------------------------------------

def apply_synthesis(
    vault_path: Path,
    dry_run: bool = False,
    target_id: Optional[str] = None,
) -> ApplyResult:
    """Regenerate synthesis pages from source pages.

    If *target_id* is specified, only that synthesis is regenerated.
    If *dry_run* is True, nothing is written.
    """
    targets = _compile.discover_synthesis_targets(vault_path)

    if target_id:
        targets = [t for t in targets if t.synthesis_id == target_id]

    if not targets:
        # Suggest targets
        targets = _compile.suggest_synthesis_targets(vault_path)
        if not targets:
            return ApplyResult(
                mode="synthesis",
                changed=0,
                errors=0,
                details=[{"action": "no-targets", "message": "No synthesis targets found."}],
            )

    changed = 0
    errors = 0
    details: List[dict] = []

    for target in targets:
        result = _compile.compile_synthesis(target, vault_path, dry_run=dry_run)
        if result.error:
            errors += 1
            details.append({
                "action": "compile-error",
                "synthesis_id": result.synthesis_id,
                "error": result.error,
            })
        elif result.written:
            changed += 1
            details.append({
                "action": "compiled" if not dry_run else "would-compile",
                "synthesis_id": result.synthesis_id,
                "title": result.title,
                "claims": result.claims_included,
                "sources": result.sources_aggregated,
            })
        else:
            details.append({
                "action": "skipped",
                "synthesis_id": result.synthesis_id,
                "reason": result.error or "no source pages",
            })

    return ApplyResult(
        mode="synthesis",
        changed=changed,
        errors=errors,
        details=details,
    )


# ---------------------------------------------------------------------------
# Apply: metadata (bulk update)
# ---------------------------------------------------------------------------

def _set_nested(fm: dict, key_path: str, value: Any) -> None:
    """Set a potentially nested key in frontmatter dict using dot notation.

    e.g. _set_nested(fm, "claims[0].status", "supported")
    """
    parts = key_path.split(".")
    current = fm

    for i, part in enumerate(parts[:-1]):
        # Handle array notation: claims[0]
        array_match = re.match(r"^(.+)\[(\d+)\]$", part)
        if array_match:
            array_key = array_match.group(1)
            index = int(array_match.group(2))
            if array_key not in current:
                current[array_key] = []
            while len(current[array_key]) <= index:
                current[array_key].append({})
            current = current[array_key][index]
        else:
            if part not in current:
                current[part] = {}
            current = current[part]

    last_part = parts[-1]
    # Handle array notation on last part too
    array_match = re.match(r"^(.+)\[(\d+)\]$", last_part)
    if array_match:
        array_key = array_match.group(1)
        index = int(array_match.group(2))
        if array_key not in current:
            current[array_key] = []
        while len(current[array_key]) <= index:
            current[array_key].append({})
        current[array_key][index] = value
    else:
        current[last_part] = value


def apply_metadata(
    vault_path: Path,
    updates: Dict[str, Any],
    dry_run: bool = False,
    filter_kinds: Optional[List[str]] = None,
    filter_query: Optional[str] = None,
) -> ApplyResult:
    """Bulk-update frontmatter fields across wiki pages.

    *updates* is a dict of frontmatter key -> value to set.
    *filter_kinds* restricts to specific page kinds (entity, concept, etc.)
    *filter_query* is a substring match on page title.
    *dry_run* shows what would change without writing.

    Example updates:
      {"updatedAt": "2024-01-01T00:00:00Z", "confidence": 0.9}
      {"claims[0].status": "supported"}
    """
    pages = read_wiki_pages(vault_path)

    # Filter
    if filter_kinds:
        pages = [p for p in pages if p.kind in filter_kinds]
    if filter_query:
        query = filter_query.lower()
        pages = [p for p in pages if query in p.title.lower()]

    changed = 0
    errors = 0
    details: List[dict] = []

    for page in pages:
        file_path = vault_path / page.relative_path
        if not file_path.exists():
            continue

        try:
            raw = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            errors += 1
            details.append({
                "action": "error",
                "path": page.relative_path,
                "error": "Cannot read file",
            })
            continue

        fm, body = parse_frontmatter(raw)

        # Apply updates
        for key, value in updates.items():
            try:
                _set_nested(fm, key, value)
            except Exception as e:
                errors += 1
                details.append({
                    "action": "update-error",
                    "path": page.relative_path,
                    "key": key,
                    "error": str(e),
                })
                continue

        if dry_run:
            details.append({
                "action": "would-update",
                "path": page.relative_path,
                "updates": updates,
            })
            changed += 1
            continue

        # Write back
        try:
            updated = render_frontmatter(fm, body)
            file_path.write_text(updated, encoding="utf-8")
            changed += 1
            details.append({
                "action": "updated",
                "path": page.relative_path,
                "updates": updates,
            })
        except OSError as e:
            errors += 1
            details.append({
                "action": "error",
                "path": page.relative_path,
                "error": str(e),
            })

    return ApplyResult(
        mode="metadata",
        changed=changed,
        errors=errors,
        details=details,
    )


# ---------------------------------------------------------------------------
# Apply: lint-fix
# ---------------------------------------------------------------------------

def _fix_missing_id(page, fm: dict) -> Optional[str]:
    """Fix a missing id field."""
    if "id" not in fm or not fm["id"]:
        kind = fm.get("pageType", page.kind)
        fm["id"] = slugify(fm.get("title", page.title))[:40]
        if not fm["id"]:
            fm["id"] = f"{kind}.{slugify(page.title)[:30]}"
        return fm["id"]
    return None


def _fix_missing_page_type(page, fm: dict) -> Optional[str]:
    """Fix a missing pageType field."""
    if "pageType" not in fm or not fm["pageType"]:
        fm["pageType"] = page.kind
        return page.kind
    return None


def _fix_missing_title(page, fm: dict, body: str) -> Optional[str]:
    """Fix a missing title."""
    if not fm.get("title"):
        # Try to extract from h1
        match = re.match(r"^#\s+(.+?)\s*$", body.strip(), re.MULTILINE)
        if match:
            fm["title"] = match.group(1).strip()
        else:
            fm["title"] = page.title or Path(page.relative_path).stem
        return fm["title"]
    return None


def _fuzzy_page_match(link: str, pages: list) -> Optional[Any]:
    """Find the best matching page for a broken wikilink.

    Tries matching strategies in order:
      1. Exact id match (case-insensitive)
      2. Exact basename match (case-insensitive)
      3. Slug match on page title
      4. Substring match on page title (link is substring of title or vice versa)
      5. Best "similarity" by shared word count
    Returns the matching page or None.
    """
    if not pages:
        return None

    link_clean = link.replace(".md", "").strip()
    link_slug = slugify(link_clean)
    link_lower = link_clean.lower()

    candidates: list = []

    for p in pages:
        score = 0
        matched_on = []

        # 1. Exact id match
        if p.id and p.id.lower() == link_lower:
            return p

        # 2. Exact basename match
        p_basename = Path(p.relative_path).stem
        if p_basename.lower() == link_lower:
            return p

        # 3. Slug match
        p_title_slug = slugify(p.title)
        if p_title_slug and p_title_slug == link_slug:
            return p

        # 4. Substring match
        p_title_lower = p.title.lower()
        if link_lower in p_title_lower:
            score = 10
            matched_on.append("title-contains-link")
        elif p_title_lower in link_lower:
            score = 8
            matched_on.append("link-contains-title")

        # 5. Shared word count
        link_words = set(link_slug.split("-"))
        p_words = set(p_title_slug.split("-"))
        shared = link_words & p_words
        if shared and len(shared) >= min(len(link_words), len(p_words)) * 0.5:
            score = max(score, 5 + len(shared) * 2)
            matched_on.append(f"shared-words:{shared}")

        if score > 0:
            candidates.append((score, p, matched_on))

    if not candidates:
        return None

    # Sort by score descending, pick best
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


_WIKILINK_PATTERN = re.compile(
    r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]",
    re.DOTALL,
)


def _fix_broken_wikilinks_in_body(
    vault_path: Path,
    pages: list,
    body: str,
) -> tuple[str, int]:
    """Find and fix all broken wikilinks in body text.

    Returns (fixed_body, fix_count).
    """
    links = extract_wikilinks(body)
    fixed_body = body
    fix_count = 0

    for link in links:
        link_clean = link.replace(".md", "").strip()

        # Check if this link target actually exists on disk
        target_path = vault_path / link_clean
        target_path_md = vault_path / f"{link_clean}.md"
        if target_path.exists() or target_path_md.exists():
            continue  # Link is valid — skip

        # Link is broken — try to find the correct target page
        target_page = _fuzzy_page_match(link_clean, pages)
        if target_page is None:
            continue  # Can't find a replacement — leave it broken

        # Find all [[link]] or [[link|display]] occurrences in the body
        for m in _WIKILINK_PATTERN.finditer(fixed_body):
            wikilink_text = m.group(1).replace(".md", "").strip()
            if wikilink_text != link_clean:
                continue  # Not this occurrence

            # Build replacement
            display_text = m.group(2)
            replacement_slug = target_page.relative_path.replace(".md", "")

            if display_text:
                new_wikilink = f"[[{replacement_slug}|{display_text}]]"
            else:
                new_wikilink = f"[[{replacement_slug}]]"

            # Replace at match position
            fixed_body = fixed_body[:m.start()] + new_wikilink + fixed_body[m.end():]
            fix_count += 1
            break  # Only fix the first occurrence per link target

    return fixed_body, fix_count


def apply_lint_fix(
    vault_path: Path,
    dry_run: bool = False,
    categories: Optional[List[str]] = None,
) -> ApplyResult:
    """Auto-fix common lint issues.

    *categories* restricts which issue types to fix:
      - structure (missing id, pageType, title)
      - links (broken wikilinks)
      - provenance (missing sourceIds, etc.)
    """
    pages = read_wiki_pages(vault_path)
    issues = collect_page_issues(pages)

    if categories:
        issues = [i for i in issues if i.category in categories]

    changed = 0
    errors = 0
    details: List[dict] = []

    # Group issues by path
    issues_by_path: Dict[str, List[LintIssue]] = {}
    for issue in issues:
        issues_by_path.setdefault(issue.path, []).append(issue)

    # Pre-load pages once for wikilink fixes
    wikilink_pages: Optional[list] = None

    for path, path_issues in issues_by_path.items():
        file_path = vault_path / path
        if not file_path.exists():
            continue

        try:
            raw = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            errors += 1
            details.append({"action": "error", "path": path, "error": "Cannot read file"})
            continue

        fm, body = parse_frontmatter(raw)

        # Reconstruct page-like object for fix functions
        page_like = type("PageLike", (), {
            "kind": infer_wiki_page_kind(path) or "entity",
            "title": fm.get("title", ""),
            "relative_path": path,
        })()

        fixes_applied: List[str] = []

        for issue in path_issues:
            if issue.code == "missing-id":
                fixed_id = _fix_missing_id(page_like, fm)
                if fixed_id:
                    fixes_applied.append(f"added id: {fixed_id}")

            elif issue.code == "missing-page-type":
                fixed_pt = _fix_missing_page_type(page_like, fm)
                if fixed_pt:
                    fixes_applied.append(f"added pageType: {fixed_pt}")

            elif issue.code == "missing-title":
                fixed_title = _fix_missing_title(page_like, fm, body)
                if fixed_title:
                    fixes_applied.append(f"added title: {fixed_title}")

            elif issue.code == "broken-wikilink":
                if wikilink_pages is None:
                    wikilink_pages = read_wiki_pages(vault_path)
                fixed_body, link_fixes = _fix_broken_wikilinks_in_body(
                    vault_path, wikilink_pages, body,
                )
                if link_fixes > 0:
                    body = fixed_body
                    fixes_applied.append(f"fixed {link_fixes} wikilink(s)")

        if fixes_applied:
            if dry_run:
                changed += 1
                details.append({
                    "action": "would-fix",
                    "path": path,
                    "fixes": fixes_applied,
                })
            else:
                try:
                    updated = render_frontmatter(fm, body)
                    file_path.write_text(updated, encoding="utf-8")
                    changed += 1
                    details.append({
                        "action": "fixed",
                        "path": path,
                        "fixes": fixes_applied,
                    })
                except OSError as e:
                    errors += 1
                    details.append({"action": "error", "path": path, "error": str(e)})

    return ApplyResult(
        mode="lint-fix",
        changed=changed,
        errors=errors,
        details=details,
    )

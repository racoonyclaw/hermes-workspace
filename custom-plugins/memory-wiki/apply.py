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


def _fix_broken_wikilink(vault_path: Path, page, body: str) -> tuple[str, int]:
    """Attempt to fix broken wikilinks in body.

    Tries to resolve ambiguous links by matching similar filenames/ids.
    Returns (fixed_body, fix_count).
    """
    pages = read_wiki_pages(vault_path)
    pages_by_id: Dict[str, Any] = {p.id: p for p in pages if p.id}
    pages_by_slug: Dict[str, Any] = {slugify(p.title): p for p in pages}

    links = extract_wikilinks(body)
    fixed_body = body
    fix_count = 0

    for link in links:
        # Try to find a match
        target = None

        # Exact match in ids
        if link in pages_by_id:
            target = pages_by_id[link]
        # Slug match
        elif slugify(link) in pages_by_slug:
            target = pages_by_slug[slugify(link)]

        if target:
            # Check if the link in the body actually points nowhere
            old_link_pattern = re.compile(
                r"\[\[([^|\]]+?)(\|[^\]]+)?\]\]",
                re.DOTALL,
            )
            for m in old_link_pattern.finditer(fixed_body):
                if m.group(1).strip() == link:
                    # Check if this is actually a broken link
                    target_path = vault_path / link.replace("/", "/")
                    target_path_md = vault_path / f"{link}.md"
                    if not target_path.exists() and not target_path_md.exists():
                        # Replace with correct target
                        new_link = f"[[{target.relative_path.replace('.md', '')}]]"
                        if m.group(2):
                            new_link = f"[[{target.relative_path.replace('.md', '')}|{m.group(2)[1:]}]]"
                        fixed_body = fixed_body[:m.start()] + new_link + fixed_body[m.end():]
                        fix_count += 1

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

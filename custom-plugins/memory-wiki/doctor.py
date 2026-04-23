"""doctor — Vault health checks for the memory-wiki plugin.

Runs comprehensive health checks: directory structure, file permissions,
broken wikilinks, index consistency, disk space, and common corruption patterns.

Inspired by OpenClaw's doctor.ts (runWikiDoctor).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .markdown_utils import extract_wikilinks, parse_frontmatter
from .query import read_wiki_pages
from .vault import REQUIRED_DIRS, get_vault_status


# ---------------------------------------------------------------------------
# Issue types
# ---------------------------------------------------------------------------

@dataclass
class DoctorIssue:
    severity: str  # "error" | "warning" | "info"
    category: str  # "structure" | "permissions" | "links" | "consistency" | "disk" | "corruption"
    code: str
    path: str
    message: str


# ---------------------------------------------------------------------------
# Check: Directory structure
# ---------------------------------------------------------------------------

def _check_structure(vault_path: Path) -> List[DoctorIssue]:
    issues: List[DoctorIssue] = []

    if not vault_path.exists():
        issues.append(DoctorIssue(
            severity="error",
            category="structure",
            code="vault-missing",
            path=str(vault_path),
            message="Vault root directory does not exist.",
        ))
        return issues

    if not vault_path.is_dir():
        issues.append(DoctorIssue(
            severity="error",
            category="structure",
            code="vault-not-directory",
            path=str(vault_path),
            message="Vault path exists but is not a directory.",
        ))
        return issues

    for dir_name in REQUIRED_DIRS:
        dir_path = vault_path / dir_name
        if not dir_path.exists():
            issues.append(DoctorIssue(
                severity="warning",
                category="structure",
                code="missing-directory",
                path=str(dir_path),
                message=f"Required directory '{dir_name}' is missing.",
            ))
        elif not dir_path.is_dir():
            issues.append(DoctorIssue(
                severity="error",
                category="structure",
                code="not-a-directory",
                path=str(dir_path),
                message=f"'{dir_name}' exists but is not a directory.",
            ))

    return issues


# ---------------------------------------------------------------------------
# Check: File permissions
# ---------------------------------------------------------------------------

def _check_permissions(vault_path: Path) -> List[DoctorIssue]:
    issues: List[DoctorIssue] = []

    for item in vault_path.rglob("*"):
        if item.is_dir():
            # Check directory read/write/execute
            if not os.access(item, os.W_OK):
                issues.append(DoctorIssue(
                    severity="warning",
                    category="permissions",
                    code="directory-not-writable",
                    path=str(item),
                    message=f"Directory is not writable: {item.name}",
                ))
        elif item.is_file() and item.suffix == ".md":
            # Check file read
            if not os.access(item, os.R_OK):
                issues.append(DoctorIssue(
                    severity="error",
                    category="permissions",
                    code="file-not-readable",
                    path=str(item),
                    message=f"File is not readable: {item.name}",
                ))
            # Check file write
            if not os.access(item, os.W_OK):
                issues.append(DoctorIssue(
                    severity="warning",
                    category="permissions",
                    code="file-not-writable",
                    path=str(item),
                    message=f"File is not writable: {item.name}",
                ))

    return issues


# ---------------------------------------------------------------------------
# Check: Wikilinks
# ---------------------------------------------------------------------------

def _check_wikilinks(vault_path: Path, pages) -> List[DoctorIssue]:
    issues: List[DoctorIssue] = []

    # Build valid target set
    valid_targets: Dict[str, Path] = {}  # target -> path
    for page in pages:
        without_ext = page.relative_path.replace(".md", "")
        valid_targets[without_ext] = Path(page.relative_path)
        # Also add by basename
        valid_targets[Path(without_ext).name] = Path(page.relative_path)
        # Also add by id
        if page.id:
            valid_targets[page.id] = Path(page.relative_path)

    for page in pages:
        try:
            raw = Path(page.absolute_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            issues.append(DoctorIssue(
                severity="error",
                category="corruption",
                code="file-unreadable",
                path=page.relative_path,
                message=f"Cannot read file: {e}",
            ))
            continue

        _, body = parse_frontmatter(raw)
        links = extract_wikilinks(body)

        for link in links:
            # Strip .md extension
            link_clean = link.replace(".md", "")

            # Check if it's a valid target
            if link_clean not in valid_targets:
                issues.append(DoctorIssue(
                    severity="warning",
                    category="links",
                    code="broken-wikilink",
                    path=page.relative_path,
                    message=f"Broken wikilink: [[{link}]] → target not found in vault.",
                ))

            # Check if the target file actually exists
            target_path = vault_path / link.replace("/", "/")
            target_path_md = vault_path / f"{link}.md"
            if not target_path.exists() and not target_path_md.exists():
                issues.append(DoctorIssue(
                    severity="warning",
                    category="links",
                    code="broken-wikilink",
                    path=page.relative_path,
                    message=f"Broken wikilink: [[{link}]] → file not found on disk.",
                ))

    return issues


# ---------------------------------------------------------------------------
# Check: Index consistency
# ---------------------------------------------------------------------------

def _check_index_consistency(vault_path: Path, pages) -> List[DoctorIssue]:
    """Check that pages listed in index.md actually exist."""
    issues: List[DoctorIssue] = []

    index_path = vault_path / "index.md"
    if not index_path.exists():
        issues.append(DoctorIssue(
            severity="warning",
            category="consistency",
            code="missing-index",
            path=str(index_path),
            message="index.md is missing.",
        ))
        return issues

    # Find all [[wikilinks]] in index.md
    try:
        raw = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        issues.append(DoctorIssue(
            severity="error",
            category="corruption",
            code="index-unreadable",
            path=str(index_path),
            message="Cannot read index.md.",
        ))
        return issues

    links = extract_wikilinks(raw)

    existing_paths = {p.relative_path for p in pages}
    existing_ids = {p.id for p in pages if p.id}

    for link in links:
        link_clean = link.replace(".md", "")
        found = False
        for path in existing_paths:
            if link_clean in path or link_clean == Path(path).stem:
                found = True
                break
        if not found and link_clean in existing_ids:
            found = True

        if not found:
            issues.append(DoctorIssue(
                severity="info",
                category="consistency",
                code="index-references-nonexistent",
                path=str(index_path),
                message=f"index.md references [[{link}]] but no matching page was found.",
            ))

    return issues


# ---------------------------------------------------------------------------
# Check: Orphan pages
# ---------------------------------------------------------------------------

def _check_orphans(vault_path: Path, pages) -> List[DoctorIssue]:
    """Find pages that nothing links to (orphan pages)."""
    issues: List[DoctorIssue] = []

    # Collect all link targets
    linked_paths: set = set()
    for page in pages:
        try:
            raw = Path(page.absolute_path).read_text(encoding="utf-8")
            _, body = parse_frontmatter(raw)
            for link in extract_wikilinks(body):
                link_clean = link.replace(".md", "")
                linked_paths.add(link_clean)
        except (OSError, UnicodeDecodeError):
            continue

    # Pages not linked by anything (excluding index, AGENTS, WIKI)
    for page in pages:
        if page.kind in ("source", "report"):
            continue  # Sources/reports don't need links
        if page.relative_path in ("index.md", "AGENTS.md", "WIKI.md"):
            continue

        slug = page.relative_path.replace(".md", "")
        slug_name = Path(slug).name

        if slug not in linked_paths and slug_name not in linked_paths and page.id not in linked_paths:
            issues.append(DoctorIssue(
                severity="info",
                category="consistency",
                code="orphan-page",
                path=page.relative_path,
                message=f"Page is not linked from any other page (orphan).",
            ))

    return issues


# ---------------------------------------------------------------------------
# Check: Disk space
# ---------------------------------------------------------------------------

def _check_disk_space(vault_path: Path) -> List[DoctorIssue]:
    issues: List[DoctorIssue] = []

    try:
        import shutil
        stat = shutil.disk_usage(vault_path)
        free_gb = stat.free / (1024**3)

        if free_gb < 0.5:
            issues.append(DoctorIssue(
                severity="error",
                category="disk",
                code="low-disk-space",
                path=str(vault_path),
                message=f"Very low disk space: {free_gb:.1f} GB free.",
            ))
        elif free_gb < 2.0:
            issues.append(DoctorIssue(
                severity="warning",
                category="disk",
                code="low-disk-space",
                path=str(vault_path),
                message=f"Low disk space: {free_gb:.1f} GB free.",
            ))
    except (OSError, AttributeError):
        pass

    return issues


# ---------------------------------------------------------------------------
# Check: Frontmatter corruption
# ---------------------------------------------------------------------------

def _check_corruption(vault_path: Path, pages) -> List[DoctorIssue]:
    issues: List[DoctorIssue] = []

    for page in pages:
        try:
            raw = Path(page.absolute_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # Check for null bytes
        if "\x00" in raw:
            issues.append(DoctorIssue(
                severity="error",
                category="corruption",
                code="null-bytes",
                path=page.relative_path,
                message="File contains null bytes — possible binary corruption.",
            ))

        # Check frontmatter parse
        try:
            fm, _ = parse_frontmatter(raw)
            if fm is None:
                issues.append(DoctorIssue(
                    severity="warning",
                    category="corruption",
                    code="frontmatter-parse-failed",
                    path=page.relative_path,
                    message="Frontmatter could not be parsed.",
                ))
        except Exception:
            issues.append(DoctorIssue(
                severity="warning",
                category="corruption",
                code="frontmatter-parse-failed",
                path=page.relative_path,
                message="Frontmatter raised an exception during parsing.",
            ))

        # Check for very long lines (possible corruption)
        _, body = parse_frontmatter(raw)
        for i, line in enumerate(body.splitlines(), 1):
            if len(line) > 100_000:
                issues.append(DoctorIssue(
                    severity="warning",
                    category="corruption",
                    code="excessively-long-line",
                    path=page.relative_path,
                    message=f"Line {i} is unusually long ({len(line)} chars) — possible corruption.",
                ))
                break

    return issues


# ---------------------------------------------------------------------------
# Run all checks
# ---------------------------------------------------------------------------

def run_doctor(vault_path: Path) -> dict:
    """Run all health checks on the vault.

    Returns a dict with:
      - vaultRoot: str
      - healthy: bool
      - issueCount: int
      - issues: list of issue dicts
      - checks: list of check names that ran
    """
    checks = [
        "structure",
        "permissions",
        "wikilinks",
        "index-consistency",
        "orphans",
        "disk-space",
        "corruption",
    ]

    issues: List[DoctorIssue] = []

    # Structure check (doesn't need pages)
    issues.extend(_check_structure(vault_path))

    # Read pages (needed for most checks)
    if vault_path.exists():
        pages = read_wiki_pages(vault_path)
        issues.extend(_check_wikilinks(vault_path, pages))
        issues.extend(_check_index_consistency(vault_path, pages))
        issues.extend(_check_orphans(vault_path, pages))
        issues.extend(_check_corruption(vault_path, pages))
        issues.extend(_check_permissions(vault_path))
        issues.extend(_check_disk_space(vault_path))

    # Count by severity
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    infos = [i for i in issues if i.severity == "info"]

    return {
        "vaultRoot": str(vault_path),
        "healthy": len(errors) == 0 and len(warnings) == 0,
        "issueCount": len(issues),
        "errorCount": len(errors),
        "warningCount": len(warnings),
        "infoCount": len(infos),
        "issues": [
            {"severity": i.severity, "category": i.category, "code": i.code,
             "path": i.path, "message": i.message}
            for i in issues
        ],
        "checks": checks,
    }

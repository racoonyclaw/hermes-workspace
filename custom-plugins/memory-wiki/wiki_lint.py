"""wiki_lint — Core lint logic for the memory-wiki plugin.

Ported from OpenClaw's memory-wiki extension (lint.ts).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .claim_health import (
    WikiFreshness,
    WikiPageSummary,
    assess_freshness,
    build_claim_clusters,
    build_page_contradiction_clusters,
    collect_claim_health,
)
from .markdown_utils import (
    LINT_END,
    LINT_START,
    extract_wikilinks,
    infer_wiki_page_kind,
    normalize_string,
    normalize_string_list,
    parse_frontmatter,
    render_frontmatter,
    replace_managed_block,
)
from .query import read_wiki_pages


# ---------------------------------------------------------------------------
# Issue types
# ---------------------------------------------------------------------------

@dataclass
class LintIssue:
    severity: str  # "error" | "warning"
    category: str  # "structure" | "provenance" | "links" | "contradictions" | "open-questions" | "quality"
    code: str  # e.g. "missing-id", "broken-wikilink", "claim-conflict"
    path: str
    message: str


# ---------------------------------------------------------------------------
# Issue collection
# ---------------------------------------------------------------------------

def _collect_structure_issues(pages: list[WikiPageSummary]) -> list[LintIssue]:
    """Collect structure-class issues: missing/duplicate id, pageType, title."""
    issues: list[LintIssue] = []
    pages_by_id: Dict[str, List[WikiPageSummary]] = {}

    for page in pages:
        # Missing id
        if not page.id:
            issues.append(LintIssue(
                severity="error",
                category="structure",
                code="missing-id",
                path=page.relative_path,
                message="Missing `id` frontmatter.",
            ))

        # Duplicate id tracking
        if page.id:
            pages_by_id.setdefault(page.id, []).append(page)

        # Missing pageType
        if not page.page_type:
            issues.append(LintIssue(
                severity="error",
                category="structure",
                code="missing-page-type",
                path=page.relative_path,
                message="Missing `pageType` frontmatter.",
            ))
        elif page.page_type != page.kind:
            # page_type != expected kind from directory
            issues.append(LintIssue(
                severity="error",
                category="structure",
                code="page-type-mismatch",
                path=page.relative_path,
                message=f"Expected pageType `{page.kind}`, found `{page.page_type}`.",
            ))

        # Missing title
        if not page.title.strip():
            issues.append(LintIssue(
                severity="error",
                category="structure",
                code="missing-title",
                path=page.relative_path,
                message="Missing page title.",
            ))

    # Duplicate ids
    for id_val, matches in pages_by_id.items():
        if len(matches) > 1:
            for match in matches:
                issues.append(LintIssue(
                    severity="error",
                    category="structure",
                    code="duplicate-id",
                    path=match.relative_path,
                    message=f"Duplicate page id `{id_val}`.",
                ))

    return issues


def _collect_provenance_issues(pages: list[WikiPageSummary]) -> list[LintIssue]:
    """Collect provenance-class issues: missing sourceIds, missing import provenance."""
    issues: list[LintIssue] = []

    for page in pages:
        # Non-source pages should have sourceIds
        if page.kind not in ("source", "report") and len(page.source_ids) == 0:
            issues.append(LintIssue(
                severity="warning",
                category="provenance",
                code="missing-source-ids",
                path=page.relative_path,
                message="Non-source page is missing `sourceIds` provenance.",
            ))

        # Bridge-imported source pages need full provenance fields
        if page.source_type in ("memory-bridge", "memory-bridge-events"):
            missing: list[str] = []
            if not page.source_path:
                missing.append("sourcePath")
            if not page.bridge_relative_path:
                missing.append("bridgeRelativePath")
            if not page.bridge_workspace_dir:
                missing.append("bridgeWorkspaceDir")
            if missing:
                issues.append(LintIssue(
                    severity="warning",
                    category="provenance",
                    code="missing-import-provenance",
                    path=page.relative_path,
                    message=f"Bridge-imported source page is missing `{', '.join(missing)}` provenance.",
                ))

        # Unsafe-local imported pages need full provenance fields
        if page.provenance_mode == "unsafe-local" or page.source_type == "memory-unsafe-local":
            missing = []
            if not page.source_path:
                missing.append("sourcePath")
            if not page.unsafe_local_configured_path:
                missing.append("unsafeLocalConfiguredPath")
            if not page.unsafe_local_relative_path:
                missing.append("unsafeLocalRelativePath")
            if missing:
                issues.append(LintIssue(
                    severity="warning",
                    category="provenance",
                    code="missing-import-provenance",
                    path=page.relative_path,
                    message=f"Unsafe-local source page is missing `{', '.join(missing)}` provenance.",
                ))

    return issues


def _collect_link_issues(pages: list[WikiPageSummary]) -> list[LintIssue]:
    """Collect link-class issues: broken wikilinks."""
    issues: list[LintIssue] = []

    # Build valid target set (relative paths without .md, plus basenames)
    valid_targets: set[str] = set()
    for page in pages:
        without_ext = re.sub(r"\.md$", "", page.relative_path, flags=re.I)
        valid_targets.add(without_ext)
        valid_targets.add(Path(without_ext).name)

    for page in pages:
        for target in page.link_targets:
            if target not in valid_targets:
                issues.append(LintIssue(
                    severity="warning",
                    category="links",
                    code="broken-wikilink",
                    path=page.relative_path,
                    message=f"Broken wikilink target `{target}`.",
                ))

    return issues


def _collect_contradiction_issues(pages: list[WikiPageSummary]) -> list[LintIssue]:
    """Collect contradiction-class issues: page contradictions and claim clusters."""
    issues: list[LintIssue] = []

    # Page-level contradictions
    for page in pages:
        if len(page.contradictions) > 0:
            issues.append(LintIssue(
                severity="warning",
                category="contradictions",
                code="contradiction-present",
                path=page.relative_path,
                message=f"Page lists {len(page.contradictions)} contradiction{'s' if len(page.contradictions) != 1 else ''} to resolve.",
            ))

    # Claim clusters with competing variants
    for cluster in build_claim_clusters(pages):
        for entry in cluster.entries:
            issues.append(LintIssue(
                severity="warning",
                category="contradictions",
                code="claim-conflict",
                path=entry.page_path,
                message=f"Claim cluster `{cluster.label}` has competing variants across {len(cluster.entries)} pages.",
            ))

    return issues


def _collect_open_question_issues(pages: list[WikiPageSummary]) -> list[LintIssue]:
    """Collect open-question-class issues."""
    issues: list[LintIssue] = []
    for page in pages:
        if len(page.questions) > 0:
            issues.append(LintIssue(
                severity="warning",
                category="open-questions",
                code="open-question",
                path=page.relative_path,
                message=f"Page lists {len(page.questions)} open question{'s' if len(page.questions) != 1 else ''}.",
            ))
    return issues


def _collect_quality_issues(pages: list[WikiPageSummary], now: datetime) -> list[LintIssue]:
    """Collect quality-class issues: low confidence, stale pages/claims."""
    issues: list[LintIssue] = []
    all_claim_health = collect_claim_health(pages, now)

    for page in pages:
        # Page-level low confidence
        if page.confidence is not None and page.confidence < 0.5:
            issues.append(LintIssue(
                severity="warning",
                category="quality",
                code="low-confidence",
                path=page.relative_path,
                message=f"Page confidence is low ({page.confidence:.2f}).",
            ))

        # Stale page (non-report pages)
        freshness = assess_freshness(page.updated_at, now)
        if page.kind != "report" and freshness.level in ("stale", "unknown"):
            issues.append(LintIssue(
                severity="warning",
                category="quality",
                code="stale-page",
                path=page.relative_path,
                message=f"Page freshness needs review ({freshness.reason}).",
            ))

    # Claim-level quality issues
    for claim in all_claim_health:
        if claim.missing_evidence:
            claim_desc = f"`{claim.claim_id}`" if claim.claim_id else f"`{claim.text[:50]}`"
            issues.append(LintIssue(
                severity="warning",
                category="provenance",
                code="claim-missing-evidence",
                path=claim.page_path,
                message=f"Claim {claim_desc} is missing structured evidence.",
            ))

        if claim.confidence is not None and claim.confidence < 0.5:
            claim_desc = f"`{claim.claim_id}`" if claim.claim_id else f"`{claim.text[:50]}`"
            issues.append(LintIssue(
                severity="warning",
                category="quality",
                code="claim-low-confidence",
                path=claim.page_path,
                message=f"Claim {claim_desc} has low confidence ({claim.confidence:.2f}).",
            ))

        if claim.freshness.level in ("stale", "unknown"):
            claim_desc = f"`{claim.claim_id}`" if claim.claim_id else f"`{claim.text[:50]}`"
            issues.append(LintIssue(
                severity="warning",
                category="quality",
                code="stale-claim",
                path=claim.page_path,
                message=f"Claim {claim_desc} freshness needs review ({claim.freshness.reason}).",
            ))

    return issues


def collect_page_issues(pages: list[WikiPageSummary], now: Optional[datetime] = None) -> list[LintIssue]:
    """Run all lint checks on *pages* and return sorted issues list."""
    if now is None:
        now = datetime.now(timezone.utc)

    issues: list[LintIssue] = []
    issues.extend(_collect_structure_issues(pages))
    issues.extend(_collect_provenance_issues(pages))
    issues.extend(_collect_link_issues(pages))
    issues.extend(_collect_contradiction_issues(pages))
    issues.extend(_collect_open_question_issues(pages))
    issues.extend(_collect_quality_issues(pages, now))

    return sorted(issues, key=lambda i: i.path)


# ---------------------------------------------------------------------------
# Issue grouping
# ---------------------------------------------------------------------------

def group_issues_by_category(issues: list[LintIssue]) -> dict[str, list[LintIssue]]:
    """Group issues by category."""
    categories = ["structure", "provenance", "links", "contradictions", "open-questions", "quality"]
    return {cat: [i for i in issues if i.category == cat] for cat in categories}


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def build_lint_report_body(issues: list[LintIssue]) -> str:
    """Build the lint report markdown body from issues."""
    if not issues:
        return "No issues found."

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    by_category = group_issues_by_category(issues)

    lines = [f"- Errors: {len(errors)}", f"- Warnings: {len(warnings)}"]

    if errors:
        lines.append("", "### Errors")
        for issue in errors:
            lines.append(f"- `{issue.path}`: {issue.message}")

    if warnings:
        lines.append("", "### Warnings")
        for issue in warnings:
            lines.append(f"- `{issue.path}`: {issue.message}")

    if by_category.get("contradictions"):
        lines.append("", "### Contradictions")
        for issue in by_category["contradictions"]:
            lines.append(f"- `{issue.path}`: {issue.message}")

    if by_category.get("open-questions"):
        lines.append("", "### Open Questions")
        for issue in by_category["open-questions"]:
            lines.append(f"- `{issue.path}`: {issue.message}")

    quality_and_provenance = by_category.get("quality", []) + by_category.get("provenance", [])
    if quality_and_provenance:
        lines.append("", "### Quality Follow-Up")
        for issue in quality_and_provenance:
            lines.append(f"- `{issue.path}`: {issue.message}")

    return "\n".join(lines)


def write_lint_report(vault_path: Path, issues: list[LintIssue]) -> Path:
    """Write lint report to reports/lint.md using managed blocks."""
    report_path = vault_path / "reports" / "lint.md"

    # Read existing file or build fresh
    if report_path.exists():
        original = report_path.read_text(encoding="utf-8")
    else:
        original = render_frontmatter({
            "pageType": "report",
            "id": "report.lint",
            "title": "Lint Report",
            "status": "active",
        }, "# Lint Report\n")

    updated = replace_managed_block(
        original=original,
        heading="## Generated",
        start_marker=LINT_START,
        end_marker=LINT_END,
        body=build_lint_report_body(issues),
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(updated, encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# Main lint function
# ---------------------------------------------------------------------------

def lint_vault(vault_path: Path) -> dict:
    """Run full lint on the wiki vault at *vault_path*.

    Returns a dict with:
      - vaultRoot: str
      - issueCount: int
      - issues: list of issue dicts
      - reportPath: str
    """
    pages = read_wiki_pages(vault_path)
    issues = collect_page_issues(pages)
    report_path = write_lint_report(vault_path, issues)

    return {
        "vaultRoot": str(vault_path),
        "issueCount": len(issues),
        "issues": [
            {"severity": i.severity, "category": i.category, "code": i.code,
             "path": i.path, "message": i.message}
            for i in issues
        ],
        "reportPath": str(report_path),
    }

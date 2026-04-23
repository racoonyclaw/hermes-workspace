"""tools — Tool handlers for the memory-wiki plugin.

Each function receives (args: dict) and returns a JSON string.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from . import compile as _compile
from . import doctor as _doctor
from . import ingest as _ingest
from . import query as _query
from . import vault as _vault
from . import wiki_lint as _lint


def _vault_path(args: Dict[str, Any]) -> Path:
    return _vault.get_vault_path_or_default(args.get("vault_path"))


# ---------------------------------------------------------------------------
# wiki_lint
# ---------------------------------------------------------------------------

def handle_wiki_lint(args: Dict[str, Any], **_: Any) -> str:
    """Run full lint on the wiki vault."""
    vault_path = _vault_path(args)

    result = _lint.lint_vault(vault_path)

    if args.get("json_output"):
        return json.dumps(result, indent=2)

    summary = (
        f"Linted vault at {result['vaultRoot']}: "
        f"{result['issueCount']} issue{'s' if result['issueCount'] != 1 else ''}, "
        f"report written to {result['reportPath']}"
    )

    issues = result["issues"]
    if not issues:
        return summary + "\nNo issues found."

    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]

    lines = [summary]
    if errors:
        lines.append(f"\nErrors ({len(errors)}):")
        for issue in errors[:10]:
            lines.append(f"  [{issue['code']}] {issue['path']}: {issue['message']}")
        if len(errors) > 10:
            lines.append(f"  ... and {len(errors) - 10} more errors")

    if warnings:
        lines.append(f"\nWarnings ({len(warnings)}):")
        shown = 0
        for issue in warnings:
            if issue["code"] in ("claim-conflict", "contradiction-present", "broken-wikilink",
                                  "missing-source-ids", "stale-page", "open-question"):
                lines.append(f"  [{issue['code']}] {issue['path']}: {issue['message']}")
                shown += 1
                if shown >= 15:
                    break
        if len(warnings) > shown:
            lines.append(f"  ... and {len(warnings) - shown} more warnings")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# wiki_status
# ---------------------------------------------------------------------------

def handle_wiki_status(args: Dict[str, Any], **_: Any) -> str:
    """Get vault health summary."""
    vault_path = _vault_path(args)
    status = _vault.get_vault_status(vault_path)

    if args.get("json_output"):
        return json.dumps(status, indent=2)

    health = _vault.get_vault_health(vault_path)

    lines = [f"Vault: {status['vaultPath']}"]
    lines.append(f"Exists: {status['exists']}")

    if not status["exists"]:
        return "\n".join(lines)

    lines.append(f"Total pages: {status['pageCounts']['total']}")
    for kind in ("entity", "concept", "source", "synthesis", "report"):
        count = status["pageCounts"][kind]
        if count:
            lines.append(f"  {kind}s: {count}")

    lines.append(f"Has index: {status['hasIndex']}")
    lines.append(f"Has .openclaw-wiki: {status['openclawMeta']}")
    if status["lastModified"]:
        lines.append(f"Last modified: {status['lastModified']}")

    if health["issues"]:
        lines.append(f"\nHealth issues ({len(health['issues'])}):")
        for issue in health["issues"]:
            lines.append(f"  - {issue}")
    else:
        lines.append("\nHealth: OK")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# wiki_search
# ---------------------------------------------------------------------------

def handle_wiki_search(args: Dict[str, Any], **_: Any) -> str:
    """Search wiki pages by query."""
    vault_path = _vault_path(args)
    query = args.get("query", "").strip()

    if not query:
        return json.dumps({"error": "No query provided"})

    max_results = args.get("max_results", 10)
    results = _query.search_wiki_pages(vault_path, query, max_results)

    if args.get("json_output"):
        return json.dumps(results, indent=2)

    if not results:
        return f"No wiki pages found matching: {query}"

    lines = [f"Found {len(results)} result{'s' if len(results) != 1 else ''} for: {query}", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']} ({r['kind']})")
        lines.append(f"   Path: {r['path']}")
        lines.append(f"   Snippet: {r['snippet'][:80]}...")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# wiki_get
# ---------------------------------------------------------------------------

def handle_wiki_get(args: Dict[str, Any], **_: Any) -> str:
    """Read a specific wiki page."""
    vault_path = _vault_path(args)
    lookup = args.get("lookup", "").strip()

    if not lookup:
        return json.dumps({"error": "No lookup provided"})

    result = _query.get_wiki_page(
        vault_path,
        lookup,
        from_line=args.get("from_line"),
        line_count=args.get("line_count"),
    )

    if args.get("json_output"):
        return json.dumps(result, indent=2)

    if result is None:
        return f"Wiki page not found: {lookup}"

    lines = [
        f"# {result['title']}",
        f"Path: {result['path']}",
        f"Kind: {result['kind']}",
        f"ID: {result['id']}",
        "",
        result["content"],
    ]

    if result.get("claims"):
        lines.append("")
        lines.append("## Claims")
        for c in result["claims"]:
            lines.append(f"- [{c['status']}] {c['text']} (confidence: {c['confidence']})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# wiki_compile
# ---------------------------------------------------------------------------

def handle_wiki_compile(args: Dict[str, Any], **_: Any) -> str:
    """Compile synthesis pages from source pages."""
    vault_path = _vault_path(args)
    dry_run = bool(args.get("dry_run"))
    target_id = args.get("target_id")

    results = _compile.compile_all(
        vault_path,
        dry_run=dry_run,
    )

    # Filter by target_id if specified
    if target_id:
        results = [r for r in results if r.synthesis_id == target_id]

    if args.get("json_output"):
        return json.dumps({
            "dryRun": dry_run,
            "targetId": target_id,
            "results": [
                {
                    "synthesisPath": r.synthesis_path,
                    "synthesisId": r.synthesis_id,
                    "title": r.title,
                    "claimsIncluded": r.claims_included,
                    "sourcesAggregated": r.sources_aggregated,
                    "written": r.written,
                    "error": r.error,
                }
                for r in results
            ],
        }, indent=2)

    if not results:
        return "No synthesis targets found."

    written = [r for r in results if r.written]
    errors = [r for r in results if r.error]

    mode_str = "Would compile" if dry_run else "Compiled"
    lines = [f"{mode_str} {len(written)} synthesis page{'s' if len(written) != 1 else ''}:"]

    for r in results:
        status = "✓" if r.written else ("would" if dry_run else "✗")
        err_str = f" (error: {r.error})" if r.error else ""
        lines.append(f"  {status} {r.title} — {r.claims_included} claims, {r.sources_aggregated} sources{err_str}")

    if errors:
        lines.append(f"\nErrors ({len(errors)}):")
        for r in errors:
            lines.append(f"  {r.synthesis_id}: {r.error}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# wiki_doctor
# ---------------------------------------------------------------------------

def handle_wiki_doctor(args: Dict[str, Any], **_: Any) -> str:
    """Run vault health checks."""
    vault_path = _vault_path(args)

    result = _doctor.run_doctor(vault_path)

    if args.get("json_output"):
        return json.dumps(result, indent=2)

    lines = [
        f"Vault: {result['vaultRoot']}",
        f"Healthy: {result['healthy']}",
        f"Issues: {result['issueCount']} (errors: {result['errorCount']}, warnings: {result['warningCount']}, info: {result['infoCount']})",
    ]

    if result["issues"]:
        lines.append("\nChecks run: " + ", ".join(result["checks"]))
        lines.append("\nIssues:")
        for issue in result["issues"][:30]:
            lines.append(f"  [{issue['severity']}] {issue['code']} ({issue['category']}): {issue['path']} — {issue['message']}")
        if len(result["issues"]) > 30:
            lines.append(f"  ... and {len(result['issues']) - 30} more issues")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# wiki_ingest
# ---------------------------------------------------------------------------

def handle_wiki_ingest(args: Dict[str, Any], **_: Any) -> str:
    """Ingest markdown files into the wiki vault."""
    vault_path = _vault_path(args)
    file_path = args.get("file_path", "").strip()

    if not file_path:
        return json.dumps({"error": "No file_path provided"})

    kind = args.get("kind", "auto")
    recursive = bool(args.get("recursive", True))
    force = bool(args.get("force", False))
    namespace = args.get("namespace")

    src = Path(file_path)

    if src.is_dir():
        results = _ingest.ingest_directory(
            src,
            vault_path,
            kind=kind,
            recursive=recursive,
            force=force,
            namespace=namespace,
        )
    else:
        result = _ingest.ingest_file(
            file_path,
            vault_path,
            kind=kind,
            force=force,
            namespace=namespace,
        )
        results = [result]

    if args.get("json_output"):
        return json.dumps({
            "filePath": file_path,
            "vaultPath": str(vault_path),
            "results": [
                {
                    "originalPath": r.original_path,
                    "wikiPath": r.wiki_path,
                    "title": r.title,
                    "id": r.id,
                    "kind": r.kind,
                    "claimsExtracted": r.claims_extracted,
                    "sourcesExtracted": r.sources_extracted,
                    "warnings": r.warnings,
                    "error": r.error,
                }
                for r in results
            ],
        }, indent=2)

    succeeded = [r for r in results if r.error is None]
    failed = [r for r in results if r.error is not None]

    lines = [f"Ingested {len(succeeded)} file{'s' if len(succeeded) != 1 else ''}:"]
    for r in succeeded:
        lines.append(f"  ✓ {r.title} → {r.wiki_path} ({r.kind}, {r.claims_extracted} claims)")

    if failed:
        lines.append(f"\nFailed ({len(failed)}):")
        for r in failed:
            lines.append(f"  ✗ {r.original_path}: {r.error}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# wiki_apply
# ---------------------------------------------------------------------------

def handle_wiki_apply(args: Dict[str, Any], **_: Any) -> str:
    """Apply mutations to the wiki vault."""
    from . import apply as _apply

    vault_path = _vault_path(args)
    mode = args.get("mode", "")

    if not mode:
        return json.dumps({"error": "mode is required (synthesis, metadata, lint-fix)"})

    dry_run = bool(args.get("dry_run", False))

    if mode == "synthesis":
        result = _apply.apply_synthesis(
            vault_path,
            dry_run=dry_run,
            target_id=args.get("target_id"),
        )
    elif mode == "metadata":
        updates = args.get("updates", {})
        if not updates:
            return json.dumps({"error": "updates dict is required for metadata mode"})
        result = _apply.apply_metadata(
            vault_path,
            updates=updates,
            dry_run=dry_run,
            filter_kinds=args.get("filter_kinds"),
            filter_query=args.get("filter_query"),
        )
    elif mode == "lint-fix":
        result = _apply.apply_lint_fix(
            vault_path,
            dry_run=dry_run,
            categories=args.get("categories"),
        )
    else:
        return json.dumps({"error": f"Unknown mode: {mode}. Must be one of: synthesis, metadata, lint-fix"})

    if args.get("json_output"):
        return json.dumps({
            "mode": result.mode,
            "changed": result.changed,
            "errors": result.errors,
            "details": result.details,
        }, indent=2)

    mode_str = "Would apply" if dry_run else "Applied"
    lines = [
        f"{mode_str} {mode} — {result.changed} changed, {result.errors} errors",
    ]

    for d in result.details[:20]:
        action = d.get("action", "")
        if action in ("compiled", "would-compile"):
            lines.append(f"  • {d['title']} — {d.get('claims', '?')} claims, {d.get('sources', '?')} sources")
        elif action == "updated":
            lines.append(f"  • Updated: {d['path']}")
        elif action == "fixed":
            lines.append(f"  • Fixed: {d['path']} — {', '.join(d.get('fixes', []))}")
        elif action in ("error", "update-error"):
            lines.append(f"  ✗ {d.get('path', '?')}: {d.get('error', 'unknown error')}")
        elif action == "no-targets":
            lines.append(f"  — {d.get('message', 'No targets found')}")

    if len(result.details) > 20:
        lines.append(f"  ... and {len(result.details) - 20} more changes")

    return "\n".join(lines)

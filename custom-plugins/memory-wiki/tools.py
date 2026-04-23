"""tools — Tool handlers for the memory-wiki plugin.

Each function receives (args: dict) and returns a JSON string.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

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

    # Human-readable summary
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

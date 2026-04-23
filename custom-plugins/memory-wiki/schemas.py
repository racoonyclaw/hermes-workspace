"""schemas — Tool schemas for the memory-wiki plugin.

These define what the LLM sees when deciding whether to call each tool.
"""

from __future__ import annotations


WIKI_LINT = {
    "name": "wiki_lint",
    "description": (
        "Run lint checks on the shared wiki vault. "
        "Checks for: missing frontmatter (id, pageType, title), duplicate IDs, "
        "broken wikilinks, missing sourceIds on non-source pages, "
        "contradicting claim clusters, low-confidence claims, stale pages, "
        "and missing import provenance. "
        "Results are written to reports/lint.md. "
        "Use this when the wiki may have outdated or inconsistent entries, "
        "or after bulk edits to check for issues."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vault_path": {
                "type": "string",
                "description": (
                    "Path to the wiki vault. Defaults to the MEMORY_WIKI_PATH "
                    "env var, or /media/racoony-wiki/ if not set."
                ),
            },
            "json_output": {
                "type": "boolean",
                "description": "Return machine-readable JSON instead of a human-readable summary.",
            },
        },
    },
}


WIKI_STATUS = {
    "name": "wiki_status",
    "description": (
        "Get a quick health summary of the wiki vault: whether the vault exists, "
        "page counts per kind (entity, concept, source, synthesis, report), "
        "presence of index files and OpenClaw metadata, and the last modified timestamp. "
        "Use this to verify the vault is accessible before running other wiki tools."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vault_path": {
                "type": "string",
                "description": (
                    "Path to the wiki vault. Defaults to MEMORY_WIKI_PATH env var, "
                    "or /media/racoony-wiki/ if not set."
                ),
            },
        },
    },
}


WIKI_SEARCH = {
    "name": "wiki_search",
    "description": (
        "Search wiki pages by a query string. Searches across page titles, "
        "claim text, and body content. Returns ranked results with snippets. "
        "Use this to find pages related to a topic without knowing exact paths."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "vault_path": {
                "type": "string",
                "description": (
                    "Path to the wiki vault. Defaults to MEMORY_WIKI_PATH env var, "
                    "or /media/racoony-wiki/ if not set."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 10).",
            },
        },
        "required": ["query"],
    },
}


WIKI_GET = {
    "name": "wiki_get",
    "description": (
        "Read a specific wiki page by its relative path, page id, or basename. "
        "Returns the page title, kind, id, and content body. "
        "Optionally return only a slice of lines using fromLine and lineCount. "
        "Use this to read known pages rather than searching."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lookup": {
                "type": "string",
                "description": (
                    "Page lookup — can be a relative path (e.g. 'entities/randomstix.md'), "
                    "a page id (e.g. 'entity.randomstix'), or a page basename (e.g. 'randomstix')."
                ),
            },
            "vault_path": {
                "type": "string",
                "description": (
                    "Path to the wiki vault. Defaults to MEMORY_WIKI_PATH env var, "
                    "or /media/racoony-wiki/ if not set."
                ),
            },
            "from_line": {
                "type": "integer",
                "description": "Start reading from this line number (1-indexed, default: beginning of body).",
            },
            "line_count": {
                "type": "integer",
                "description": "Number of lines to return (default: all lines).",
            },
        },
        "required": ["lookup"],
    },
}


WIKI_COMPILE = {
    "name": "wiki_compile",
    "description": (
        "Compile synthesis pages by aggregating claims from entity and concept pages. "
        "Reads all entity/concept source pages, deduplicates claims, resolves contradictions, "
        "and writes updated synthesis documents. "
        "Use after significant changes to entity or concept pages, "
        "or to regenerate synthesis pages from claim clusters. "
        "Run with dry_run=true first to see what would be compiled."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vault_path": {
                "type": "string",
                "description": (
                    "Path to the wiki vault. Defaults to MEMORY_WIKI_PATH env var, "
                    "or /media/racoony-wiki/ if not set."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "If true, shows what would be compiled without writing any files. "
                    "Use this to preview changes before applying them."
                ),
            },
            "target_id": {
                "type": "string",
                "description": (
                    "Optional synthesis ID to compile. If not provided, "
                    "compiles all synthesis targets discovered in the vault."
                ),
            },
            "json_output": {
                "type": "boolean",
                "description": "Return machine-readable JSON instead of a human-readable summary.",
            },
        },
    },
}


WIKI_DOCTOR = {
    "name": "wiki_doctor",
    "description": (
        "Run comprehensive health checks on the wiki vault: directory structure, "
        "file permissions, broken wikilinks, index consistency, orphan pages, "
        "disk space, and frontmatter corruption. "
        "Use this to diagnose vault issues, before running heavy operations, "
        "or to get a full picture of vault health. "
        "Checks include: missing required directories, unreadable files, "
        "broken wikilinks, unreferenced pages in index, orphan pages, "
        "low disk space, and corrupt frontmatter."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vault_path": {
                "type": "string",
                "description": (
                    "Path to the wiki vault. Defaults to MEMORY_WIKI_PATH env var, "
                    "or /media/racoony-wiki/ if not set."
                ),
            },
            "json_output": {
                "type": "boolean",
                "description": "Return machine-readable JSON instead of a human-readable summary.",
            },
        },
    },
}


WIKI_INGEST = {
    "name": "wiki_ingest",
    "description": (
        "Ingest raw markdown files into the wiki vault. Parses frontmatter, "
        "extracts claims (lines matching claim-like patterns), extracts sources "
        "(markdown links to external URLs), detects the page kind (entity/concept/source), "
        "and writes a properly formatted wiki page. "
        "Useful for importing scraped content, exported notes, or external documents. "
        "Can ingest a single file or a whole directory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Path to the markdown file or directory to ingest. "
                    "Can be absolute or relative to the current working directory."
                ),
            },
            "vault_path": {
                "type": "string",
                "description": (
                    "Path to the wiki vault. Defaults to MEMORY_WIKI_PATH env var, "
                    "or /media/racoony-wiki/ if not set."
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["entity", "concept", "source", "auto"],
                "description": (
                    "Target page kind: 'entity', 'concept', 'source', or 'auto' "
                    "(auto-detect from content). Default: 'auto'."
                ),
            },
            "recursive": {
                "type": "boolean",
                "description": (
                    "If ingesting a directory, process files in subdirectories too. "
                    "Default: True."
                ),
            },
            "force": {
                "type": "boolean",
                "description": (
                    "Overwrite existing pages with the same title. "
                    "If False (default), renames conflicting files."
                ),
            },
            "namespace": {
                "type": "string",
                "description": (
                    "Optional custom ID prefix, e.g. 'project-x'. "
                    "Defaults to the kind-based prefix (entity., concept., etc.)."
                ),
            },
            "json_output": {
                "type": "boolean",
                "description": "Return machine-readable JSON instead of a human-readable summary.",
            },
        },
        "required": ["file_path"],
    },
}


WIKI_APPEND = {
    "name": "wiki_append",
    "description": (
        "Append content to an existing wiki page. "
        "Finds the page by ID, relative path, or basename. "
        "If 'heading' is provided, inserts new content after that heading "
        "(e.g. '## Spark Plugs'). If the heading already exists, content is "
        "added below it (before the next heading). If no heading is provided, "
        "content is appended to the end of the page. "
        "Use after wiki modifications that add new sections to existing pages."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lookup": {
                "type": "string",
                "description": (
                    "Page lookup — can be a relative path (e.g. 'entities/civic.md'), "
                    "a page id (e.g. 'entity.honda-civic-2016-ex-t-maintenance'), "
                    "or a page basename (e.g. 'honda-civic-2016-ex-t-maintenance')."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "The markdown content to append. Can include headings, lists, links, etc. "
                    "No leading newline needed — one is added automatically."
                ),
            },
            "heading": {
                "type": "string",
                "description": (
                    "Optional heading to insert after (e.g. '## Spark Plugs'). "
                    "If the heading exists, content goes after it. "
                    "If it doesn't exist, content is appended at the end. "
                    "Leading '## ' is optional (can pass 'Spark Plugs' directly)."
                ),
            },
            "vault_path": {
                "type": "string",
                "description": (
                    "Path to the wiki vault. Defaults to MEMORY_WIKI_PATH env var, "
                    "or /media/racoony-wiki/ if not set."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "If true, shows what would be written without modifying any files. "
                    "Use this to preview the change before applying it."
                ),
            },
        },
        "required": ["lookup", "content"],
    },
}


WIKI_APPLY = {
    "name": "wiki_apply",
    "description": (
        "Apply mutations to the wiki vault in three modes: "
        "(1) 'synthesis' regenerates synthesis pages from source entity/concept pages; "
        "(2) 'metadata' bulk-updates frontmatter fields across pages "
        "(e.g. set confidence=0.9 on all entity pages matching a query); "
        "(3) 'lint-fix' auto-fixes common lint issues like missing IDs, "
        "missing pageType, missing titles, and broken wikilinks. "
        "All modes support dry_run=true to preview without writing. "
        "CAUTION: lint-fix and metadata modes modify your wiki files — "
        "always run with dry_run=true first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["synthesis", "metadata", "lint-fix"],
                "description": (
                    "Apply mode: 'synthesis' (regenerate syntheses), "
                    "'metadata' (bulk-update frontmatter), "
                    "'lint-fix' (auto-fix lint issues)."
                ),
            },
            "vault_path": {
                "type": "string",
                "description": (
                    "Path to the wiki vault. Defaults to MEMORY_WIKI_PATH env var, "
                    "or /media/racoony-wiki/ if not set."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "Preview the changes without writing anything. "
                    "Always use this first to see what would happen."
                ),
            },
            "target_id": {
                "type": "string",
                "description": (
                    "For mode='synthesis': only regenerate this specific synthesis ID. "
                    "If not provided, regenerates all synthesis pages."
                ),
            },
            "updates": {
                "type": "object",
                "description": (
                    "For mode='metadata': a dict of frontmatter key->value to set. "
                    "Supports dot notation for nested keys (e.g. 'claims[0].status'). "
                    "Example: {\"confidence\": 0.9, \"status\": \"reviewed\"}"
                ),
            },
            "filter_kinds": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "For mode='metadata': only update pages of these kinds "
                    "(e.g. ['entity', 'concept']). If not provided, updates all kinds."
                ),
            },
            "filter_query": {
                "type": "string",
                "description": (
                    "For mode='metadata': only update pages whose title contains this substring."
                ),
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "For mode='lint-fix': only fix issues in these categories "
                    "(e.g. ['structure', 'links']). If not provided, fixes all fixable issues."
                ),
            },
            "json_output": {
                "type": "boolean",
                "description": "Return machine-readable JSON instead of a human-readable summary.",
            },
        },
        "required": ["mode"],
    },
}

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

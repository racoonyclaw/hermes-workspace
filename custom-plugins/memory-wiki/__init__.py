"""memory-wiki — Hermes plugin for wiki vault lint, search, and management.

Register tools:
  - wiki_lint    Run lint checks and write report to reports/lint.md
  - wiki_status  Get vault health summary
  - wiki_search  Search pages by query string
  - wiki_get     Read a specific page by path/id/basename

Vault path is read from MEMORY_WIKI_PATH in ~/.hermes/.env,
falling back to /media/racoony-wiki/.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package can be imported even if dependencies are missing
try:
    from . import schemas as _schemas
    from . import tools as _tools
    from . import vault as _vault
except ImportError as _exc:
    # Provide stub handlers so the plugin still loads
    _schemas = None
    _tools = None
    _vault = None


def register(ctx) -> None:
    """Register memory-wiki tools with the Hermes plugin context."""
    if _schemas is None or _tools is None:
        print(
            "[memory-wiki] Warning: failed to import dependencies. "
            "Install with: pip install pyyaml",
            file=sys.stderr,
        )
        return

    ctx.register_tool("wiki_lint", _schemas.WIKI_LINT, _tools.handle_wiki_lint)
    ctx.register_tool("wiki_status", _schemas.WIKI_STATUS, _tools.handle_wiki_status)
    ctx.register_tool("wiki_search", _schemas.WIKI_SEARCH, _tools.handle_wiki_search)
    ctx.register_tool("wiki_get", _schemas.WIKI_GET, _tools.handle_wiki_get)

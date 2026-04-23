# memory-wiki — Hermes Plugin

A Hermes plugin for linting, searching, and managing an Obsidian-compatible wiki vault.

## Tools

| Tool | Description |
|------|-------------|
| `wiki_lint` | Run full vault lint checks — missing frontmatter, duplicate IDs, broken links, contradicting claims, stale pages. Writes `reports/lint.md` |
| `wiki_status` | Quick health summary — page counts, last modified, missing dirs |
| `wiki_search` | Search pages by title, claim text, or body content |
| `wiki_get` | Read a specific page by path, id, or basename |

## Configuration

Set the vault path in `~/.hermes/.env`:

```
MEMORY_WIKI_PATH=/media/racoony-wiki/
```

If not set, defaults to `/media/racoony-wiki/`.

## Installation

```bash
hermes plugins install racoonyclaw/hermes-workspace --subdir custom-plugins/memory-wiki
hermes plugins enable memory-wiki
```

Or copy the `memory-wiki/` directory to `~/.hermes/plugins/`.

## Architecture

```
memory-wiki/
├── plugin.yaml         # Manifest
├── __init__.py        # register() — wires tools to handlers
├── schemas.py         # Tool schemas (LLM-facing descriptions)
├── tools.py           # Tool handler implementations
├── vault.py           # Config loading, vault status/init
├── query.py           # Page reading, search, get
├── wiki_lint.py       # Core lint logic
├── markdown_utils.py  # Frontmatter parsing, managed blocks
└── claim_health.py    # Freshness, claim clusters
```

## Lint Checks

The lint tool checks for:

- **Structure** — missing `id`, `pageType`, `title` frontmatter; duplicate IDs
- **Links** — broken wikilinks
- **Provenance** — missing `sourceIds` on non-source pages; missing import provenance fields
- **Contradictions** — claim clusters with competing variants; page-level contradictions
- **Open Questions** — pages listing unresolved questions
- **Quality** — low-confidence pages/claims; stale pages (>90 days)

## OpenClaw Compatibility

This plugin is compatible with OpenClaw's `memory-wiki` plugin vault format:
- Reads the same frontmatter schema (`id`, `pageType`, `title`, `sourceIds`, `claims`, `contradictions`, `questions`, `confidence`, `updatedAt`)
- Uses the same managed block markers (`<!-- openclaw:wiki:lint:start -->` etc.)
- Detects the same claim contradiction clusters
- Writes lint results to `reports/lint.md` in the same format

## License

MIT

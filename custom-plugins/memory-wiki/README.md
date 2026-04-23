# memory-wiki — Hermes Plugin

A full-featured Hermes plugin for managing an Obsidian-compatible wiki vault — linting, searching, compiling, ingesting, health-checking, and auto-fixing.

## Tools (8 total)

| Tool | Description |
|------|-------------|
| `wiki_lint` | Run full vault lint checks — missing frontmatter, duplicate IDs, broken wikilinks, contradicting claims, stale pages |
| `wiki_status` | Quick health summary — page counts, last modified, disk space, missing directories |
| `wiki_search` | Search pages by title, claim text, or body content with relevance scoring |
| `wiki_get` | Read a specific page by path, ID, or basename |
| `wiki_compile` | Compile entity and concept pages into synthesis pages — deduplicates claims, resolves contradictions, builds wikilink maps |
| `wiki_doctor` | Comprehensive vault health checks — structure, permissions, broken wikilinks, index consistency, orphan pages, disk space, frontmatter corruption |
| `wiki_ingest` | Parse raw markdown files (scraped content, exported notes) into wiki pages with proper frontmatter, claim IDs, and source attribution |
| `wiki_apply` | Apply three modes of mutations to the vault: `synthesis` (regenerate synthesis pages), `metadata` (bulk frontmatter updates), `lint-fix` (auto-fix lint issues in one step) |

## Quick Start

```bash
# Set vault path
echo "MEMORY_WIKI_PATH=/media/racoony-wiki/" >> ~/.hermes/.env

# Run all lint checks
wiki_lint

# Search the vault
wiki_search query="project status"

# Read a specific page
wiki_get id="my-page-id"

# Compile all synthesis pages
wiki_compile

# Run vault health checks
wiki_doctor

# Auto-fix lint issues in one step (mirrors OpenClaw's `openclaw wiki apply lint`)
wiki_apply mode="lint-fix"

# Preview lint fixes without writing
wiki_apply mode="lint-fix" dry_run=true
```

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
├── plugin.yaml          # Manifest (8 tools, v1.1.0)
├── __init__.py         # register() — wires tools to handlers
├── schemas.py          # Tool schemas (LLM-facing JSON descriptions)
├── tools.py            # Tool handler implementations
├── vault.py            # Config loading, vault status/init
├── query.py            # Page reading, search, get
├── wiki_lint.py        # Core lint logic — 14 issue codes
├── apply.py            # Apply mutations — synthesis, metadata, lint-fix
├── compile.py          # Synthesis compilation engine
├── doctor.py           # Vault health checks (6 categories)
├── ingest.py           # Markdown ingestion and transformation
├── markdown_utils.py   # Frontmatter parsing, managed blocks, wikilink extraction
└── claim_health.py     # Freshness thresholds, claim clusters, contradiction detection
```

## Lint Checks (14 issue codes)

`wiki_lint` detects:

| Code | Category | Description |
|------|----------|-------------|
| `missing-id` | Structure | Page missing `id` frontmatter |
| `missing-page-type` | Structure | Page missing `pageType` |
| `page-type-mismatch` | Structure | Page kind doesn't match `pageType` |
| `missing-title` | Structure | Page missing `title` |
| `broken-wikilink` | Links | Wikilink target doesn't exist |
| `missing-source-id` | Provenance | Non-source page missing `sourceIds` |
| `duplicate-id` | Structure | `id` appears in multiple pages |
| `claim-conflict` | Contradictions | Claim cluster has competing values |
| `page-type-unknown` | Structure | `pageType` not in known set |
| `contradiction-conflict` | Contradictions | Page has contradictory claims |
| `stale-page` | Quality | Page not updated in 90+ days |
| `low-confidence` | Quality | Page or claim has low confidence score |
| `open-question` | Open Questions | Page has unresolved questions |
| `provenance-missing` | Provenance | Page missing import provenance fields |

## Apply Modes

### `wiki_apply mode="synthesis"`

Regenerate synthesis pages from source entity and concept pages.

- Deduplicates claims across source pages
- Resolves contradictions (prefers highest-confidence claim)
- Aggregates sources from all contributing pages
- Resolves `![[wikilinks]]` to full page references
- Uses managed blocks (`<!-- openclaw:wiki:generated:start -->`) to protect generated content

```bash
# Compile all synthesis pages
wiki_apply mode="synthesis"

# Compile a specific synthesis
wiki_apply mode="synthesis" target_id="my-synthesis-id"

# Preview without writing
wiki_apply mode="synthesis" dry_run=true
```

### `wiki_apply mode="metadata"`

Bulk-update frontmatter fields across pages using dot notation.

```bash
# Set a top-level field
wiki_apply mode="metadata" key="confidence" value="0.9"

# Set a nested claim field
wiki_apply mode="metadata" key="claims[0].status" value="supported"

# Filter to pages matching a query
wiki_apply mode="metadata" query="project status" key="updatedAt" value="2024-01-15"
```

### `wiki_apply mode="lint-fix"`

Auto-fix common lint issues — missing IDs, missing page types, missing titles, broken wikilinks. Runs lint inline first, then applies fixes. Matches OpenClaw's `openclaw wiki apply lint` UX.

```bash
# Fix all lint issues
wiki_apply mode="lint-fix"

# Preview fixes without writing
wiki_apply mode="lint-fix" dry_run=true
```

Broken wikilink fixes use 5-tier fuzzy matching:
1. Exact ID match (case-insensitive)
2. Exact basename match
3. Slug match on title
4. Substring match
5. Shared-word similarity (≥50% word overlap)

## wiki_compile

Synthesis compilation engine — aggregates entity and concept pages into synthesis documents.

```bash
# Compile all synthesis targets
wiki_compile

# Compile a specific synthesis
wiki_compile target_id="my-synthesis-id"
```

Features:
- Discovers synthesis targets from `syntheses/` directory and `claimIds` references
- Suggests new synthesis targets when none exist
- Deduplicates claims by text and source
- Resolves contradictions (prefers `supported` status, then highest confidence)
- Builds wikilink maps for cross-reference resolution
- Writes managed blocks protecting generated content from manual edits

## wiki_doctor

Comprehensive vault health diagnostics across 6 categories:

| Category | Checks |
|----------|--------|
| Structure | Vault root exists, is a directory, required subdirs present |
| Permissions | Vault and files are readable/writable |
| Links | Broken wikilinks (scans all pages) |
| Consistency | Index file matches actual pages |
| Orphans | Pages not referenced by any other page |
| Disk | Available disk space >1GB |

```bash
wiki_doctor
```

## wiki_ingest

Parse raw markdown files into wiki-format pages with proper frontmatter.

```bash
# Ingest a single file
wiki_ingest path="/tmp/my-notes.md" kind="entity"

# Auto-detect page kind from content
wiki_ingest path="/tmp/my-notes.md" kind="auto"

# Ingest all markdown files in a directory
wiki_ingest path="/tmp/notes/" kind="auto"
```

Supports:
- Title extraction from first `# Heading` or filename
- Auto-detection of page kind (entity, concept, source)
- Claim extraction from `- claim::` lines
- Source extraction from `- source::` lines
- Slug/ID generation from title
- Frontmatter generation with all required fields

## wiki_search

Full-text search with relevance scoring.

```bash
# Search by claim text or body content
wiki_search query="email workflow"

# Search with stricter matching
wiki_search query="project status" strict=true
```

Scoring: title match (10×), kind match (3×), claims match (2×), body match (1×).

## wiki_status

Quick vault health summary.

```bash
wiki_status
```

Returns: page counts by kind, total pages, last modified, disk space, vault path, missing directories.

## wiki_get

Read a specific page by path, ID, or basename.

```bash
# By ID
wiki_get id="randomstix"

# By basename
wiki_get basename="daily-notes-2024"

# By full path
wiki_get path="entities/randomstix.md"
```

## OpenClaw Compatibility

Fully compatible with OpenClaw's `memory-wiki` vault format:

- Reads the same frontmatter schema (`id`, `pageType`, `title`, `sourceIds`, `claims`, `contradictions`, `questions`, `confidence`, `updatedAt`)
- Uses the same managed block markers:
  - `<!-- openclaw:wiki:generated:start/end -->` — synthesis output
  - `<!-- openclaw:human:start/end -->` — manually-edited sections
  - `<!-- openclaw:wiki:related:start/end -->` — related pages list
  - `<!-- openclaw:wiki:lint:start/end -->` — lint report
- Detects the same claim contradiction clusters
- `wiki_apply --mode lint-fix` mirrors OpenClaw's `openclaw wiki apply lint` in one step

## License

MIT

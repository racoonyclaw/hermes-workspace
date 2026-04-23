"""ingest — Parse raw markdown files into wiki format for the memory-wiki plugin.

Reads arbitrary markdown files (scraped content, exported notes, etc.) and
transforms them into wiki pages with proper frontmatter, claim IDs, and
source attribution.

Inspired by OpenClaw's ingest functionality.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .markdown_utils import (
    OBSIDIAN_LINK_PATTERN,
    MARKDOWN_LINK_PATTERN,
    extract_title_from_markdown,
    infer_wiki_page_kind,
    normalize_string,
    parse_frontmatter,
    render_frontmatter,
    slugify,
)


# ---------------------------------------------------------------------------
# Ingestion modes
# ---------------------------------------------------------------------------

INGESTION_MODES = ("entity", "concept", "source", "auto")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class IngestResult:
    """Result of ingesting a single file."""
    original_path: str
    wiki_path: Optional[str]
    title: str
    id: str
    kind: str
    claims_extracted: int
    sources_extracted: int
    warnings: List[str]
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Title extraction helpers
# ---------------------------------------------------------------------------

def _extract_title(raw: str, fallback_path: str) -> str:
    """Extract title from frontmatter, h1 heading, or filename."""
    fm, body = parse_frontmatter(raw)

    # Try frontmatter title
    if isinstance(fm.get("title"), str) and fm["title"].strip():
        return fm["title"].strip()

    # Try first # heading
    extracted = extract_title_from_markdown(body)
    if extracted:
        return extracted

    # Fall back to filename
    return Path(fallback_path).stem.replace("-", " ").replace("_", " ").title()


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _generate_id(title: str, kind: str, namespace: Optional[str] = None) -> str:
    """Generate a deterministic ID from title and kind."""
    base = f"{kind}." if not namespace else f"{namespace}."
    slug = slugify(title)
    if slug:
        return base + slug
    # Fallback to hash of title
    short_hash = hashlib.sha256(title.encode()).hexdigest()[:8]
    return base + short_hash


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

def _extract_claims(body: str) -> List[dict]:
    """Extract structured claims from markdown body text.

    Looks for patterns like:
    - "- **Claim:** text here"
    - "- The system ... (supported)"
    - Lines that look like factual statements
    """
    claims: List[dict] = []
    lines = body.splitlines()

    for line in lines:
        line = line.strip()
        if not line or not line.startswith("-"):
            continue

        # Remove list marker
        text = line.lstrip("- *").strip()
        if not text:
            continue

        # Skip obvious non-claims (questions, headers, references)
        if text.startswith("#") or text.startswith("http") or text.startswith("*"):
            continue
        if text.endswith("?") or text.startswith("?"):
            continue

        # Detect inline status: (supported), (contested), (refuted), etc.
        status = "supported"
        confidence: Optional[float] = None

        status_match = re.search(r"\((supported|contested|contradicted|refuted|superseded)\)", text, re.I)
        if status_match:
            status = status_match.group(1).lower()
            text = re.sub(r"\((supported|contested|contradicted|refuted|superseded)\)", "", text, flags=re.I).strip()

        # Detect confidence: (confidence: 0.8)
        conf_match = re.search(r"\(confidence:\s*([0-9.]+)\)", text, re.I)
        if conf_match:
            try:
                confidence = float(conf_match.group(1))
                text = re.sub(r"\(confidence:\s*[0-9.]+\)", "", text, flags=re.I).strip()
            except ValueError:
                pass

        if len(text) < 10:
            continue  # Too short to be meaningful

        claim_id = slugify(text[:60])

        claims.append({
            "id": claim_id,
            "text": text,
            "status": status,
            "confidence": confidence,
        })

    return claims


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------

def _extract_sources(body: str) -> List[dict]:
    """Extract markdown links as sources."""
    sources: List[dict] = []

    # Markdown links
    for match in MARKDOWN_LINK_PATTERN.finditer(body):
        url = match.group(1)  # This is the URL for markdown links
        text = match.group(0)

    for match in MARKDOWN_LINK_PATTERN.finditer(body):
        url = match.group(1)
        text = match.group(0)

        # Only external URLs
        if not re.match(r"^[a-z]+://", url):
            continue

        # Skip common non-source URLs
        if any(skip in url for skip in ("localhost", "127.0.0.1", "file://")):
            continue

        # Extract domain as source name
        domain_match = re.search(r"://([^/]+)", url)
        domain = domain_match.group(1) if domain_match else url

        # Generate source id
        source_id = slugify(domain)[:20]

        sources.append({
            "sourceId": source_id,
            "sourceType": "web",
            "sourcePath": url,
            "title": text[:100] if text else domain,
        })

    # Also look for bare URLs
    url_pattern = re.compile(r"(?<![\[\(])(https?://[^\s\)'\"]+)")
    for match in url_pattern.finditer(body):
        url = match.group(1)
        if any(skip in url for skip in ("localhost", "127.0.0.1", "file://")):
            continue

        domain_match = re.search(r"://([^/]+)", url)
        domain = domain_match.group(1) if domain_match else url
        source_id = slugify(domain)[:20]

        # Avoid duplicates
        if not any(s.get("sourcePath") == url for s in sources):
            sources.append({
                "sourceId": source_id,
                "sourceType": "web",
                "sourcePath": url,
                "title": domain,
            })

    return sources


# ---------------------------------------------------------------------------
# Wiki page kind detection
# ---------------------------------------------------------------------------

def _detect_kind(body: str, title: str) -> str:
    """Detect the most likely wiki page kind from content.

    Uses keyword matching on the body text.
    """
    title_lower = title.lower()
    body_lower = body.lower()

    if any(kw in body_lower for kw in ("person", "user", "contact", "randomstix", "racoony")):
        return "entity"
    if any(kw in body_lower for kw in ("concept", "idea", "theory", "pattern", "architecture")):
        return "concept"
    if any(kw in body_lower for kw in ("source", "article", "document", "reference", "paper", "book")):
        return "source"
    if any(kw in body_lower for kw in ("synthesis", "summary", "overview", "report")):
        return "synthesis"

    return "entity"  # Default


# ---------------------------------------------------------------------------
# Content cleaning
# ---------------------------------------------------------------------------

def _clean_body(body: str) -> str:
    """Clean body text for wiki ingestion.

    Removes excessive blank lines, normalizes whitespace, strips
    some common non-content patterns.
    """
    lines = body.splitlines()

    # Remove lines that are only whitespace
    lines = [l.rstrip() for l in lines]

    # Collapse more than 2 consecutive blank lines to 2
    result: List[str] = []
    blank_count = 0
    for line in lines:
        if not line.strip():
            blank_count += 1
            if blank_count <= 2:
                result.append("")
        else:
            blank_count = 0
            result.append(line)

    return "\n".join(result).strip()


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def ingest_file(
    file_path: str,
    vault_path: Path,
    kind: str = "auto",
    force: bool = False,
    namespace: Optional[str] = None,
) -> IngestResult:
    """Ingest a single markdown file into the wiki vault.

    *file_path* can be an absolute path or a path relative to the current dir.
    *kind* determines the target directory: entity, concept, source, or auto.
    *force* overwrites existing pages with the same ID.
    *namespace* sets a custom ID prefix.

    Returns an IngestResult describing what was created.
    """
    src = Path(file_path)
    if not src.exists():
        return IngestResult(
            original_path=str(src),
            wiki_path=None,
            title="",
            id="",
            kind="",
            claims_extracted=0,
            sources_extracted=0,
            warnings=[],
            error=f"File not found: {file_path}",
        )

    try:
        raw = src.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return IngestResult(
            original_path=str(src),
            wiki_path=None,
            title="",
            id="",
            kind="",
            claims_extracted=0,
            sources_extracted=0,
            warnings=[],
            error=f"Cannot read file: {e}",
        )

    # Parse
    fm, body = parse_frontmatter(raw)
    title = _extract_title(raw, str(src))
    body = _clean_body(body)

    # Detect kind
    if kind == "auto":
        detected = _detect_kind(body, title)
        kind = detected

    # Generate ID
    id_val = _generate_id(title, kind, namespace)

    # Determine output path
    out_dir = vault_path / f"{kind}s"
    out_path = out_dir / f"{slugify(title)}.md"

    # Check for existing file
    warnings: List[str] = []
    if out_path.exists() and not force:
        # Try to avoid collision
        counter = 1
        while out_path.exists():
            out_path = out_dir / f"{slugify(title)}-{counter}.md"
            counter += 1
            if counter > 100:
                return IngestResult(
                    original_path=str(src),
                    wiki_path=None,
                    title=title,
                    id=id_val,
                    kind=kind,
                    claims_extracted=0,
                    sources_extracted=0,
                    warnings=warnings,
                    error="Too many filename collisions",
                )
        warnings.append(f"File existed, renamed to {out_path.name}")

    # Extract claims and sources
    claims = _extract_claims(body)
    sources = _extract_sources(body)

    # Build frontmatter
    source_ids = [s["sourceId"] for s in sources if s.get("sourceId")]

    new_fm = {
        "pageType": kind,
        "id": id_val,
        "title": title,
        "provenanceMode": "unsafe-local",
        "sourcePath": str(src.resolve()),
        "unsafeLocalConfiguredPath": str(vault_path),
        "unsafeLocalRelativePath": str(src),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }

    if source_ids:
        new_fm["sourceIds"] = source_ids
    if sources:
        new_fm["sources"] = sources
    if claims:
        new_fm["claims"] = claims

    # Render
    content = render_frontmatter(new_fm, body)

    # Write
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    except OSError as e:
        return IngestResult(
            original_path=str(src),
            wiki_path=None,
            title=title,
            id=id_val,
            kind=kind,
            claims_extracted=len(claims),
            sources_extracted=len(sources),
            warnings=warnings,
            error=f"Cannot write file: {e}",
        )

    return IngestResult(
        original_path=str(src),
        wiki_path=str(out_path.relative_to(vault_path)),
        title=title,
        id=id_val,
        kind=kind,
        claims_extracted=len(claims),
        sources_extracted=len(sources),
        warnings=warnings,
    )


def ingest_directory(
    dir_path: Path,
    vault_path: Path,
    kind: str = "auto",
    recursive: bool = True,
    force: bool = False,
    namespace: Optional[str] = None,
) -> List[IngestResult]:
    """Ingest all .md files from a directory into the wiki vault.

    If *recursive* is True, processes subdirectories too.
    Returns a list of IngestResults, one per file.
    """
    results: List[IngestResult] = []

    pattern = "**/*.md" if recursive else "*.md"
    for md_file in dir_path.glob(pattern):
        if md_file.name.startswith("."):
            continue
        result = ingest_file(
            str(md_file),
            vault_path,
            kind=kind,
            force=force,
            namespace=namespace,
        )
        results.append(result)

    return results

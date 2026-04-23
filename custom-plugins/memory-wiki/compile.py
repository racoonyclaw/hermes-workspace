"""compile — Synthesis compilation for the memory-wiki plugin.

Aggregates entity and concept pages into synthesis pages, deduplicates
claims, resolves contradictions, and writes structured synthesis documents.

Ported from OpenClaw's compile.ts.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from .claim_health import (
    ClaimContradictionCluster,
    WikiPageSummary,
    build_claim_clusters,
)
from .markdown_utils import (
    GENERATED_END,
    GENERATED_START,
    HUMAN_END,
    HUMAN_START,
    extract_wikilinks,
    infer_wiki_page_kind,
    normalize_string,
    normalize_string_list,
    parse_frontmatter,
    render_frontmatter,
    replace_managed_block,
    slugify,
)
from .query import read_wiki_pages


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CompiledClaim:
    """A claim as it appears in a compiled synthesis page."""
    text: str
    sources: List[dict]  # [{path, title, pageId}]
    status: str = "supported"
    confidence: Optional[float] = None
    claim_id: Optional[str] = None
    variant_count: int = 1
    contributing_pages: List[str] = field(default_factory=list)


@dataclass
class SynthesisTarget:
    """A synthesis page and the source pages that feed into it."""
    synthesis_path: Path
    synthesis_id: str
    title: str
    source_pages: List[WikiPageSummary] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)


@dataclass
class CompilationResult:
    """Result of compiling a synthesis page."""
    synthesis_path: str
    synthesis_id: str
    title: str
    claims_included: int
    contradictions_resolved: int
    sources_aggregated: int
    written: bool
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Synthesis target discovery
# ---------------------------------------------------------------------------

def discover_synthesis_targets(vault_path: Path) -> List[SynthesisTarget]:
    """Discover existing synthesis pages and their potential source pages.

    Reads all existing synthesis pages and tries to match them to source
    entity/concept pages based on wikilinks and topic overlap.
    """
    pages = read_wiki_pages(vault_path)
    pages_by_id: Dict[str, WikiPageSummary] = {p.id: p for p in pages if p.id}
    pages_by_path: Dict[str, WikiPageSummary] = {p.relative_path: p for p in pages}

    entities_concepts = [p for p in pages if p.kind in ("entity", "concept")]
    syntheses = [p for p in pages if p.kind == "synthesis"]

    targets: List[SynthesisTarget] = []

    for synth in syntheses:
        synth_file = vault_path / synth.relative_path
        if not synth_file.exists():
            continue

        # Determine topics: from frontmatter topics, or infer from wikilinks
        frontmatter, _ = parse_frontmatter(synth_file.read_text(encoding="utf-8"))
        topics = normalize_string_list(frontmatter.get("topics"))

        if not topics:
            # Infer from wikilinks in the synthesis body
            _, body = parse_frontmatter(synth_file.read_text(encoding="utf-8"))
            linked = extract_wikilinks(body)
            topics = [ln for ln in linked if not ln.endswith(".md")]

        # Find source pages: entities/concepts linked from the synthesis
        source_pages: List[WikiPageSummary] = []
        for link_target in topics:
            # Try as a path
            if link_target in pages_by_path:
                source_pages.append(pages_by_path[link_target])
            # Try as an id
            elif link_target in pages_by_id:
                source_pages.append(pages_by_id[link_target])
            # Try by slugifying
            else:
                slug = slugify(link_target)
                for p in entities_concepts:
                    if slugify(p.title) == slug or slugify(p.id or "") == slug:
                        source_pages.append(p)
                        break

        # Fall back: use all entities/concepts if no sources found
        if not source_pages:
            source_pages = entities_concepts

        targets.append(SynthesisTarget(
            synthesis_path=synth_file,
            synthesis_id=synth.id or slugify(synth.title),
            title=synth.title,
            source_pages=source_pages,
            topics=topics,
        ))

    return targets


def suggest_synthesis_targets(vault_path: Path) -> List[SynthesisTarget]:
    """Suggest synthesis targets based on claim clusters and topic groupings.

    Groups entity/concept pages by their claim clusters and creates synthesis
    targets for each major cluster that doesn't have one yet.
    """
    pages = read_wiki_pages(vault_path)
    entities_concepts = [p for p in pages if p.kind in ("entity", "concept")]

    # Group pages by slugified title (topic proxy)
    by_topic: Dict[str, List[WikiPageSummary]] = defaultdict(list)
    for page in entities_concepts:
        key = slugify(page.title)
        if key:
            by_topic[key].append(page)

    # Pages with claim clusters (multiple pages referencing same claimId)
    clusters = build_claim_clusters(entities_concepts)
    clustered_ids: Set[str] = set()
    for cluster in clusters:
        clustered_ids.add(cluster.key)

    # Build targets for pages that have claim clusters
    targets: List[SynthesisTarget] = []
    seen_ids: Set[str] = set()

    for cluster in clusters:
        cluster_key = cluster.key
        # Find pages contributing to this cluster
        contributing: List[WikiPageSummary] = []
        for entry in cluster.entries:
            for page in entities_concepts:
                if page.relative_path == entry.page_path:
                    contributing.append(page)
                    break

        if not contributing:
            continue

        # Pick the first page's title as the synthesis title
        primary = contributing[0]
        title = primary.title.title()

        # Skip if already have a synthesis for this cluster
        if cluster_key in seen_ids:
            continue
        seen_ids.add(cluster_key)

        synth_id = f"synthesis.{slugify(cluster_key)}"
        synth_path = vault_path / "syntheses" / f"{slugify(cluster_key)}.md"

        targets.append(SynthesisTarget(
            synthesis_path=synth_path,
            synthesis_id=synth_id,
            title=title,
            source_pages=contributing,
            topics=[cluster_key],
        ))

    # Also create targets for major topics not yet covered
    existing_synth_ids = {t.synthesis_id for t in targets}
    for topic, topic_pages in by_topic.items():
        if len(topic_pages) < 2:
            continue
        synth_id = f"synthesis.{topic}"
        if synth_id in existing_synth_ids:
            continue

        synth_path = vault_path / "syntheses" / f"{topic}.md"
        targets.append(SynthesisTarget(
            synthesis_path=synth_path,
            synthesis_id=synth_id,
            title=topic_pages[0].title.title(),
            source_pages=topic_pages,
            topics=[topic],
        ))

    return targets


# ---------------------------------------------------------------------------
# Claim compilation
# ---------------------------------------------------------------------------

def _normalize_for_comparison(text: str) -> str:
    """Normalize claim text for deduplication comparison."""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def compile_claims(source_pages: List[WikiPageSummary]) -> List[CompiledClaim]:
    """Compile and deduplicate claims from source pages.

    Groups identical claims across pages, tracks all sources, detects
    status conflicts, and returns a deduplicated list.
    """
    by_normalized: Dict[str, CompiledClaim] = {}

    for page in source_pages:
        for claim in page.claims:
            key = _normalize_for_comparison(claim.text)

            if key not in by_normalized:
                by_normalized[key] = CompiledClaim(
                    text=claim.text,
                    sources=[],
                    status=claim.status,
                    confidence=claim.confidence,
                    claim_id=claim.id,
                    variant_count=1,
                    contributing_pages=[],
                )

            existing = by_normalized[key]

            # Track source
            existing.sources.append({
                "path": page.relative_path,
                "title": page.title,
                "pageId": page.id,
            })

            # Track contributing page
            if page.relative_path not in existing.contributing_pages:
                existing.contributing_pages.append(page.relative_path)

            # Track variant count
            if claim.text != existing.text:
                existing.variant_count += 1

            # Track status conflict (contested vs supported)
            if claim.status in ("contested", "contradicted", "refuted"):
                # Downgrade if any source contests
                if existing.status not in ("contested", "contradicted", "refuted"):
                    existing.status = claim.status

            # Boost confidence (average)
            if claim.confidence is not None:
                if existing.confidence is None:
                    existing.confidence = claim.confidence
                else:
                    existing.confidence = (existing.confidence + claim.confidence) / 2

    return list(by_normalized.values())


# ---------------------------------------------------------------------------
# Synthesis body rendering
# ---------------------------------------------------------------------------

def _render_synthesis_body(
    claims: List[CompiledClaim],
    source_pages: List[WikiPageSummary],
    topics: List[str],
    claim_clusters: List[ClaimContradictionCluster],
) -> str:
    """Render the body of a synthesis page."""
    lines: List[str] = []

    # Introduction
    if topics:
        topics_str = ", ".join(f"[[{t}]]" for t in topics)
        lines.append(f"This synthesis covers: {topics_str}.\n")

    # Claim clusters (grouped by status)
    if claims:
        lines.append("## Claims\n")
        supported = [c for c in claims if c.status == "supported"]
        contested = [c for c in claims if c.status in ("contested", "contradicted", "refuted")]

        if supported:
            lines.append("### Supported\n")
            for claim in supported:
                sources_str = ", ".join(
                    f"[[{s['path'].replace('.md', '')}|{s['title']}]]"
                    for s in claim.sources
                )
                conf_str = f" (confidence: {claim.confidence:.0%})" if claim.confidence else ""
                lines.append(f"- {claim.text}{conf_str}\n  - Sources: {sources_str}")

        if contested:
            lines.append("\n### Contested / Refuted\n")
            for claim in contested:
                sources_str = ", ".join(
                    f"[[{s['path'].replace('.md', '')}|{s['title']}]]"
                    for s in claim.sources
                )
                lines.append(f"- ~~{claim.text}~~ (status: {claim.status})")
                lines.append(f"  - Sources: {sources_str}")

    # Contradiction clusters
    if claim_clusters:
        lines.append("\n## Contradictions\n")
        for cluster in claim_clusters:
            lines.append(f"### {cluster.label}\n")
            for entry in cluster.entries:
                lines.append(f"- [[{entry.page_path.replace('.md', '')}]]: {entry.text}")
            lines.append("")

    # Open questions (claims without evidence)
    questions = [c for c in claims if len(c.sources) == 0]
    if questions:
        lines.append("\n## Open Questions\n")
        for q in questions:
            lines.append(f"- {q.text}")
        lines.append("")

    # Related entities and concepts
    related = list({p.relative_path for p in source_pages})
    if related:
        lines.append("## Related\n")
        for rel_path in sorted(related):
            slug = rel_path.replace(".md", "")
            lines.append(f"- [[{slug}]]")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full compilation
# ---------------------------------------------------------------------------

def compile_synthesis(
    target: SynthesisTarget,
    vault_path: Path,
    dry_run: bool = False,
) -> CompilationResult:
    """Compile a single synthesis page from its source pages.

    If *dry_run* is True, does not write anything — returns what would be written.
    """
    try:
        source_pages = target.source_pages
        if not source_pages:
            return CompilationResult(
                synthesis_path=str(target.synthesis_path),
                synthesis_id=target.synthesis_id,
                title=target.title,
                claims_included=0,
                contradictions_resolved=0,
                sources_aggregated=0,
                written=False,
                error="No source pages available",
            )

        # Compile claims from all sources
        compiled_claims = compile_claims(source_pages)

        # Get claim clusters for contradiction tracking
        claim_clusters = build_claim_clusters(source_pages)

        # Build frontmatter
        all_source_ids = list({
            sid
            for page in source_pages
            for sid in page.source_ids
        })
        all_claim_ids = [c.claim_id for c in compiled_claims if c.claim_id]

        frontmatter = {
            "pageType": "synthesis",
            "id": target.synthesis_id,
            "title": target.title,
            "topics": target.topics,
            "sourceIds": sorted(all_source_ids),
            "claims": [
                {
                    "id": c.claim_id,
                    "text": c.text,
                    "status": c.status,
                    "confidence": c.confidence,
                    "evidence": [
                        {"sourceId": s["pageId"], "path": s["path"]}
                        for s in c.sources
                    ],
                }
                for c in compiled_claims
            ],
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }

        # Build body
        body = _render_synthesis_body(
            compiled_claims,
            source_pages,
            target.topics,
            claim_clusters,
        )

        # Wrap in managed blocks
        managed_body = (
            f"{GENERATED_START}\n\n"
            f"{body}\n\n"
            f"{GENERATED_END}\n\n"
            f"{HUMAN_START}\n\n"
            f"<!-- Human notes go here -->\n\n"
            f"{HUMAN_END}"
        )

        content = render_frontmatter(frontmatter, managed_body)

        if dry_run:
            return CompilationResult(
                synthesis_path=str(target.synthesis_path),
                synthesis_id=target.synthesis_id,
                title=target.title,
                claims_included=len(compiled_claims),
                contradictions_resolved=len(claim_clusters),
                sources_aggregated=len(source_pages),
                written=False,
            )

        # Write the synthesis page
        target.synthesis_path.parent.mkdir(parents=True, exist_ok=True)
        target.synthesis_path.write_text(content, encoding="utf-8")

        return CompilationResult(
            synthesis_path=str(target.synthesis_path),
            synthesis_id=target.synthesis_id,
            title=target.title,
            claims_included=len(compiled_claims),
            contradictions_resolved=len(claim_clusters),
            sources_aggregated=len(source_pages),
            written=True,
        )

    except Exception as e:
        return CompilationResult(
            synthesis_path=str(target.synthesis_path),
            synthesis_id=target.synthesis_id,
            title=target.title,
            claims_included=0,
            contradictions_resolved=0,
            sources_aggregated=0,
            written=False,
            error=str(e),
        )


def compile_all(
    vault_path: Path,
    dry_run: bool = False,
) -> List[CompilationResult]:
    """Compile all synthesis pages that have source pages.

    If *dry_run* is True, does not write anything.
    """
    targets = discover_synthesis_targets(vault_path)
    if not targets:
        targets = suggest_synthesis_targets(vault_path)

    results: List[CompilationResult] = []
    for target in targets:
        result = compile_synthesis(target, vault_path, dry_run=dry_run)
        results.append(result)

    return results

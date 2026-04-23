"""claim_health — Wiki freshness, claim health, and contradiction clustering.

Ported from OpenClaw's memory-wiki extension (claim-health.ts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WIKI_AGING_DAYS = 30
WIKI_STALE_DAYS = 90

# Claim statuses that indicate a claim is contested/contradicted
CONTESTED_CLAIM_STATUSES = frozenset({
    "contested",
    "contradicted",
    "refuted",
    "superseded",
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WikiFreshness:
    level: str  # "fresh" | "aging" | "stale" | "unknown"
    reason: str
    days_since_touch: Optional[int] = None
    last_touched_at: Optional[str] = None


@dataclass
class WikiClaimEvidence:
    source_id: Optional[str] = None
    path: Optional[str] = None
    lines: Optional[str] = None
    weight: Optional[float] = None
    note: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class WikiClaim:
    id: Optional[str] = None
    text: str = ""
    status: str = "supported"
    confidence: Optional[float] = None
    evidence: list[WikiClaimEvidence] = field(default_factory=list)
    updated_at: Optional[str] = None


@dataclass
class WikiPageSummary:
    absolute_path: str = ""
    relative_path: str = ""
    kind: str = ""  # entity | concept | source | synthesis | report
    title: str = ""
    id: Optional[str] = None
    page_type: Optional[str] = None
    source_ids: list[str] = field(default_factory=list)
    link_targets: list[str] = field(default_factory=list)
    claims: list[WikiClaim] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    confidence: Optional[float] = None
    source_type: Optional[str] = None
    provenance_mode: Optional[str] = None
    source_path: Optional[str] = None
    bridge_relative_path: Optional[str] = None
    bridge_workspace_dir: Optional[str] = None
    unsafe_local_configured_path: Optional[str] = None
    unsafe_local_relative_path: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class WikiClaimHealth:
    key: str
    page_path: str
    page_title: str
    page_id: Optional[str] = None
    claim_id: Optional[str] = None
    text: str = ""
    status: str = "supported"
    confidence: Optional[float] = None
    evidence_count: int = 0
    missing_evidence: bool = True
    freshness: WikiFreshness = field(default_factory=lambda: WikiFreshness("unknown", "uninitialized"))


@dataclass
class ClaimContradictionCluster:
    key: str
    label: str
    entries: list[WikiClaimHealth] = field(default_factory=list)


@dataclass
class PageContradictionCluster:
    key: str
    label: str
    entries: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Timestamp utilities
# ---------------------------------------------------------------------------

_MS_PER_DAY = 24 * 60 * 60 * 1000


def _parse_timestamp(value: Optional[str]) -> Optional[int]:
    """Parse an ISO timestamp string to epoch ms, or None if invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _clamp_days(days: float) -> int:
    return max(0, int(days))


# ---------------------------------------------------------------------------
# Freshness assessment
# ---------------------------------------------------------------------------

def assess_freshness(timestamp: Optional[str], now: Optional[datetime] = None) -> WikiFreshness:
    """Assess page/claim freshness based on updatedAt timestamp.

    - Fresh: touched within WIKI_AGING_DAYS (30)
    - Aging: touched within WIKI_STALE_DAYS (90)
    - Stale: touched more than WIKI_STALE_DAYS ago
    - Unknown: no timestamp
    """
    if now is None:
        now = datetime.now(timezone.utc)

    ms = _parse_timestamp(timestamp)
    if ms is None or not timestamp:
        return WikiFreshness(level="unknown", reason="missing updatedAt")

    now_ms = int(now.timestamp() * 1000)
    days_since = _clamp_days((now_ms - ms) / _MS_PER_DAY)

    level: str
    if days_since >= WIKI_STALE_DAYS:
        level = "stale"
    elif days_since >= WIKI_AGING_DAYS:
        level = "aging"
    else:
        level = "fresh"

    return WikiFreshness(
        level=level,
        reason=f"last touched {timestamp}",
        days_since_touch=days_since,
        last_touched_at=timestamp,
    )


def resolve_latest_timestamp(candidates: list[Optional[str]]) -> Optional[str]:
    """Return the most recent non-null timestamp from candidates."""
    best: Optional[str] = None
    best_ms = -1
    for candidate in candidates:
        if not candidate:
            continue
        ms = _parse_timestamp(candidate)
        if ms is not None and ms > best_ms:
            best_ms = ms
            best = candidate
    return best


# ---------------------------------------------------------------------------
# Claim normalization
# ---------------------------------------------------------------------------

def normalize_claim_status(status: Optional[str]) -> str:
    """Normalize claim status to lowercase, default to 'supported'."""
    if not status:
        return "supported"
    normalized = status.strip().lower()
    return normalized if normalized else "supported"


def is_claim_contested(status: Optional[str]) -> bool:
    """Return True if claim status indicates the claim is contested."""
    return normalize_claim_status(status) in CONTESTED_CLAIM_STATUSES


# ---------------------------------------------------------------------------
# Claim health construction
# ---------------------------------------------------------------------------

def build_claim_health(
    page: WikiPageSummary,
    claim: WikiClaim,
    index: int,
    now: Optional[datetime] = None,
) -> WikiClaimHealth:
    """Build a WikiClaimHealth record from a page and its claim."""
    claim_id = claim.id.strip() if claim.id else None
    latest_ts = resolve_latest_timestamp([
        claim.updated_at,
        page.updated_at,
        *[e.updated_at for e in claim.evidence if e.updated_at],
    ])
    freshness = assess_freshness(latest_ts, now)
    return WikiClaimHealth(
        key=f"{page.relative_path}#{claim_id or f'claim-{index + 1}'}",
        page_path=page.relative_path,
        page_title=page.title,
        page_id=page.id,
        claim_id=claim_id,
        text=claim.text,
        status=normalize_claim_status(claim.status),
        confidence=claim.confidence,
        evidence_count=len(claim.evidence),
        missing_evidence=len(claim.evidence) == 0,
        freshness=freshness,
    )


def collect_claim_health(pages: list[WikiPageSummary], now: Optional[datetime] = None) -> list[WikiClaimHealth]:
    """Collect health records for all claims across all pages."""
    health: list[WikiClaimHealth] = []
    for page in pages:
        for idx, claim in enumerate(page.claims):
            health.append(build_claim_health(page, claim, idx, now))
    return health


# ---------------------------------------------------------------------------
# Claim contradiction clusters
# ---------------------------------------------------------------------------

def _normalize_claim_text_key(text: str) -> str:
    """Normalize claim text to a comparison key for contradiction detection.

    Lowercase, collapse whitespace, strip punctuation for comparison.
    """
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_cluster_key(text: str) -> str:
    """Normalize text to a comparison key for page contradiction notes.

    Like claim text key but also removes non-Latin characters.
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def build_claim_clusters(pages: list[WikiPageSummary], now: Optional[datetime] = None) -> list[ClaimContradictionCluster]:
    """Group claims by claimId and detect contradiction clusters.

    A contradiction cluster is a group of 2+ claims with the same claimId
    but different text or status — indicating competing variants across pages.
    """
    health = collect_claim_health(pages, now)

    by_id: dict[str, list[WikiClaimHealth]] = {}
    for h in health:
        if h.claim_id:
            by_id.setdefault(h.claim_id, []).append(h)

    clusters: list[ClaimContradictionCluster] = []
    for claim_id, entries in by_id.items():
        if len(entries) < 2:
            continue

        distinct_texts = {_normalize_claim_text_key(e.text) for e in entries}
        distinct_statuses = {e.status for e in entries}

        # Skip if nothing actually varies
        if len(distinct_texts) < 2 and len(distinct_statuses) < 2:
            continue

        cluster = ClaimContradictionCluster(
            key=claim_id,
            label=claim_id,
            entries=sorted(entries, key=lambda e: e.page_path),
        )
        clusters.append(cluster)

    return sorted(clusters, key=lambda c: c.label)


def build_page_contradiction_clusters(pages: list[WikiPageSummary]) -> list[PageContradictionCluster]:
    """Group pages by their contradiction notes text.

    Pages with the same contradiction note text form a cluster,
    indicating the same issue is flagged across multiple pages.
    """
    by_note: dict[str, list[dict]] = {}
    for page in pages:
        for note in page.contradictions:
            key = _normalize_cluster_key(note)
            if not key:
                continue
            by_note.setdefault(key, []).append({
                "page_path": page.relative_path,
                "page_title": page.title,
                "page_id": page.id,
                "note": note,
            })

    clusters: list[PageContradictionCluster] = []
    for key, entries in by_note.items():
        if not entries:
            continue
        clusters.append(PageContradictionCluster(
            key=key,
            label=entries[0]["note"] if entries else key,
            entries=sorted(entries, key=lambda e: e["page_path"]),
        ))

    return sorted(clusters, key=lambda c: c.label)

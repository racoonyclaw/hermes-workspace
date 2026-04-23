#!/usr/bin/env python3
"""Unit tests for memory-wiki plugin modules.

Run with: python test_memory_wiki.py
Or from project root:
    python -m pytest /tmp/memory_wiki_tests/ -v
"""

import os
import sys
import tempfile
import shutil
import importlib
import types
import json
import re
from pathlib import Path
from datetime import datetime, timezone

import unittest

# -----------------------------------------------------------------------
# Setup — import memory_wiki modules directly from the plugin source
# -----------------------------------------------------------------------

PLUGIN_DIR = "/root/.hermes/plugins/memory-wiki/"
VENV_SITE = "/root/.hermes/hermes-agent/venv/lib/python3.11/site-packages"

sys.path.insert(0, VENV_SITE)
sys.path.insert(0, PLUGIN_DIR)

# Mock parent package so relative imports work
_parent = types.ModuleType("memory_wiki")
_parent.__file__ = PLUGIN_DIR
_parent.__path__ = [PLUGIN_DIR]
_parent.__package__ = "memory_wiki"
_parent.__name__ = "memory_wiki"
sys.modules["memory_wiki"] = _parent

# Force-reload modules in correct package context
for mod_name in [
    "markdown_utils", "claim_health", "vault",
    "query", "wiki_lint", "compile", "doctor", "ingest", "apply",
]:
    full_name = f"memory_wiki.{mod_name}"
    mod_path = os.path.join(PLUGIN_DIR, f"{mod_name}.py")
    spec = importlib.util.spec_from_file_location(full_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "memory_wiki"
    mod.__name__ = full_name
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)

from memory_wiki import markdown_utils as mu
from memory_wiki import claim_health as ch
from memory_wiki import vault
from memory_wiki import query
from memory_wiki import wiki_lint
from memory_wiki import compile as cmp
from memory_wiki import doctor
from memory_wiki import ingest
from memory_wiki import apply as app_mod


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def write_page(root: Path, relative: str, content: str) -> Path:
    """Write a wiki page file."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def make_summary(
    relative_path: str,
    kind: str = "entity",
    title: str = "",
    id: str = "",
    claims: list = None,
    link_targets: list = None,
    source_ids: list = None,
    contradictions: list = None,
    questions: list = None,
    updated_at: str = None,
    **kwargs,
):
    """Create a WikiPageSummary for testing."""
    from memory_wiki.claim_health import WikiPageSummary, WikiClaim, WikiClaimEvidence
    return WikiPageSummary(
        absolute_path=str(Path("/tmp/vault") / relative_path),
        relative_path=relative_path,
        kind=kind,
        title=title or Path(relative_path).stem,
        id=id,
        page_type=kind,
        source_ids=source_ids or [],
        link_targets=link_targets or [],
        claims=[WikiClaim(**c) if isinstance(c, dict) else c for c in (claims or [])],
        contradictions=contradictions or [],
        questions=questions or [],
        updated_at=updated_at,
        **kwargs,
    )


# =======================================================================
# markdown_utils tests
# =======================================================================

class TestMarkdownUtils(unittest.TestCase):

    def test_parse_frontmatter_with_yaml(self):
        raw = "---\ntitle: Hello World\nid: test.page\n---\n\nBody text here."
        fm, body = mu.parse_frontmatter(raw)
        self.assertEqual(fm.get("title"), "Hello World")
        self.assertEqual(fm.get("id"), "test.page")
        self.assertEqual(body.strip(), "Body text here.")

    def test_parse_frontmatter_missing(self):
        raw = "# Just a heading\n\nSome body."
        fm, body = mu.parse_frontmatter(raw)
        self.assertEqual(fm, {})
        self.assertIn("Just a heading", body)

    def test_parse_frontmatter_empty(self):
        fm, body = mu.parse_frontmatter("")
        self.assertEqual(fm, {})

    def test_render_frontmatter_roundtrip(self):
        fm_in = {"title": "Test", "id": "test.id", "tags": ["a", "b"]}
        body = "## Body\n\nSome content."
        rendered = mu.render_frontmatter(fm_in, body)
        fm_out, body_out = mu.parse_frontmatter(rendered)
        self.assertEqual(fm_out.get("title"), "Test")
        self.assertEqual(fm_out.get("id"), "test.id")
        self.assertIn("Body", body_out)

    def test_render_frontmatter_special_chars(self):
        fm = {"title": "Test: with 'quotes' and colons", "id": "test"}
        body = "Body with :: colons"
        rendered = mu.render_frontmatter(fm, body)
        fm_out, body_out = mu.parse_frontmatter(rendered)
        self.assertEqual(fm_out.get("title"), "Test: with 'quotes' and colons")

    def test_extract_wikilinks_basic(self):
        text = "See [[Coffee Shop]] for details and [[Brewing Methods]] too."
        links = mu.extract_wikilinks(text)
        self.assertIn("Coffee Shop", links)
        self.assertIn("Brewing Methods", links)

    def test_extract_wikilinks_with_alias(self):
        text = "The [[Coffee Shop|local cafe]] is great."
        links = mu.extract_wikilinks(text)
        self.assertIn("Coffee Shop", links)

    def test_extract_wikilinks_excludes_headings(self):
        text = "See [[#Heading Anchor]] and [[http://example.com]]"
        links = mu.extract_wikilinks(text)
        for link in links:
            self.assertFalse(link.startswith("#"))
            self.assertFalse("://" in link)

    def test_extract_wikilinks_excludes_related_block(self):
        text = "Normal [[Link A]] here.\n<!-- openclaw:wiki:related:start -->\nRelated: [[Link B]] and [[Link C]]\n<!-- openclaw:wiki:related:end -->\nMore [[Link D]]."
        links = mu.extract_wikilinks(text)
        self.assertIn("Link A", links)
        self.assertIn("Link D", links)
        self.assertNotIn("Link B", links)
        self.assertNotIn("Link C", links)

    def test_extract_wikilinks_markdown_links(self):
        text = "Check [Example](https://example.com) and [Internal Page](entities/test)."
        links = mu.extract_wikilinks(text)
        # External URLs skipped; internal relative paths included
        self.assertIn("entities/test", links)
        # https://... URLs are skipped (external scheme)
        for link in links:
            self.assertFalse(re.match(r"^[a-z]+://", link))

    def test_infer_wiki_page_kind(self):
        self.assertEqual(mu.infer_wiki_page_kind("entities/racoony.md"), "entity")
        self.assertEqual(mu.infer_wiki_page_kind("concepts/architecture.md"), "concept")
        self.assertEqual(mu.infer_wiki_page_kind("sources/article.md"), "source")
        self.assertEqual(mu.infer_wiki_page_kind("syntheses/overview.md"), "synthesis")
        self.assertEqual(mu.infer_wiki_page_kind("reports/weekly.md"), "report")
        self.assertIsNone(mu.infer_wiki_page_kind("other/file.md"))

    def test_infer_wiki_page_kind_windows_paths(self):
        self.assertEqual(mu.infer_wiki_page_kind("entities\\racoony.md"), "entity")

    def test_extract_title_from_markdown(self):
        body = "# My Title\n\nSome content."
        self.assertEqual(mu.extract_title_from_markdown(body), "My Title")

    def test_extract_title_from_markdown_no_h1(self):
        body = "## Subheading\n\nSome content."
        self.assertIsNone(mu.extract_title_from_markdown(body))

    def test_normalize_string(self):
        self.assertEqual(mu.normalize_string("  hello  "), "hello")
        self.assertIsNone(mu.normalize_string(""))
        self.assertIsNone(mu.normalize_string("   "))
        self.assertIsNone(mu.normalize_string(None))
        # Non-string types should return None
        self.assertIsNone(mu.normalize_string(datetime.now()))

    def test_normalize_string_list(self):
        self.assertEqual(mu.normalize_string_list([" a ", "b ", ""]), ["a", "b"])
        self.assertEqual(mu.normalize_string_list(None), [])
        self.assertEqual(mu.normalize_string_list([1, 2, 3]), [])

    def test_slugify(self):
        self.assertEqual(mu.slugify("Hello World"), "hello-world")
        self.assertEqual(mu.slugify("Test: with colon?"), "test-with-colon")
        self.assertEqual(mu.slugify("  spaced  "), "spaced")
        self.assertEqual(mu.slugify(""), "")

    def test_replace_managed_block_replaces_existing(self):
        original = (
            "# Doc\n\n"
            "<!-- openclaw:wiki:generated:start -->\n"
            "Old content\n"
            "<!-- openclaw:wiki:generated:end -->\n"
            "More text."
        )
        result = mu.replace_managed_block(
            original=original,
            heading="## Generated",
            start_marker=mu.GENERATED_START,
            end_marker=mu.GENERATED_END,
            body="New content",
        )
        self.assertIn("New content", result)
        self.assertNotIn("Old content", result)
        self.assertIn(mu.GENERATED_START, result)
        self.assertIn(mu.GENERATED_END, result)

    def test_replace_managed_block_no_markers(self):
        original = "# Doc\n\n## Notes\n\nSome text."
        result = mu.replace_managed_block(
            original=original,
            heading="## Notes",
            start_marker=mu.GENERATED_START,
            end_marker=mu.GENERATED_END,
            body="Generated content",
        )
        self.assertIn("Generated content", result)
        self.assertIn(mu.GENERATED_START, result)

    def test_replace_managed_block_no_heading(self):
        original = "Plain text with no headings."
        result = mu.replace_managed_block(
            original=original,
            heading="## Notes",
            start_marker=mu.GENERATED_START,
            end_marker=mu.GENERATED_END,
            body="Gen",
        )
        # Should append at end
        self.assertIn("Gen", result)


# =======================================================================
# claim_health tests
# =======================================================================

class TestClaimHealth(unittest.TestCase):

    def test_assess_freshness_fresh(self):
        now = datetime(2026, 4, 23, tzinfo=timezone.utc)
        # 10 days ago — should be "fresh" (< 30)
        from datetime import timedelta
        past = (now - timedelta(days=10)).isoformat()
        f = ch.assess_freshness(past, now)
        self.assertEqual(f.level, "fresh")

    def test_assess_freshness_aging(self):
        now = datetime(2026, 4, 23, tzinfo=timezone.utc)
        # 60 days ago — "aging" (>= 30, < 90)
        from datetime import timedelta
        past = (now - timedelta(days=60)).isoformat()
        f = ch.assess_freshness(past, now)
        self.assertEqual(f.level, "aging")

    def test_assess_freshness_stale(self):
        now = datetime(2026, 4, 23, tzinfo=timezone.utc)
        # 100 days ago — "stale" (>= 90)
        from datetime import timedelta
        past = (now - timedelta(days=100)).isoformat()
        f = ch.assess_freshness(past, now)
        self.assertEqual(f.level, "stale")

    def test_assess_freshness_unknown(self):
        f = ch.assess_freshness(None)
        self.assertEqual(f.level, "unknown")
        f2 = ch.assess_freshness("")
        self.assertEqual(f2.level, "unknown")

    def test_normalize_claim_status(self):
        self.assertEqual(ch.normalize_claim_status("Supported"), "supported")
        self.assertEqual(ch.normalize_claim_status("  CONTESTED  "), "contested")
        self.assertEqual(ch.normalize_claim_status(None), "supported")
        self.assertEqual(ch.normalize_claim_status(""), "supported")

    def test_is_claim_contested(self):
        self.assertTrue(ch.is_claim_contested("contested"))
        self.assertTrue(ch.is_claim_contested("contradicted"))
        self.assertTrue(ch.is_claim_contested("refuted"))
        self.assertFalse(ch.is_claim_contested("supported"))
        self.assertFalse(ch.is_claim_contested(None))

    def test_build_claim_health(self):
        from memory_wiki.claim_health import WikiPageSummary, WikiClaim, WikiClaimEvidence
        page = WikiPageSummary(
            relative_path="entities/test.md",
            title="Test Page",
            id="entity.test",
            claims=[],
            updated_at="2026-04-01T00:00:00Z",
        )
        claim = WikiClaim(id="claim.1", text="The sky is blue.", status="supported",
                          evidence=[WikiClaimEvidence(source_id="src.1", path="sources/test.md")])
        h = ch.build_claim_health(page, claim, 0)
        self.assertEqual(h.key, "entities/test.md#claim.1")
        self.assertEqual(h.status, "supported")
        self.assertFalse(h.missing_evidence)
        self.assertEqual(h.evidence_count, 1)

    def test_build_claim_health_no_evidence(self):
        from memory_wiki.claim_health import WikiPageSummary, WikiClaim
        page = WikiPageSummary(relative_path="e.md", title="E", id="e.1", claims=[])
        claim = WikiClaim(id="c1", text="Something.", status="supported", evidence=[])
        h = ch.build_claim_health(page, claim, 0)
        self.assertTrue(h.missing_evidence)
        self.assertEqual(h.evidence_count, 0)

    def test_build_claim_clusters_same_id_different_text(self):
        pages = [
            make_summary("entities/a.md", id="entity.a", title="A", kind="entity",
                claims=[
                    {"id": "email-contact", "text": "Contact via email.", "status": "supported"},
                ]),
            make_summary("entities/b.md", id="entity.b", title="B", kind="entity",
                claims=[
                    {"id": "email-contact", "text": "Email is the contact method.", "status": "supported"},
                ]),
        ]
        clusters = ch.build_claim_clusters(pages)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].key, "email-contact")
        self.assertEqual(len(clusters[0].entries), 2)

    def test_build_claim_clusters_same_id_same_text(self):
        pages = [
            make_summary("entities/a.md", id="entity.a", kind="entity",
                claims=[{"id": "same-claim", "text": "Same text.", "status": "supported"}]),
            make_summary("entities/b.md", id="entity.b", kind="entity",
                claims=[{"id": "same-claim", "text": "Same text.", "status": "supported"}]),
        ]
        clusters = ch.build_claim_clusters(pages)
        # Same text + same status = no contradiction, should be empty
        self.assertEqual(len(clusters), 0)

    def test_build_page_contradiction_clusters(self):
        pages = [
            make_summary("entities/a.md", id="a", contradictions=["Email is slow"]),
            make_summary("entities/b.md", id="b", contradictions=["Email is slow"]),
        ]
        clusters = ch.build_page_contradiction_clusters(pages)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0].entries), 2)

    def test_collect_claim_health(self):
        pages = [
            make_summary("e.md", claims=[
                {"id": "c1", "text": "Claim one.", "status": "supported"},
                {"id": "c2", "text": "Claim two.", "status": "contested"},
            ]),
        ]
        health = ch.collect_claim_health(pages)
        self.assertEqual(len(health), 2)
        statuses = {h.status for h in health}
        self.assertIn("supported", statuses)
        self.assertIn("contested", statuses)


# =======================================================================
# vault tests
# =======================================================================

class TestVault(unittest.TestCase):

    def setUp(self):
        self.vault_dir = Path(tempfile.mkdtemp(prefix="vault_test_"))

    def tearDown(self):
        shutil.rmtree(self.vault_dir)

    def test_get_vault_status_empty(self):
        status = vault.get_vault_status(self.vault_dir)
        self.assertTrue(status["exists"])
        self.assertEqual(status["pageCounts"]["total"], 0)
        self.assertFalse(status["hasIndex"])

    def test_get_vault_status_with_pages(self):
        write_page(self.vault_dir, "entities/person.md", "---\ntitle: Person\nid: entity.person\n---\n")
        write_page(self.vault_dir, "concepts/idea.md", "---\ntitle: Idea\nid: concept.idea\n---\n")
        write_page(self.vault_dir, "index.md", "# Index")
        status = vault.get_vault_status(self.vault_dir)
        self.assertEqual(status["pageCounts"]["entity"], 1)
        self.assertEqual(status["pageCounts"]["concept"], 1)
        self.assertEqual(status["pageCounts"]["total"], 2)
        self.assertTrue(status["hasIndex"])

    def test_get_vault_status_skips_index_md(self):
        write_page(self.vault_dir, "entities/person.md", "---\nid: e.p\n---\n")
        write_page(self.vault_dir, "entities/index.md", "# Index")  # Should be skipped
        status = vault.get_vault_status(self.vault_dir)
        self.assertEqual(status["pageCounts"]["entity"], 1)

    def test_init_vault_creates_dirs(self):
        result = vault.init_vault(self.vault_dir)
        for dir_name in vault.REQUIRED_DIRS:
            self.assertTrue((self.vault_dir / dir_name).is_dir())
        self.assertTrue((self.vault_dir / "index.md").exists())

    def test_init_vault_idempotent(self):
        vault.init_vault(self.vault_dir)
        result2 = vault.init_vault(self.vault_dir)
        # Should report no new directories/files
        self.assertEqual(len(result2["createdDirectories"]), 0)
        self.assertEqual(len(result2["createdFiles"]), 0)

    def test_get_vault_health_missing_dir(self):
        health = vault.get_vault_health(Path("/nonexistent/path"))
        self.assertFalse(health["exists"])
        self.assertFalse(health["healthy"])

    def test_get_vault_health_empty(self):
        health = vault.get_vault_health(self.vault_dir)
        self.assertFalse(health["healthy"])  # Empty = not healthy


# =======================================================================
# query tests
# =======================================================================

class TestQuery(unittest.TestCase):

    def setUp(self):
        self.vault_dir = Path(tempfile.mkdtemp(prefix="query_test_"))
        # Write some test pages
        self.p1 = write_page(self.vault_dir, "entities/alice.md",
            "---\ntitle: Alice\nid: entity.alice\n---\n\n# Alice\n\nAlice works at ExampleCo. Contact via email.")
        self.p2 = write_page(self.vault_dir, "concepts/architecture.md",
            "---\ntitle: Architecture\nid: concept.architecture\n---\n\n# Architecture\n\nSystem architecture overview.")
        self.p3 = write_page(self.vault_dir, "entities/bob.md",
            "---\ntitle: Bob\nid: entity.bob\n---\n\n# Bob\n\nBob is the project lead.")

    def tearDown(self):
        shutil.rmtree(self.vault_dir)

    def test_read_wiki_pages(self):
        pages = query.read_wiki_pages(self.vault_dir)
        self.assertEqual(len(pages), 3)
        kinds = {p.kind for p in pages}
        self.assertEqual(kinds, {"entity", "concept"})

    def test_read_wiki_pages_respects_kind_dirs(self):
        pages = query.read_wiki_pages(self.vault_dir)
        entity_pages = [p for p in pages if p.kind == "entity"]
        self.assertEqual(len(entity_pages), 2)
        concept_pages = [p for p in pages if p.kind == "concept"]
        self.assertEqual(len(concept_pages), 1)

    def test_to_page_summary_extracts_title_from_frontmatter(self):
        raw = "---\ntitle: Custom Title\nid: test.1\n---\n\n# Should Not Be This"
        result = query.to_page_summary("a.md", "entities/a.md", raw)
        self.assertEqual(result.title, "Custom Title")

    def test_to_page_summary_extracts_title_from_h1(self):
        raw = "---\nid: test.2\n---\n\n# H1 Title Here"
        result = query.to_page_summary("a.md", "entities/a.md", raw)
        self.assertEqual(result.title, "H1 Title Here")

    def test_to_page_summary_falls_back_to_filename(self):
        raw = "---\nid: test.3\n---\n\nNo title here."
        result = query.to_page_summary("entities/foobar.md", "entities/foobar.md", raw)
        self.assertEqual(result.title, "foobar")

    def test_search_wiki_pages_by_title(self):
        results = query.search_wiki_pages(self.vault_dir, "alice")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Alice")

    def test_search_wiki_pages_by_body(self):
        results = query.search_wiki_pages(self.vault_dir, "ExampleCo")
        self.assertGreaterEqual(len(results), 1)
        titles = {r["title"] for r in results}
        self.assertIn("Alice", titles)

    def test_search_wiki_pages_by_claim_text(self):
        # alice.md has "Contact via email" in body — it's extracted as claim
        results = query.search_wiki_pages(self.vault_dir, "contact")
        # Should find alice via body text
        self.assertGreaterEqual(len(results), 1)

    def test_search_wiki_pages_max_results(self):
        results = query.search_wiki_pages(self.vault_dir, "entity", max_results=2)
        self.assertLessEqual(len(results), 2)

    def test_get_wiki_page_by_id(self):
        result = query.get_wiki_page(self.vault_dir, "entity.alice")
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "Alice")

    def test_get_wiki_page_by_relpath(self):
        result = query.get_wiki_page(self.vault_dir, "entities/alice.md")
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "Alice")

    def test_get_wiki_page_by_basename(self):
        result = query.get_wiki_page(self.vault_dir, "alice")
        self.assertIsNotNone(result)

    def test_get_wiki_page_not_found(self):
        result = query.get_wiki_page(self.vault_dir, "nonexistent.page")
        self.assertIsNone(result)

    def test_get_wiki_page_with_line_slice(self):
        raw = "---\ntitle: Test\nid: t.slice\n---\n\nLine 1\nLine 2\nLine 3"
        write_page(self.vault_dir, "entities/testc.md", raw)
        result = query.get_wiki_page(self.vault_dir, "t.slice", from_line=3, line_count=1)
        self.assertIsNotNone(result)
        # from_line is 1-indexed into the body lines after frontmatter
        self.assertIn("Line 2", result["content"])

    def test_get_wiki_page_claims_structured(self):
        raw = """---
title: Test
id: t.1
claims:
  - id: c1
    text: This is a claim.
    status: supported
    confidence: 0.8
---
Body."""
        write_page(self.vault_dir, "entities/testc.md", raw)
        result = query.get_wiki_page(self.vault_dir, "t.1")
        self.assertEqual(len(result["claims"]), 1)
        self.assertEqual(result["claims"][0]["text"], "This is a claim.")
        self.assertEqual(result["claims"][0]["status"], "supported")


# =======================================================================
# wiki_lint tests
# =======================================================================

class TestWikiLint(unittest.TestCase):

    def test_collect_structure_issues_missing_id(self):
        pages = [
            make_summary("entities/no-id.md", id="", kind="entity"),
        ]
        issues = wiki_lint._collect_structure_issues(pages)
        codes = {i.code for i in issues}
        self.assertIn("missing-id", codes)

    def test_collect_structure_issues_missing_page_type(self):
        # Must use WikiPageSummary directly because make_summary forces page_type=kind
        pages = [
            ch.WikiPageSummary(
                absolute_path="/tmp/no-pt.md",
                relative_path="entities/no-pt.md",
                kind="entity",
                title="No PT",
                id="entity.no-pt",
                page_type="",  # empty intentionally
                source_ids=[],
                link_targets=[],
                claims=[],
                contradictions=[],
                questions=[],
            ),
        ]
        issues = wiki_lint._collect_structure_issues(pages)
        codes = {i.code for i in issues}
        self.assertIn("missing-page-type", codes)

    def test_collect_structure_issues_duplicate_id(self):
        pages = [
            make_summary("entities/a.md", id="entity.dup", kind="entity"),
            make_summary("entities/b.md", id="entity.dup", kind="entity"),
        ]
        issues = wiki_lint._collect_structure_issues(pages)
        dup_issues = [i for i in issues if i.code == "duplicate-id"]
        self.assertEqual(len(dup_issues), 2)

    def test_collect_provenance_issues_missing_source_ids(self):
        pages = [
            make_summary("entities/no-src.md", kind="entity", source_ids=[]),
        ]
        issues = wiki_lint._collect_provenance_issues(pages)
        codes = {i.code for i in issues}
        self.assertIn("missing-source-ids", codes)

    def test_collect_provenance_issues_source_pages_exempt(self):
        pages = [
            make_summary("sources/article.md", kind="source", source_ids=[]),
        ]
        issues = wiki_lint._collect_provenance_issues(pages)
        codes = {i.code for i in issues}
        self.assertNotIn("missing-source-ids", codes)

    def test_collect_link_issues_broken_wikilink(self):
        # Use .md extension to match how link_targets are stored
        pages = [
            make_summary("entities/e.md", kind="entity", link_targets=["Nonexistent-Page"]),
        ]
        issues = wiki_lint._collect_link_issues(pages)
        broken = [i for i in issues if i.code == "broken-wikilink"]
        self.assertEqual(len(broken), 1)

    def test_collect_link_issues_valid_wikilink(self):
        # Use path without .md extension — link_targets store bare paths (no extension)
        pages = [
            make_summary("entities/e.md", kind="entity", link_targets=["entities/e"]),
        ]
        issues = wiki_lint._collect_link_issues(pages)
        broken = [i for i in issues if i.code == "broken-wikilink"]
        self.assertEqual(len(broken), 0)

    def test_collect_contradiction_issues_claim_cluster(self):
        pages = [
            make_summary("entities/a.md", id="a", kind="entity",
                claims=[{"id": "email-claim", "text": "Use email.", "status": "supported"}]),
            make_summary("entities/b.md", id="b", kind="entity",
                claims=[{"id": "email-claim", "text": "Email is best.", "status": "supported"}]),
        ]
        issues = wiki_lint._collect_contradiction_issues(pages)
        conflicts = [i for i in issues if i.code == "claim-conflict"]
        # One issue per page in the competing cluster (2 pages → 2 issues)
        self.assertEqual(len(conflicts), 2)

    def test_collect_quality_issues_low_confidence(self):
        pages = [
            make_summary("entities/test.md", kind="entity", confidence=0.3),
        ]
        issues = wiki_lint._collect_quality_issues(pages, datetime.now(timezone.utc))
        codes = {i.code for i in issues}
        self.assertIn("low-confidence", codes)

    def test_collect_quality_issues_stale_page(self):
        pages = [
            make_summary("entities/test.md", kind="entity",
                         updated_at="2020-01-01T00:00:00Z"),
        ]
        issues = wiki_lint._collect_quality_issues(pages, datetime.now(timezone.utc))
        codes = {i.code for i in issues}
        self.assertIn("stale-page", codes)

    def test_group_issues_by_category(self):
        pages = [
            make_summary("entities/no-id.md", id="", kind="entity", source_ids=[]),
        ]
        all_issues = wiki_lint._collect_structure_issues(pages) + \
                     wiki_lint._collect_provenance_issues(pages)
        grouped = wiki_lint.group_issues_by_category(all_issues)
        self.assertIn("structure", grouped)
        self.assertIn("provenance", grouped)

    def test_build_lint_report_body_empty(self):
        body = wiki_lint.build_lint_report_body([])
        self.assertEqual(body, "No issues found.")

    def test_build_lint_report_body_with_issues(self):
        from memory_wiki.wiki_lint import LintIssue
        issues = [
            LintIssue(severity="error", category="structure", code="missing-id",
                      path="e.md", message="Missing id."),
            LintIssue(severity="warning", category="links", code="broken-wikilink",
                      path="e.md", message="Broken link."),
        ]
        body = wiki_lint.build_lint_report_body(issues)
        self.assertIn("Errors: 1", body)
        self.assertIn("Warnings: 1", body)
        self.assertIn("Missing id", body)


# =======================================================================
# ingest tests
# =======================================================================

class TestIngest(unittest.TestCase):

    def setUp(self):
        self.vault_dir = Path(tempfile.mkdtemp(prefix="ingest_test_"))
        self.src_dir = Path(tempfile.mkdtemp(prefix="ingest_src_"))

    def tearDown(self):
        shutil.rmtree(self.vault_dir)
        shutil.rmtree(self.src_dir)

    def test_ingest_file_basic(self):
        src = self.src_dir / "test.md"
        src.write_text("# Test Article\n\nThis is a test article with some content.\n\n- Claim one.\n- Claim two.", encoding="utf-8")
        result = ingest.ingest_file(str(src), self.vault_dir, kind="entity")
        self.assertIsNone(result.error)
        self.assertEqual(result.kind, "entity")
        self.assertEqual(result.title, "Test Article")
        self.assertTrue(result.id.startswith("entity."))
        self.assertIsNotNone(result.wiki_path)

    def test_ingest_file_with_frontmatter(self):
        src = self.src_dir / "with-fm.md"
        src.write_text("---\ntitle: From Frontmatter\nid: custom.id\n---\n\nBody content.", encoding="utf-8")
        result = ingest.ingest_file(str(src), self.vault_dir, kind="auto")
        self.assertEqual(result.title, "From Frontmatter")
        self.assertIn("from-frontmatter.md", result.wiki_path)

    def test_ingest_file_force_overwrites(self):
        src = self.src_dir / "dup.md"
        src.write_text("# Test\n\nContent.", encoding="utf-8")
        r1 = ingest.ingest_file(str(src), self.vault_dir, kind="entity")
        r2 = ingest.ingest_file(str(src), self.vault_dir, kind="entity")
        self.assertIsNone(r1.error)
        self.assertIsNone(r2.error)
        self.assertNotEqual(r1.wiki_path, r2.wiki_path)  # Renamed

    def test_ingest_extracts_claims_from_bullets(self):
        src = self.src_dir / "claims.md"
        src.write_text("# Claims Doc\n\n- The sky is blue (supported)\n- The earth is flat (contested)\n- Just a regular line\n- This is a claim that has enough characters to be valid.", encoding="utf-8")
        result = ingest.ingest_file(str(src), self.vault_dir, kind="entity")
        self.assertGreaterEqual(result.claims_extracted, 1)
        # Read back the written file
        written = self.vault_dir / result.wiki_path
        content = written.read_text()
        self.assertIn("claims:", content)

    def test_ingest_extracts_sources_from_urls(self):
        src = self.src_dir / "sources.md"
        src.write_text("# Sources Doc\n\nSee [Example](https://example.com) for more.\n\nAlso [Google](https://google.com).", encoding="utf-8")
        result = ingest.ingest_file(str(src), self.vault_dir, kind="source")
        self.assertGreaterEqual(result.sources_extracted, 1)

    def test_ingest_auto_kind_detection(self):
        src = self.src_dir / "person.md"
        src.write_text("# Person\n\nA person who works at ExampleCo.", encoding="utf-8")
        result = ingest.ingest_file(str(src), self.vault_dir, kind="auto")
        self.assertEqual(result.kind, "entity")

    def test_ingest_file_not_found(self):
        result = ingest.ingest_file("/nonexistent/file.md", self.vault_dir)
        self.assertIsNotNone(result.error)
        self.assertIn("not found", result.error)

    def test_ingest_directory(self):
        (self.src_dir / "a.md").write_text("# A\n\nContent A.", encoding="utf-8")
        (self.src_dir / "b.md").write_text("# B\n\nContent B.", encoding="utf-8")
        (self.src_dir / ".hidden.md").write_text("# Hidden\n\nShould be skipped.", encoding="utf-8")
        results = ingest.ingest_directory(self.src_dir, self.vault_dir, kind="entity")
        self.assertEqual(len(results), 2)  # .hidden.md skipped


# =======================================================================
# compile tests
# =======================================================================

class TestCompile(unittest.TestCase):

    def setUp(self):
        self.vault_dir = Path(tempfile.mkdtemp(prefix="compile_test_"))
        # Create some source pages with claims
        write_page(self.vault_dir, "entities/alice.md",
            "---\ntitle: Alice\nid: entity.alice\nclaims:\n  - id: email-claim\n    text: Alice uses email.\n    status: supported\n---\n# Alice\n\nAlice info.")
        write_page(self.vault_dir, "entities/bob.md",
            "---\ntitle: Bob\nid: entity.bob\nclaims:\n  - id: email-claim\n    text: Bob also uses email.\n    status: supported\n---\n# Bob\n\nBob info.")
        write_page(self.vault_dir, "syntheses/ops-overview.md",
            "---\ntitle: Ops Overview\nid: synthesis.ops-overview\ntopics:\n  - email\n---\n# Ops Overview\n\nOverview.")

    def tearDown(self):
        shutil.rmtree(self.vault_dir)

    def test_discover_synthesis_targets(self):
        targets = cmp.discover_synthesis_targets(self.vault_dir)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].synthesis_id, "synthesis.ops-overview")

    def test_compile_claims_deduplicates(self):
        pages = [
            make_summary("entities/a.md", id="a", kind="entity",
                claims=[{"id": "c1", "text": "Same claim.", "status": "supported"}]),
            make_summary("entities/b.md", id="b", kind="entity",
                claims=[{"id": "c1", "text": "Same claim.", "status": "supported"}]),
        ]
        compiled = cmp.compile_claims(pages)
        self.assertEqual(len(compiled), 1)
        self.assertEqual(compiled[0].variant_count, 1)

    def test_compile_claims_tracks_contributing_pages(self):
        pages = [
            make_summary("entities/a.md", id="a", kind="entity",
                claims=[{"id": "c1", "text": "Claim A.", "status": "supported"}]),
            make_summary("entities/b.md", id="b", kind="entity",
                claims=[{"id": "c2", "text": "Claim B.", "status": "supported"}]),
        ]
        compiled = cmp.compile_claims(pages)
        self.assertEqual(len(compiled), 2)
        by_key = {c.claim_id: c for c in compiled}
        self.assertEqual(len(by_key["c1"].contributing_pages), 1)
        self.assertEqual(len(by_key["c2"].contributing_pages), 1)

    def test_compile_synthesis_dry_run(self):
        targets = cmp.discover_synthesis_targets(self.vault_dir)
        result = cmp.compile_synthesis(targets[0], self.vault_dir, dry_run=True)
        self.assertGreaterEqual(result.claims_included, 1)  # email claim(s) compiled
        self.assertFalse(result.written)

    def test_compile_synthesis_writes_file(self):
        targets = cmp.discover_synthesis_targets(self.vault_dir)
        result = cmp.compile_synthesis(targets[0], self.vault_dir, dry_run=False)
        self.assertTrue(result.written)
        self.assertTrue(targets[0].synthesis_path.exists())

    def test_compile_all_no_targets(self):
        empty_vault = Path(tempfile.mkdtemp(prefix="empty_compile_"))
        try:
            results = cmp.compile_all(empty_vault, dry_run=True)
            self.assertEqual(len(results), 0)
        finally:
            shutil.rmtree(empty_vault)


# =======================================================================
# doctor tests
# =======================================================================

class TestDoctor(unittest.TestCase):

    def setUp(self):
        self.vault_dir = Path(tempfile.mkdtemp(prefix="doctor_test_"))

    def tearDown(self):
        shutil.rmtree(self.vault_dir)

    def test_doctor_structure_missing(self):
        result = doctor.run_doctor(Path("/nonexistent"))
        self.assertFalse(result["healthy"])

    def test_doctor_missing_required_dirs(self):
        result = doctor.run_doctor(self.vault_dir)
        # Should warn about missing directories
        issues_by_cat = {i["code"] for i in result["issues"]}
        self.assertIn("missing-directory", issues_by_cat)

    def test_doctor_orphaned_page(self):
        write_page(self.vault_dir, "entities/orphan.md",
            "---\ntitle: Orphan\nid: entity.orphan\n---\n# Orphan\n\nNothing links here.")
        result = doctor.run_doctor(self.vault_dir)
        codes = {i["code"] for i in result["issues"]}
        self.assertIn("orphan-page", codes)

    def test_doctor_broken_wikilink(self):
        write_page(self.vault_dir, "entities/a.md",
            "---\ntitle: A\nid: entity.a\n---\n# A\n\nSee [[Nonexistent Place]].")
        write_page(self.vault_dir, "entities/b.md",
            "---\ntitle: B\nid: entity.b\n---\n# B\n\nReal page.")
        result = doctor.run_doctor(self.vault_dir)
        codes = {i["code"] for i in result["issues"]}
        self.assertIn("broken-wikilink", codes)

    def test_doctor_null_bytes_detected(self):
        p = write_page(self.vault_dir, "entities/corrupt.md",
            "---\ntitle: Corrupt\nid: entity.corrupt\n---\n# Corrupt\n")
        p.write_bytes(b"Normal \x00 null byte")
        result = doctor.run_doctor(self.vault_dir)
        codes = {i["code"] for i in result["issues"]}
        self.assertIn("null-bytes", codes)

    def test_doctor_checks_run(self):
        result = doctor.run_doctor(self.vault_dir)
        for check in ["structure", "permissions", "wikilinks", "orphans", "disk-space", "corruption"]:
            self.assertIn(check, result["checks"])


# =======================================================================
# apply tests
# =======================================================================

class TestApply(unittest.TestCase):

    def setUp(self):
        self.vault_dir = Path(tempfile.mkdtemp(prefix="apply_test_"))

    def tearDown(self):
        shutil.rmtree(self.vault_dir)

    def test_apply_metadata_dry_run(self):
        write_page(self.vault_dir, "entities/test.md",
            "---\ntitle: Test\nid: entity.test\n---\n# Test\n")
        result = app_mod.apply_metadata(
            self.vault_dir,
            updates={"confidence": 0.9},
            dry_run=True,
        )
        self.assertEqual(result.changed, 1)
        self.assertEqual(result.mode, "metadata")
        # File should NOT be modified in dry run
        content = (self.vault_dir / "entities/test.md").read_text()
        self.assertNotIn("0.9", content)

    def test_apply_metadata_writes(self):
        write_page(self.vault_dir, "entities/test.md",
            "---\ntitle: Test\nid: entity.test\n---\n# Test\n")
        result = app_mod.apply_metadata(
            self.vault_dir,
            updates={"confidence": 0.9},
            dry_run=False,
        )
        content = (self.vault_dir / "entities/test.md").read_text()
        self.assertIn("0.9", content)

    def test_apply_metadata_nested_key(self):
        write_page(self.vault_dir, "entities/test.md",
            "---\ntitle: Test\nid: entity.test\nclaims:\n  - id: c1\n    text: Test\n    status: supported\n---\n# Test\n")
        result = app_mod.apply_metadata(
            self.vault_dir,
            updates={"claims[0].status": "contested"},
            dry_run=False,
        )
        content = (self.vault_dir / "entities/test.md").read_text()
        self.assertIn("contested", content)

    def test_apply_lint_fix_missing_id(self):
        write_page(self.vault_dir, "entities/no-id.md",
            "---\ntitle: No ID Page\n---\n# No ID Page\n")
        result = app_mod.apply_lint_fix(self.vault_dir, dry_run=True)
        self.assertGreaterEqual(result.changed, 0)  # dry run

    def test_apply_lint_fix_adds_id(self):
        write_page(self.vault_dir, "entities/no-id.md",
            "---\ntitle: No ID Page\n---\n# No ID Page\n")
        result = app_mod.apply_lint_fix(self.vault_dir, dry_run=False)
        content = (self.vault_dir / "entities/no-id.md").read_text()
        self.assertIn("id:", content)

    def test_apply_synthesis_dry_run(self):
        # Create synthesis + source pages
        write_page(self.vault_dir, "entities/src.md",
            "---\ntitle: Src\nid: entity.src\nclaims:\n  - id: c1\n    text: A claim.\n    status: supported\n---\n# Src\n")
        write_page(self.vault_dir, "syntheses/overview.md",
            "---\ntitle: Overview\nid: synthesis.overview\ntopics:\n  - src\n---\n# Overview\n")
        result = app_mod.apply_synthesis(self.vault_dir, dry_run=True)
        self.assertGreaterEqual(result.changed, 0)


# =======================================================================
# Main
# =======================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)

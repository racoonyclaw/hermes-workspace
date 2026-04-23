"""vault — Wiki vault utilities: config loading, status, init.

Handles MEMORY_WIKI_PATH from ~/.hermes/.env and provides vault status / init helpers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

DEFAULT_VAULT_PATH = Path("/media/racoony-wiki/")


def get_wiki_vault_path(env_path: Optional[Path] = None) -> Path:
    """Load MEMORY_WIKI_PATH from ~/.hermes/.env and return the vault path.

    If the env var is not set or the file doesn't exist, returns DEFAULT_VAULT_PATH.
    """
    if env_path is None:
        env_path = Path.home() / ".hermes" / ".env"

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("MEMORY_WIKI_PATH="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return Path(value)

    return DEFAULT_VAULT_PATH


def get_vault_path_or_default(vault_path: Optional[str]) -> Path:
    """Return vault_path if provided, otherwise load from .env."""
    if vault_path:
        return Path(vault_path)
    return get_wiki_vault_path()


# ---------------------------------------------------------------------------
# Vault directories
# ---------------------------------------------------------------------------

REQUIRED_DIRS = ("entities", "concepts", "sources", "syntheses", "reports")
VAULT_INDEX_FILES = (
    "index.md",
    "AGENTS.md",
    "WIKI.md",
)


# ---------------------------------------------------------------------------
# Vault status
# ---------------------------------------------------------------------------

def get_vault_status(vault_path: Path) -> dict:
    """Get a status summary of the wiki vault.

    Returns a dict with:
      - vaultPath: str
      - exists: bool
      - pageCounts: dict (by kind)
      - hasIndex: bool
      - openclawMeta: bool
      - lastModified: str or None
    """
    result = {
        "vaultPath": str(vault_path),
        "exists": vault_path.is_dir(),
        "pageCounts": {"entity": 0, "concept": 0, "source": 0, "synthesis": 0, "report": 0, "total": 0},
        "hasIndex": False,
        "openclawMeta": False,
        "lastModified": None,
    }

    if not result["exists"]:
        return result

    # Check for index files
    for idx_file in VAULT_INDEX_FILES:
        if (vault_path / idx_file).exists():
            result["hasIndex"] = True
            break

    # Check for .openclaw-wiki metadata dir
    if (vault_path / ".openclaw-wiki").is_dir():
        result["openclawMeta"] = True

    # Count pages per kind (exclude index.md files)
    for kind in ("entities", "concepts", "sources", "syntheses", "reports"):
        dir_path = vault_path / kind
        if dir_path.is_dir():
            count = sum(
                1 for f in dir_path.glob("*.md")
                if f.is_file() and f.name not in ("index.md",)
            )
            # Singularize kind key: "entities"→"entity", "syntheses"→"synthesis", etc.
            if kind == "sources":
                singular = "source"
            elif kind == "syntheses":
                singular = "synthesis"
            elif kind.endswith("ies"):
                singular = kind[:-3] + "y"  # entities → entity
            elif kind.endswith("es"):
                singular = kind[:-2]  # syntheses → synthesis
            elif kind.endswith("s"):
                singular = kind[:-1]  # reports → report
            else:
                singular = kind
            result["pageCounts"][singular] = count
            result["pageCounts"]["total"] += count

    # Find last modified page (exclude index.md files)
    try:
        mtimes: list[float] = []
        for kind in ("entities", "concepts", "sources", "syntheses", "reports"):
            dir_path = vault_path / kind
            if dir_path.is_dir():
                mtimes.extend(
                    f.stat().st_mtime
                    for f in dir_path.glob("*.md")
                    if f.is_file() and f.name not in ("index.md",)
                )
        if mtimes:
            import datetime
            result["lastModified"] = datetime.datetime.fromtimestamp(
                max(mtimes), tz=datetime.timezone.utc
            ).isoformat()
    except OSError:
        pass

    return result


def get_vault_health(vault_path: Path) -> dict:
    """Return a quick health assessment of the vault."""
    status = get_vault_status(vault_path)

    health = {
        "vaultPath": status["vaultPath"],
        "exists": status["exists"],
        "healthy": False,
        "issues": [],
    }

    if not status["exists"]:
        health["issues"].append("Vault directory does not exist")
        return health

    if status["pageCounts"]["total"] == 0:
        health["issues"].append("Vault is empty — no pages found")

    missing_dirs = [d for d in REQUIRED_DIRS if not (vault_path / d).is_dir()]
    if missing_dirs:
        health["issues"].append(f"Missing required directories: {', '.join(missing_dirs)}")

    if not status["hasIndex"]:
        health["issues"].append("Missing vault index file (index.md)")

    health["healthy"] = len(health["issues"]) == 0

    return health


# ---------------------------------------------------------------------------
# Vault init
# ---------------------------------------------------------------------------

def init_vault(vault_path: Path) -> dict:
    """Create the vault directory layout if it doesn't exist.

    Creates all required subdirectories. Does not overwrite existing files.

    Returns a dict with:
      - rootDir: str
      - createdDirectories: list[str]
      - createdFiles: list[str]
    """
    created_dirs: list[str] = []
    created_files: list[str] = []

    vault_path = vault_path.resolve()

    for dir_name in REQUIRED_DIRS:
        dir_path = vault_path / dir_name
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            created_dirs.append(dir_name)

    # Create reports dir explicitly (also covered by REQUIRED_DIRS)
    reports_path = vault_path / "reports"
    if not reports_path.exists():
        reports_path.mkdir(parents=True, exist_ok=True)
        created_dirs.append("reports")

    # Create a minimal index.md if it doesn't exist
    index_path = vault_path / "index.md"
    if not index_path.exists():
        index_path.write_text("# Wiki Index\n\nWelcome to the wiki.\n", encoding="utf-8")
        created_files.append("index.md")

    return {
        "rootDir": str(vault_path),
        "createdDirectories": sorted(created_dirs),
        "createdFiles": sorted(created_files),
    }

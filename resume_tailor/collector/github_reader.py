"""GitHub reader — collect evidence from a public GitHub profile.

Evidence is extracted from repo READMEs, source files, and recent commit
messages. The snapshot fetcher is deliberately rate-limit-safe: the plain
repos-listing endpoint ignores sort=stars for large profiles, so we use the
Search API (separate quota) for star-sorted metadata, raw.githubusercontent.com
for content, and atom feeds for commit subjects — with the git trees API being
the only per-repo core-quota call (1 per repo).

Offline/fixture mode (brainstorm rec #1): a fetched profile is cached as a
local JSON snapshot in cache_dir; collect_github() reloads the cache when
present, so the whole pipeline runs reproducibly without network or rate
limits. The demo (sindresorhus) is fetched once, then replayed from cache.

Pre-filtering (brainstorm rec #4): repos are filtered by stars/recency and
capped *before* chunking+embedding, so a rich profile never floods the corpus.
Language relevance is applied at MATCH time (Phase 2), not here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from resume_tailor.collector.base import SourceArtifact
from resume_tailor.collector.chunking import chunk_text
from resume_tailor.schemas import Evidence, EvidenceChunk

# Root readme variants are indexed as the repo's README evidence, not as code
# files. Keep in sync with the README fetch loop in _fetch_snapshot.
_README_NAMES = frozenset({"readme.md", "readme.markdown"})

# File types worth mining for skill evidence; everything else is skipped.
DEFAULT_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".sh", ".sql", ".json", ".yaml", ".yml",
    ".toml", ".md", ".txt", ".css", ".html", ".vue", ".svelte", ".kt", ".swift",
    ".r", ".jl", ".lua", ".ex", ".exs",
})
# Paths that add noise, not signal.
SKIP_DIRS = frozenset({
    "node_modules", "vendor", "dist", "build", ".git", ".github", "coverage",
    ".venv", "venv", "__pycache__", "third_party", "migrations", "fixtures",
    "testdata", ".next", "target",
})
MAX_CHUNK_BYTES = 100_000


@dataclass
class RepoFilters:
    """Pre-filter knobs applied before chunking/embedding (rec #4)."""

    min_stars: int = 0
    max_repos: int = 30
    max_files_per_repo: int = 8
    max_commits: int = 20
    updated_within_days: int | None = None
    extensions: frozenset[str] = field(default_factory=lambda: DEFAULT_EXTENSIONS)
    max_file_bytes: int = MAX_CHUNK_BYTES


def _is_interesting_file(path: str, filters: RepoFilters) -> bool:
    parts = path.split("/")
    if any(seg in SKIP_DIRS for seg in parts[:-1]):
        return False
    # Only ROOT-level readmes duplicate the README evidence; nested readmes
    # (monorepos) aren't covered by it, so they stay indexed as code files.
    if "/" not in path and Path(path).name.lower() in _README_NAMES:
        return False
    suffix = Path(path).suffix.lower()
    return suffix in filters.extensions and not path.startswith(".")


def _blob_url(full_name: str, branch: str, path: str) -> str:
    return f"https://github.com/{full_name}/blob/{branch}/{path}"


def _is_recent(updated_at: str, within_days: int) -> bool:
    """True if updated_at is within within_days; unparseable dates are kept."""
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86_400
    return age_days <= within_days


def extract_artifacts(snapshot: dict, filters: RepoFilters | None = None) -> list[SourceArtifact]:
    """Turn a snapshot dict into Evidence + chunks. Pure and testable."""
    filters = filters or RepoFilters()
    artifacts: list[SourceArtifact] = []

    repos = [r for r in snapshot.get("repos", []) if r.get("stargazers_count", 0) >= filters.min_stars]
    if filters.updated_within_days is not None:
        repos = [r for r in repos if _is_recent(r.get("updated_at", ""), filters.updated_within_days)]
    repos.sort(key=lambda r: (r.get("stargazers_count", 0), r.get("updated_at", "")), reverse=True)
    repos = repos[: filters.max_repos]

    for repo in repos:
        full_name = repo.get("full_name", "unknown")
        branch = repo.get("default_branch") or "main"
        language = repo.get("language") or ""
        skill_tags = [language] if language else []

        # README
        readme = (repo.get("readme") or "").strip()
        if readme:
            source_id = f"repo:{full_name}#readme"
            chunks = [
                EvidenceChunk(chunk_id=f"{source_id}#c{i}", source_id=source_id, text=text, skill_tags=skill_tags)
                for i, text in enumerate(chunk_text(readme))
            ]
            artifacts.append(
                SourceArtifact(
                    evidence=Evidence(
                        source_id=source_id,
                        source_type="readme",
                        skill_tags=skill_tags,
                        snippet=readme[:200],
                        confidence=1.0,
                        url=f"https://github.com/{full_name}/blob/{branch}/README.md",
                    ),
                    chunks=chunks,
                )
            )

        # Source files
        for file in (repo.get("files") or [])[: filters.max_files_per_repo]:
            path = file.get("path", "")
            content = file.get("content", "")
            if not _is_interesting_file(path, filters):
                continue
            if len(content.encode("utf-8", errors="ignore")) > filters.max_file_bytes:
                continue
            source_id = f"repo:{full_name}#file:{path}"
            chunks = [
                EvidenceChunk(chunk_id=f"{source_id}#c{i}", source_id=source_id, text=text, skill_tags=skill_tags)
                for i, text in enumerate(chunk_text(content))
            ]
            artifacts.append(
                SourceArtifact(
                    evidence=Evidence(
                        source_id=source_id,
                        source_type="code",
                        skill_tags=skill_tags,
                        snippet=content[:200],
                        confidence=1.0,
                        url=_blob_url(full_name, branch, path),
                    ),
                    chunks=chunks,
                )
            )

        # Recent commit messages
        for commit in (repo.get("commits") or [])[: filters.max_commits]:
            sha = commit.get("sha", "")
            message = (commit.get("message") or "").strip()
            if not message:
                continue
            short = sha[:8]
            source_id = f"repo:{full_name}#commit:{short}"
            artifacts.append(
                SourceArtifact(
                    evidence=Evidence(
                        source_id=source_id,
                        source_type="commit",
                        skill_tags=skill_tags,
                        snippet=message[:200],
                        confidence=1.0,
                        url=f"https://github.com/{full_name}/commit/{sha}",
                    ),
                    chunks=[EvidenceChunk(chunk_id=f"{source_id}#c0", source_id=source_id, text=message, skill_tags=skill_tags)],
                )
            )
    return artifacts


# --------------------------------------------------------------------------
# Network fetch + snapshot cache
# --------------------------------------------------------------------------

def _snapshot_path(cache_dir: Path, username: str) -> Path:
    return cache_dir / f"github_{username}.json"


def load_snapshot(cache_dir: Path, username: str) -> dict | None:
    path = _snapshot_path(cache_dir, username)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_snapshot(cache_dir: Path, username: str, snapshot: dict) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _snapshot_path(cache_dir, username)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return path


def _atom_commit_subjects(atom_xml: str) -> list[dict]:
    """Parse commit subjects (titles) from a repo's atom feed."""
    import xml.etree.ElementTree as ET

    out: list[dict] = []
    try:
        root = ET.fromstring(atom_xml)
    except ET.ParseError:
        return out
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        if not title:
            continue
        link = entry.find("a:link", ns)
        sha = (link.get("href") or "").rstrip("/").split("/")[-1] if link is not None else ""
        out.append({"sha": sha, "message": title})
    return out


def _fetch_snapshot(username: str, token: str | None, filters: RepoFilters) -> dict:
    """Fetch a profile snapshot in a rate-limit-safe way.

    The plain /users/{user}/repos endpoint ignores sort=stars for large profiles
    (verified empirically), so we use instead:
      * the Search API (separate quota) for star-sorted repo metadata,
      * raw.githubusercontent.com for READMEs + file contents (no API quota),
      * each repo's atom feed for recent commit subjects (no API quota),
      * the git trees API for file listings — the only per-repo core-quota call,
        limited to the max_repos repos that will actually be selected.

    An unauthenticated run stays well inside the 60 req/hr core limit (≈max_repos
    calls total); a token lifts the caps (5k req/hr).
    """
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": "resume-tailor-demo"})
    if token:
        session.headers["Authorization"] = f"token {token}"

    search = session.get(
        "https://api.github.com/search/repositories",
        params={"q": f"user:{username}", "sort": "stars", "order": "desc", "per_page": 50},
        timeout=30,
    )
    search.raise_for_status()
    items = search.json().get("items", [])

    candidates = [r for r in items if r.get("stargazers_count", 0) >= filters.min_stars]
    candidates = candidates[: filters.max_repos + 2]  # small buffer for star ties

    repos: list[dict] = []
    for idx, repo in enumerate(candidates):
        full_name = repo["full_name"]
        branch = repo.get("default_branch") or "main"
        entry: dict = {
            "full_name": full_name,
            "description": repo.get("description") or "",
            "stargazers_count": repo.get("stargazers_count", 0),
            "language": repo.get("language") or "",
            "updated_at": repo.get("pushed_at") or repo.get("updated_at") or "",
            "default_branch": branch,
            "readme": "",
            "files": [],
            "commits": [],
        }
        # README via raw.githubusercontent.com (no API quota)
        for readme_name in ("README.md", "readme.md", "README.markdown"):
            try:
                raw = session.get(f"https://raw.githubusercontent.com/{full_name}/{branch}/{readme_name}", timeout=30)
                if raw.status_code == 200 and raw.content.strip():
                    entry["readme"] = raw.content.decode("utf-8", errors="replace")
                    break
            except Exception:
                pass
        # Commit subjects via atom feed (no API quota)
        try:
            atom = session.get(f"https://github.com/{full_name}/commits.atom", timeout=30)
            if atom.status_code == 200:
                entry["commits"] = _atom_commit_subjects(atom.text)[: filters.max_commits]
        except Exception:
            pass
        # File listing via git trees API (1 core call per repo) — only for the
        # repos that will actually be selected; buffer repos skip it.
        if idx < filters.max_repos:
            try:
                tree_resp = session.get(
                    f"https://api.github.com/repos/{full_name}/git/trees/{branch}",
                    params={"recursive": "1"},
                    timeout=30,
                )
                if tree_resp.status_code == 200:
                    paths = [
                        t.get("path", "")
                        for t in tree_resp.json().get("tree", [])
                        if t.get("type") == "blob" and _is_interesting_file(t.get("path", ""), filters)
                    ]
                    for path in paths[: filters.max_files_per_repo]:
                        raw_url = f"https://raw.githubusercontent.com/{full_name}/{branch}/{quote(path, safe='/')}"
                        raw = session.get(raw_url, timeout=30)
                        if raw.status_code == 200:
                            entry["files"].append(
                                {"path": path, "content": raw.content.decode("utf-8", errors="replace")[: filters.max_file_bytes]}
                            )
            except Exception:
                pass
        repos.append(entry)

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "username": username,
        "repos": repos,
    }


def collect_github(
    username: str,
    *,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    filters: RepoFilters | None = None,
    refresh: bool = False,
) -> list[SourceArtifact]:
    """Collect GitHub evidence.

    Uses the cached snapshot when present (offline/fixture mode); pass
    refresh=True to force a live re-fetch. Returns [] on empty profiles.
    """
    filters = filters or RepoFilters()
    cache = Path(cache_dir) if cache_dir else None

    snapshot = None
    if cache is not None and not refresh:
        snapshot = load_snapshot(cache, username)
    if snapshot is None:
        snapshot = _fetch_snapshot(username, token=token, filters=filters)
        if cache is not None:
            save_snapshot(cache, username, snapshot)
    return extract_artifacts(snapshot, filters=filters)

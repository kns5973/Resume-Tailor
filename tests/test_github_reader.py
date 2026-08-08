import json
from pathlib import Path

from resume_tailor.collector.github_reader import RepoFilters, collect_github, extract_artifacts, save_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "github_snapshot.json"


def _snapshot() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_extract_artifacts_basic():
    artifacts = extract_artifacts(_snapshot())
    types = {a.evidence.source_type for a in artifacts}
    assert types == {"readme", "code", "commit"}

    by_id = {a.evidence.source_id: a for a in artifacts}
    assert "repo:janedemo/backend-api#readme" in by_id
    assert "repo:janedemo/backend-api#file:app/queue.py" in by_id
    assert "repo:janedemo/backend-api#commit:a1b2c3d4" in by_id
    # commits have exactly one chunk each
    assert len(by_id["repo:janedemo/backend-api#commit:a1b2c3d4"].chunks) == 1


def test_prefilter_min_stars_drops_scratch_repo():
    artifacts = extract_artifacts(_snapshot(), RepoFilters(min_stars=1))
    ids = [a.evidence.source_id for a in artifacts]
    assert not any("old-script" in sid for sid in ids)
    assert any("backend-api" in sid for sid in ids)


def test_max_repos_cap():
    artifacts = extract_artifacts(_snapshot(), RepoFilters(max_repos=1))
    repos = {a.evidence.source_id.split("#")[0] for a in artifacts}
    assert repos == {"repo:janedemo/backend-api"}  # highest stars, sorted first


def test_vendored_and_binary_files_skipped():
    snapshot = _snapshot()
    snapshot["repos"][0]["files"] = [
        {"path": "node_modules/pkg/index.js", "content": "x"},
        {"path": "logo.png", "content": "x"},
        {"path": "app/real.py", "content": "print(1)"},
    ]
    artifacts = extract_artifacts(snapshot)
    file_ids = [a.evidence.source_id for a in artifacts if a.evidence.source_type == "code"]
    assert "repo:janedemo/backend-api#file:app/real.py" in file_ids
    assert not any("node_modules" in sid for sid in file_ids)
    assert not any("logo.png" in sid for sid in file_ids)


def test_readme_not_indexed_as_code_file():
    snapshot = _snapshot()
    snapshot["repos"][0]["files"].append(
        {"path": "readme.md", "content": "duplicate readme"}
    )
    snapshot["repos"][0]["files"].append(
        {"path": "packages/foo/readme.md", "content": "nested monorepo readme"}
    )
    artifacts = extract_artifacts(snapshot)
    file_ids = [a.evidence.source_id for a in artifacts if a.evidence.source_type == "code"]
    # root readme is already README evidence — skip; nested readme is not
    assert not any(sid.endswith("#file:readme.md") for sid in file_ids)
    assert any(sid.endswith("#file:packages/foo/readme.md") for sid in file_ids)


def test_updated_within_days_filter():
    # backend-api (2026-07-01) and gitlytics (2025-11-20) are recent;
    # old-script (2019-01-01) predates the window.
    artifacts = extract_artifacts(_snapshot(), RepoFilters(updated_within_days=400))
    ids = [a.evidence.source_id for a in artifacts]
    assert not any("old-script" in sid for sid in ids)
    assert any("backend-api" in sid for sid in ids)
    assert any("gitlytics" in sid for sid in ids)


def test_updated_within_days_unparseable_dates_kept():
    snapshot = _snapshot()
    snapshot["repos"][0]["updated_at"] = "garbage-date"
    artifacts = extract_artifacts(snapshot, RepoFilters(updated_within_days=1))
    assert any("backend-api" in a.evidence.source_id for a in artifacts)


def test_collect_github_from_cache_no_network(tmp_path):
    # Write the fixture into the cache dir — collect_github must load it without
    # ever touching the GitHub API (offline/fixture mode).
    cache = tmp_path / "cache"
    save_snapshot(cache, "janedemo", _snapshot())

    artifacts = collect_github("janedemo", cache_dir=cache)
    assert len(artifacts) >= 3
    assert any(a.evidence.source_type == "readme" for a in artifacts)

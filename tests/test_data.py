"""
Validate the seed JSONL files.

These tests are pure file-read — no heavy deps, fast to run.
They guard against schema drift and broken cross-references.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DATA_DIR = Path("data")

KNOWN_EVENT_IDS = {
    "evt_gdg_cloud_2024",
    "evt_gdg_cloud_2023",
    "evt_pycon_india_2023",
    "evt_devfest_kolkata_2024",
    "evt_bangla_python_2024",
    "evt_fossasia_2024",
    "evt_fossasia_2023",
}

KNOWN_ORG_IDS = {
    "org_gdg_cloud_kolkata",
    "org_jadavpur_cs",
    "org_iit_kgp",
    "org_iiit_kalyani",
    "org_iiest",
    "org_women_techmakers",
}

VALID_EDGE_TYPES = {"maintains", "follows", "attended", "member_of", "contributed_to"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                pytest.fail(f"{path}:{i} is not valid JSON: {exc}")
    return records


@pytest.fixture(scope="module")
def people():
    return load_jsonl(DATA_DIR / "people.jsonl")


@pytest.fixture(scope="module")
def repos():
    return load_jsonl(DATA_DIR / "repos.jsonl")


@pytest.fixture(scope="module")
def edges():
    return load_jsonl(DATA_DIR / "edges.jsonl")


@pytest.fixture(scope="module")
def person_ids(people):
    return {p["id"] for p in people}


@pytest.fixture(scope="module")
def repo_ids(repos):
    return {r["id"] for r in repos}


# ---------------------------------------------------------------------------
# people.jsonl
# ---------------------------------------------------------------------------


PEOPLE_REQUIRED = {"id", "name", "bio", "location", "languages", "followers", "url", "evidence"}


class TestPeople:
    def test_file_exists(self):
        assert (DATA_DIR / "people.jsonl").exists(), "data/people.jsonl is missing"

    def test_minimum_count(self, people):
        assert len(people) >= 20, f"Expected ≥20 people, got {len(people)}"

    def test_required_fields(self, people):
        for p in people:
            missing = PEOPLE_REQUIRED - p.keys()
            assert not missing, f"Person {p.get('id', '?')} missing fields: {missing}"

    def test_ids_are_unique(self, people):
        ids = [p["id"] for p in people]
        dupes = {x for x in ids if ids.count(x) > 1}
        assert not dupes, f"Duplicate person IDs: {dupes}"

    def test_ids_are_strings(self, people):
        for p in people:
            assert isinstance(p["id"], str) and p["id"], f"Bad id in person: {p}"

    def test_languages_is_list(self, people):
        for p in people:
            assert isinstance(p["languages"], list), f"{p['id']}: languages must be a list"

    def test_evidence_is_list(self, people):
        for p in people:
            assert isinstance(p["evidence"], list), f"{p['id']}: evidence must be a list"

    def test_followers_is_non_negative_int(self, people):
        for p in people:
            assert isinstance(p["followers"], int) and p["followers"] >= 0, (
                f"{p['id']}: followers must be non-negative int"
            )

    def test_url_looks_like_github(self, people):
        for p in people:
            assert p["url"].startswith("https://github.com/"), (
                f"{p['id']}: url should start with https://github.com/"
            )

    def test_profile_has_searchable_text(self, people):
        sparse = [
            p["id"]
            for p in people
            if not p.get("bio", "").strip() and not p.get("evidence")
        ]
        assert not sparse, f"People missing both bio and evidence: {sparse}"

    def test_evidence_contains_strings(self, people):
        bad = [
            p["id"]
            for p in people
            if any(not isinstance(item, str) or not item.strip() for item in p.get("evidence", []))
        ]
        assert not bad, f"People with malformed evidence entries: {bad}"


# ---------------------------------------------------------------------------
# repos.jsonl
# ---------------------------------------------------------------------------


REPOS_REQUIRED = {"id", "owner", "description", "stars", "language", "topics"}


class TestRepos:
    def test_file_exists(self):
        assert (DATA_DIR / "repos.jsonl").exists(), "data/repos.jsonl is missing"

    def test_minimum_count(self, repos):
        assert len(repos) >= 10, f"Expected ≥10 repos, got {len(repos)}"

    def test_required_fields(self, repos):
        for r in repos:
            missing = REPOS_REQUIRED - r.keys()
            assert not missing, f"Repo {r.get('id', '?')} missing fields: {missing}"

    def test_ids_are_unique(self, repos):
        ids = [r["id"] for r in repos]
        dupes = {x for x in ids if ids.count(x) > 1}
        assert not dupes, f"Duplicate repo IDs: {dupes}"

    def test_id_format_owner_slash_name(self, repos):
        for r in repos:
            assert "/" in r["id"], f"Repo id should be owner/name format, got: {r['id']}"

    def test_owner_matches_id_prefix(self, repos):
        for r in repos:
            expected_prefix = r["id"].split("/")[0]
            assert r["owner"] == expected_prefix, (
                f"Repo {r['id']}: owner '{r['owner']}' doesn't match id prefix '{expected_prefix}'"
            )

    def test_topics_is_list(self, repos):
        for r in repos:
            assert isinstance(r["topics"], list), f"{r['id']}: topics must be a list"

    def test_stars_non_negative(self, repos):
        for r in repos:
            assert isinstance(r["stars"], int) and r["stars"] >= 0, (
                f"{r['id']}: stars must be non-negative int"
            )

    def test_owner_references_known_person(self, repos, person_ids):
        unknown = [r["id"] for r in repos if r["owner"] not in person_ids]
        assert not unknown, f"Repos with unknown owner IDs: {unknown}"


# ---------------------------------------------------------------------------
# edges.jsonl
# ---------------------------------------------------------------------------


class TestEdges:
    def test_file_exists(self):
        assert (DATA_DIR / "edges.jsonl").exists(), "data/edges.jsonl is missing"

    def test_minimum_count(self, edges):
        assert len(edges) >= 50, f"Expected ≥50 edges, got {len(edges)}"

    def test_required_fields(self, edges):
        for i, e in enumerate(edges):
            for field in ("src", "dst", "type"):
                assert field in e, f"Edge {i} missing field '{field}': {e}"

    def test_edge_types_are_valid(self, edges):
        bad = [e for e in edges if e["type"] not in VALID_EDGE_TYPES]
        assert not bad, f"Edges with unrecognised type: {[e['type'] for e in bad]}"

    def test_no_self_loops(self, edges):
        loops = [e for e in edges if e["src"] == e["dst"]]
        assert not loops, f"Self-loop edges found: {loops}"

    def test_follows_edges_are_between_people(self, edges, person_ids):
        bad = [
            e for e in edges
            if e["type"] == "follows"
            and (e["src"] not in person_ids or e["dst"] not in person_ids)
        ]
        assert not bad, f"'follows' edges reference non-person nodes: {bad}"

    def test_maintains_src_is_person(self, edges, person_ids):
        bad = [e for e in edges if e["type"] == "maintains" and e["src"] not in person_ids]
        assert not bad, f"'maintains' edges with non-person src: {bad}"

    def test_maintains_dst_is_repo(self, edges, repo_ids):
        bad = [e for e in edges if e["type"] == "maintains" and e["dst"] not in repo_ids]
        assert not bad, f"'maintains' edges with unknown repo dst: {bad}"

    def test_attended_dst_is_known_event(self, edges):
        bad = [e for e in edges if e["type"] == "attended" and e["dst"] not in KNOWN_EVENT_IDS]
        assert not bad, (
            f"'attended' edges point to unknown event IDs: {[e['dst'] for e in bad]}. "
            f"Add to KNOWN_EVENT_IDS in this file or in scripts/build_index.py."
        )

    def test_member_of_dst_is_known_org(self, edges):
        bad = [e for e in edges if e["type"] == "member_of" and e["dst"] not in KNOWN_ORG_IDS]
        assert not bad, (
            f"'member_of' edges point to unknown org IDs: {[e['dst'] for e in bad]}. "
            f"Add to KNOWN_ORG_IDS in this file or in scripts/build_index.py."
        )

    def test_contributed_to_dst_is_repo(self, edges, repo_ids):
        bad = [
            e for e in edges
            if e["type"] == "contributed_to" and e["dst"] not in repo_ids
        ]
        assert not bad, f"'contributed_to' edges reference unknown repo: {bad}"

    def test_maintains_and_owner_consistent(self, edges, repos):
        """Every maintains edge should match the repo's owner field."""
        repo_owner = {r["id"]: r["owner"] for r in repos}
        bad = [
            e for e in edges
            if e["type"] == "maintains"
            and e["dst"] in repo_owner
            and repo_owner[e["dst"]] != e["src"]
        ]
        assert not bad, f"'maintains' edges inconsistent with repos.owner: {bad}"

    def test_all_people_have_at_least_one_edge(self, edges, person_ids):
        """Every person should appear in at least one edge as src or dst."""
        connected = {e["src"] for e in edges} | {e["dst"] for e in edges}
        isolated = person_ids - connected
        assert not isolated, f"People with zero edges (invisible in graph): {isolated}"

    def test_graph_has_social_or_contribution_edges(self, edges):
        relationship_edges = [
            e for e in edges if e["type"] in {"follows", "contributed_to", "attended", "member_of"}
        ]
        assert relationship_edges, "Expected graph to include non-maintainer relationships"

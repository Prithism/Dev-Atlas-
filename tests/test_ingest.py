from __future__ import annotations

from unittest.mock import MagicMock

import requests

from scripts import ingest


def test_merge_user_records_combines_metadata_and_dedupes_repos():
    existing = {
        "id": "alice",
        "name": "Alice",
        "bio": "short bio",
        "location": "",
        "company": "",
        "blog": "",
        "twitter": "",
        "followers": 12,
        "following": 5,
        "public_repos": 2,
        "url": "https://github.com/alice",
        "search_queries": ["location:Kolkata"],
        "source_urls": ["https://github.com/alice"],
        "seed_signals": ["GDG Cloud Kolkata"],
        "repos": [
            {
                "name": "atlas",
                "full_name": "alice/atlas",
                "owner": "alice",
                "description": "",
                "language": "Python",
                "stars": 1,
                "topics": ["graph"],
            }
        ],
    }
    incoming = {
        "id": "alice",
        "name": "Alice Roy",
        "bio": "much longer bio for the same user",
        "location": "Kolkata",
        "company": "@Acme",
        "blog": "",
        "twitter": "alice_dev",
        "followers": 40,
        "following": 8,
        "public_repos": 5,
        "url": "https://github.com/alice",
        "search_queries": ["location:Jadavpur"],
        "source_urls": ["https://example.com/alice"],
        "seed_signals": ["PyData Kolkata"],
        "repos": [
            {
                "name": "atlas",
                "full_name": "alice/atlas",
                "owner": "alice",
                "description": "real description",
                "language": "Python",
                "stars": 7,
                "topics": ["graph", "search"],
            }
        ],
    }

    merged = ingest._merge_user_records(existing, incoming)

    assert merged["bio"] == "much longer bio for the same user"
    assert merged["location"] == "Kolkata"
    assert merged["company"] == "@Acme"
    assert merged["followers"] == 40
    assert merged["public_repos"] == 5
    assert merged["search_queries"] == ["location:Kolkata", "location:Jadavpur"]
    assert merged["seed_signals"] == ["GDG Cloud Kolkata", "PyData Kolkata"]
    assert len(merged["repos"]) == 1
    assert merged["repos"][0]["stars"] == 7
    assert merged["repos"][0]["description"] == "real description"


def test_normalize_writes_real_graph_edges_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "DATA_DIR", tmp_path)

    users = [
        {
            "id": "alice",
            "name": "Alice Roy",
            "bio": "",
            "location": "Kolkata",
            "company": "@Acme",
            "blog": "",
            "twitter": "",
            "followers": 42,
            "url": "https://github.com/alice",
            "search_queries": ["location:Kolkata followers:>=5 repos:>=3"],
            "source_urls": ["https://github.com/alice"],
            "seed_signals": ["GDG Cloud Kolkata organizer"],
            "repos": [
                {
                    "name": "atlas",
                    "full_name": "alice/atlas",
                    "owner": "alice",
                    "description": "graph search for developers",
                    "language": "Python",
                    "stars": 10,
                    "topics": ["graph", "search"],
                }
            ],
        },
        {
            "id": "bob",
            "name": "Bob Das",
            "bio": "Builds retrieval systems",
            "location": "Salt Lake",
            "company": "",
            "blog": "",
            "twitter": "",
            "followers": 20,
            "url": "https://github.com/bob",
            "search_queries": [],
            "source_urls": ["https://github.com/bob"],
            "seed_signals": [],
            "repos": [
                {
                    "name": "retriever",
                    "full_name": "bob/retriever",
                    "owner": "bob",
                    "description": "retrieval pipeline",
                    "language": "Go",
                    "stars": 4,
                    "topics": ["retrieval"],
                }
            ],
        },
    ]

    extra_edges = [
        {"src": "alice", "dst": "bob", "type": "follows"},
        {"src": "alice", "dst": "bob", "type": "follows"},
        {"src": "bob", "dst": "alice/atlas", "type": "contributed_to"},
        {"src": "charlie", "dst": "alice/atlas", "type": "contributed_to"},
        {"src": "alice", "dst": "alice", "type": "follows"},
    ]

    ingest.normalize(users, extra_edges)

    people = ingest.load_jsonl(tmp_path / "people.jsonl")
    repos = ingest.load_jsonl(tmp_path / "repos.jsonl")
    edges = ingest.load_jsonl(tmp_path / "edges.jsonl")

    assert {person["id"] for person in people} == {"alice", "bob"}
    assert {repo["id"] for repo in repos} == {"alice/atlas", "bob/retriever"}

    alice = next(person for person in people if person["id"] == "alice")
    assert "Python" in alice["languages"]
    assert any(item == "GitHub location: Kolkata" for item in alice["evidence"])
    assert any(item == "GitHub company: @Acme" for item in alice["evidence"])
    assert any("Top repo: alice/atlas (10 stars)" == item for item in alice["evidence"])

    follows_edges = [edge for edge in edges if edge["type"] == "follows"]
    contribution_edges = [edge for edge in edges if edge["type"] == "contributed_to"]
    maintains_edges = [edge for edge in edges if edge["type"] == "maintains"]

    assert follows_edges == [{"src": "alice", "dst": "bob", "type": "follows"}]
    assert contribution_edges == [{"src": "bob", "dst": "alice/atlas", "type": "contributed_to"}]
    assert len(maintains_edges) == 2


def test_get_repo_contributors_returns_empty_on_non_json_response():
    client = ingest.GitHubClient("test-token")

    response = MagicMock()
    response.content = b"<html>secondary rate limit</html>"
    response.text = "<html>secondary rate limit</html>"
    response.status_code = 200
    response.json.side_effect = requests.exceptions.JSONDecodeError("bad json", "", 0)

    client._request = MagicMock(return_value=response)

    assert client.get_repo_contributors("alice/atlas", 5) == []


def test_build_contribution_edges_skips_oversized_repos_and_caps_total(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "RAW_DIR", tmp_path)
    monkeypatch.setattr(ingest, "MAX_STARS_FOR_CONTRIBUTOR_SCAN", 100)
    monkeypatch.setattr(ingest, "MAX_CONTRIBUTOR_REPOS_TOTAL", 1)
    monkeypatch.setattr(ingest, "MAX_CONTRIBUTOR_REPOS_PER_USER", 2)
    monkeypatch.setattr(ingest, "MAX_CONTRIBUTORS_PER_REPO", 5)

    client = MagicMock()
    client.get_repo_contributors.return_value = ["bob", "external-user"]

    users = [
        {
            "id": "alice",
            "followers": 50,
            "repos": [
                {
                    "name": "huge",
                    "full_name": "alice/huge",
                    "owner": "alice",
                    "description": "huge repo",
                    "language": "Python",
                    "stars": 500,
                    "topics": [],
                },
                {
                    "name": "atlas",
                    "full_name": "alice/atlas",
                    "owner": "alice",
                    "description": "local repo",
                    "language": "Python",
                    "stars": 10,
                    "topics": [],
                },
            ],
        },
        {
            "id": "bob",
            "followers": 20,
            "repos": [
                {
                    "name": "retriever",
                    "full_name": "bob/retriever",
                    "owner": "bob",
                    "description": "second repo should be skipped by cap",
                    "language": "Go",
                    "stars": 8,
                    "topics": [],
                }
            ],
        },
    ]

    edges = ingest.build_contribution_edges(client, users)

    assert edges == [{"src": "bob", "dst": "alice/atlas", "type": "contributed_to"}]
    client.get_repo_contributors.assert_called_once_with("alice/atlas", 5)

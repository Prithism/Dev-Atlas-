"""Shared fixtures for all test modules."""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from unittest.mock import MagicMock

import networkx as nx
import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Minimal in-memory dataset used by retrieval and build_index tests
# ---------------------------------------------------------------------------

PEOPLE = [
    {
        "id": "alice",
        "name": "Alice Roy",
        "bio": "ML engineer building LangGraph pipelines for Bengali NLP",
        "location": "Kolkata",
        "languages": ["Python"],
        "followers": 100,
        "url": "https://github.com/alice",
        "evidence": ["GDG Cloud Kolkata 2024 speaker"],
    },
    {
        "id": "bob",
        "name": "Bob Das",
        "bio": "NLP researcher, Bengali language models and RAG systems",
        "location": "Kolkata",
        "languages": ["Python"],
        "followers": 50,
        "url": "https://github.com/bob",
        "evidence": ["Jadavpur University PhD"],
    },
    {
        "id": "charlie",
        "name": "Charlie Sen",
        "bio": "Backend Go engineer, civic tech Kolkata open data",
        "location": "Kolkata",
        "languages": ["Go"],
        "followers": 30,
        "url": "https://github.com/charlie",
        "evidence": [],
    },
]

REPOS = [
    {
        "id": "alice/langraph-demo",
        "owner": "alice",
        "description": "LangGraph multi-agent Bengali chatbot demo",
        "stars": 42,
        "language": "Python",
        "topics": ["langgraph", "bengali", "nlp"],
    },
    {
        "id": "bob/bengali-embeddings",
        "owner": "bob",
        "description": "Sentence embeddings for Bengali text",
        "stars": 18,
        "language": "Python",
        "topics": ["bengali", "embeddings", "nlp"],
    },
]

EDGES = [
    {"src": "alice", "dst": "alice/langraph-demo", "type": "maintains"},
    {"src": "bob", "dst": "bob/bengali-embeddings", "type": "maintains"},
    {"src": "alice", "dst": "bob", "type": "follows"},
    {"src": "bob", "dst": "alice", "type": "follows"},
    {"src": "alice", "dst": "evt_gdg_cloud_2024", "type": "attended"},
    {"src": "bob", "dst": "evt_gdg_cloud_2024", "type": "attended"},
    {"src": "alice", "dst": "org_gdg_cloud_kolkata", "type": "member_of"},
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_graph():
    """Minimal DiGraph with centrality pre-computed."""
    G = nx.DiGraph()
    for p in PEOPLE:
        G.add_node(
            p["id"],
            type="person",
            name=p["name"],
            bio=p["bio"],
            centrality=0.0,
            evidence=p["evidence"],
        )
    G.add_node("evt_gdg_cloud_2024", type="event", name="GDG Cloud Kolkata 2024")
    G.add_node("org_gdg_cloud_kolkata", type="org", name="GDG Cloud Kolkata")
    for repo in REPOS:
        G.add_node(
            repo["id"],
            type="repo",
            name=repo["id"],
            description=repo["description"],
            stars=repo["stars"],
        )
    for e in EDGES:
        G.add_edge(e["src"], e["dst"], type=e["type"])

    pr = nx.pagerank(G.to_undirected(), alpha=0.85)
    nx.set_node_attributes(G, pr, "centrality")
    return G


@pytest.fixture
def tmp_data_dir(tmp_path, test_graph):
    """
    Write graph.pkl + people.jsonl to a temp directory.
    Returns the Path to that directory.
    """
    with open(tmp_path / "graph.pkl", "wb") as f:
        pickle.dump(test_graph, f)

    with open(tmp_path / "people.jsonl", "w") as f:
        for p in PEOPLE:
            f.write(json.dumps(p) + "\n")

    return tmp_path


@pytest.fixture
def mock_chroma_collection():
    """Mock ChromaDB collection that returns alice then bob."""
    coll = MagicMock()
    coll.count.return_value = len(PEOPLE)
    coll.query.return_value = {
        "ids": [["alice", "bob"]],
        "distances": [[0.05, 0.25]],
        "metadatas": [[{"name": "Alice Roy"}, {"name": "Bob Das"}]],
        "documents": [["alice text", "bob text"]],
    }
    return coll


@pytest.fixture
def mock_model():
    """Mock SentenceTransformer that returns a zero embedding."""
    model = MagicMock()
    model.encode.return_value = np.zeros(384)
    return model

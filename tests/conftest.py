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


# ---------------------------------------------------------------------------
# Agent / API layer fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_retriever():
    """
    MagicMock Retriever pre-configured with realistic return values.
    Used by test_agents.py and test_api.py.
    """
    from unittest.mock import MagicMock
    from atlas.retrieval import Result, Retriever

    r = MagicMock(spec=Retriever)

    r.query.return_value = [
        Result(person_id="alice", score=0.88, evidence=["GDG Cloud 2024 speaker"]),
        Result(person_id="bob", score=0.72, evidence=["JU PhD, Bengali NLP"]),
        Result(person_id="charlie", score=0.55, evidence=[]),
    ]

    r.subgraph.return_value = {
        "nodes": [
            {"id": "alice", "label": "Alice Roy", "type": "person", "centrality": 0.4},
            {"id": "bob", "label": "Bob Das", "type": "person", "centrality": 0.3},
        ],
        "edges": [{"src": "alice", "dst": "bob", "type": "follows"}],
    }

    r.get_person.side_effect = lambda pid: {
        "alice": {
            "id": "alice",
            "name": "Alice Roy",
            "bio": "ML engineer building LangGraph pipelines",
            "languages": ["Python"],
            "followers": 100,
            "url": "https://github.com/alice",
            "evidence": ["GDG Cloud 2024 speaker"],
        },
        "bob": {
            "id": "bob",
            "name": "Bob Das",
            "bio": "NLP researcher, Bengali language models",
            "languages": ["Python"],
            "followers": 50,
            "url": "https://github.com/bob",
            "evidence": ["JU PhD"],
        },
        "charlie": {
            "id": "charlie",
            "name": "Charlie Sen",
            "bio": "Backend Go engineer",
            "languages": ["Go"],
            "followers": 30,
            "url": "https://github.com/charlie",
            "evidence": [],
        },
    }.get(pid, {})

    return r


@pytest.fixture
def llm_response():
    """
    Factory that returns a mock anthropic Message with a given text body.
    Usage: mock_client.messages.create = AsyncMock(return_value=llm_response("..."))
    """
    from unittest.mock import MagicMock

    def _make(text: str):
        msg = MagicMock()
        msg.content = [MagicMock(text=text)]
        return msg

    return _make


@pytest.fixture
def mock_anthropic_client(llm_response):
    """
    AsyncAnthropic mock whose messages.create returns a configurable response.
    Default response is a valid composer JSON array.
    """
    from unittest.mock import AsyncMock, MagicMock
    import anthropic

    client = MagicMock(spec=anthropic.AsyncAnthropic)
    default_body = '[{"id": "alice", "evidence": ["LangGraph engineer in Kolkata"]}]'
    client.messages.create = AsyncMock(return_value=llm_response(default_body))
    return client

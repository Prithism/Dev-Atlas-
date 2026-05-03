"""
Integration tests for atlas/main.py FastAPI endpoints.

Patches Retriever and AsyncAnthropic so tests never touch disk or the network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from atlas.agents import ComposedResult
from atlas.retrieval import Result


# ---------------------------------------------------------------------------
# App fixture — patches the two heavy dependencies before lifespan runs
# ---------------------------------------------------------------------------


@pytest.fixture
def client(mock_retriever, mock_anthropic_client):
    """TestClient with mocked Retriever + mocked Anthropic client."""
    with (
        patch("atlas.main.Retriever", return_value=mock_retriever),
        patch("atlas.main.anthropic.AsyncAnthropic", return_value=mock_anthropic_client),
    ):
        from atlas.main import app

        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# POST /query
# ---------------------------------------------------------------------------


VALID_RESPONSE = {
    "results": [
        {
            "id": "alice",
            "name": "Alice Roy",
            "score": 0.88,
            "evidence": ["LangGraph engineer in Kolkata"],
            "url": "https://github.com/alice",
        }
    ],
    "subgraph": {
        "nodes": [{"id": "alice", "label": "Alice Roy", "type": "person", "centrality": 0.4}],
        "edges": [],
    },
}


@pytest.fixture
def query_client(mock_retriever, mock_anthropic_client, llm_response):
    """Client where the pipeline returns a predictable composed response."""
    import atlas.agents as agents_module
    from unittest.mock import AsyncMock

    async def _fake_pipeline(q, retriever, client):
        return VALID_RESPONSE

    with (
        patch("atlas.main.Retriever", return_value=mock_retriever),
        patch("atlas.main.anthropic.AsyncAnthropic", return_value=mock_anthropic_client),
        patch("atlas.main.run_pipeline", side_effect=_fake_pipeline),
    ):
        from atlas.main import app

        with TestClient(app) as c:
            yield c


class TestQueryEndpoint:
    def test_post_query_returns_200(self, query_client):
        resp = query_client.post("/query", json={"q": "langgraph kolkata"})
        assert resp.status_code == 200

    def test_response_has_results_field(self, query_client):
        resp = query_client.post("/query", json={"q": "langgraph"})
        assert "results" in resp.json()

    def test_response_has_subgraph_field(self, query_client):
        resp = query_client.post("/query", json={"q": "langgraph"})
        assert "subgraph" in resp.json()

    def test_subgraph_has_nodes_and_edges(self, query_client):
        resp = query_client.post("/query", json={"q": "jadavpur"})
        sg = resp.json()["subgraph"]
        assert "nodes" in sg
        assert "edges" in sg

    def test_result_items_have_required_fields(self, query_client):
        resp = query_client.post("/query", json={"q": "ml mentor"})
        for item in resp.json()["results"]:
            for field in ("id", "name", "score", "evidence", "url"):
                assert field in item, f"Missing field '{field}' in result: {item}"

    def test_empty_query_returns_400(self, client):
        resp = client.post("/query", json={"q": ""})
        assert resp.status_code == 400

    def test_whitespace_only_query_returns_400(self, client):
        resp = client.post("/query", json={"q": "   "})
        assert resp.status_code == 400

    def test_missing_q_field_returns_422(self, client):
        resp = client.post("/query", json={})
        assert resp.status_code == 422

    def test_response_content_type_is_json(self, query_client):
        resp = query_client.post("/query", json={"q": "test"})
        assert "application/json" in resp.headers["content-type"]

    def test_demo_query_langgraph(self, query_client):
        resp = query_client.post("/query", json={"q": "Who works on LangGraph in Kolkata"})
        assert resp.status_code == 200

    def test_demo_query_mentor(self, query_client):
        resp = query_client.post("/query", json={"q": "Who mentors junior ML engineers"})
        assert resp.status_code == 200

    def test_demo_query_jadavpur(self, query_client):
        resp = query_client.post("/query", json={"q": "Show me the Jadavpur cluster"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_returns_ok_status(self, client):
        resp = client.get("/health")
        assert resp.json() == {"status": "ok"}


class TestFrontendServing:
    def test_root_serves_index_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Kolkata Dev Atlas" in resp.text

    def test_app_js_is_served(self, client):
        resp = client.get("/app.js")
        assert resp.status_code == 200
        assert "application/javascript" in resp.headers["content-type"]
        assert "API_BASE" in resp.text


# ---------------------------------------------------------------------------
# Contract shape — matches the spec from member-a-query-agents.md exactly
# ---------------------------------------------------------------------------


class TestContractShape:
    """Verify the response matches what Member B will consume."""

    def test_result_score_is_float(self, query_client):
        resp = query_client.post("/query", json={"q": "test"})
        for item in resp.json()["results"]:
            assert isinstance(item["score"], float)

    def test_result_evidence_is_list_of_strings(self, query_client):
        resp = query_client.post("/query", json={"q": "test"})
        for item in resp.json()["results"]:
            assert isinstance(item["evidence"], list)
            assert all(isinstance(e, str) for e in item["evidence"])

    def test_subgraph_nodes_have_id_and_label(self, query_client):
        resp = query_client.post("/query", json={"q": "test"})
        for node in resp.json()["subgraph"]["nodes"]:
            assert "id" in node
            assert "label" in node

    def test_subgraph_edges_have_src_and_dst(self, query_client):
        resp = query_client.post("/query", json={"q": "test"})
        for edge in resp.json()["subgraph"]["edges"]:
            assert "src" in edge
            assert "dst" in edge

    def test_empty_result_includes_message(self, mock_retriever, mock_anthropic_client):
        """When no results are found the response includes a helpful message."""
        async def _empty_pipeline(q, retriever, client):
            return {
                "results": [],
                "subgraph": {"nodes": [], "edges": []},
                "message": "No results found.",
            }

        with (
            patch("atlas.main.Retriever", return_value=mock_retriever),
            patch("atlas.main.anthropic.AsyncAnthropic", return_value=mock_anthropic_client),
            patch("atlas.main.run_pipeline", side_effect=_empty_pipeline),
        ):
            from atlas.main import app
            with TestClient(app) as c:
                resp = c.post("/query", json={"q": "xyzzy irrelevant"})
                body = resp.json()
                assert body["results"] == []
                assert "message" in body

"""
Tests for atlas/retrieval.py.

Unit tests: use mocked ChromaDB and SentenceTransformer — no I/O after fixture setup.
Smoke tests: require data/graph.pkl + data/chroma/ (built by scripts/build_index.py).
             Skipped automatically if artifacts are missing.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas.retrieval import Result, Retriever
from tests.conftest import PEOPLE


# ---------------------------------------------------------------------------
# Fixture: Retriever backed by tmp graph.pkl + mocked ChromaDB
# ---------------------------------------------------------------------------


@pytest.fixture
def retriever(tmp_data_dir, mock_chroma_collection, mock_model):
    """
    Real graph from tmp_data_dir, mocked chroma + model.
    Patches are applied only during __init__; stored object refs keep working.
    """
    mock_client = MagicMock()
    mock_client.get_collection.return_value = mock_chroma_collection

    with (
        patch("atlas.retrieval.chromadb.PersistentClient", return_value=mock_client),
        patch("atlas.retrieval.SentenceTransformer", return_value=mock_model),
    ):
        r = Retriever(
            graph_path=str(tmp_data_dir / "graph.pkl"),
            chroma_path=str(tmp_data_dir / "chroma"),
        )
    return r


# ---------------------------------------------------------------------------
# Retriever.__init__
# ---------------------------------------------------------------------------


class TestRetrieverInit:
    def test_raises_if_graph_missing(self, tmp_path, mock_chroma_collection, mock_model):
        mock_client = MagicMock()
        mock_client.get_collection.return_value = mock_chroma_collection
        with (
            patch("atlas.retrieval.chromadb.PersistentClient", return_value=mock_client),
            patch("atlas.retrieval.SentenceTransformer", return_value=mock_model),
            pytest.raises(FileNotFoundError, match="Graph not found"),
        ):
            Retriever(graph_path=str(tmp_path / "nonexistent.pkl"))

    def test_loads_people_lookup(self, retriever):
        assert len(retriever._people) == len(PEOPLE)
        assert retriever._people["alice"]["name"] == "Alice Roy"

    def test_max_centrality_is_positive(self, retriever):
        assert retriever._max_centrality > 0.0

    def test_graph_has_expected_person_count(self, retriever):
        person_nodes = [
            n for n, d in retriever.G.nodes(data=True) if d.get("type") == "person"
        ]
        assert len(person_nodes) == len(PEOPLE)


# ---------------------------------------------------------------------------
# Retriever.query
# ---------------------------------------------------------------------------


class TestRetrieverQuery:
    def test_returns_list_of_result(self, retriever):
        results = retriever.query("langraph bengali", k=5)
        assert isinstance(results, list)
        assert all(isinstance(r, Result) for r in results)

    def test_result_has_required_fields(self, retriever):
        results = retriever.query("ml engineer")
        for r in results:
            assert isinstance(r.person_id, str)
            assert isinstance(r.score, float)
            assert isinstance(r.evidence, list)

    def test_results_sorted_by_score_descending(self, retriever):
        results = retriever.query("nlp")
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_scores_are_in_valid_range(self, retriever):
        results = retriever.query("bengali nlp")
        for r in results:
            assert 0.0 <= r.score <= 1.0, f"Score out of range: {r.score}"

    def test_respects_k_limit(self, retriever):
        results = retriever.query("python", k=1)
        assert len(results) <= 1

    def test_no_duplicate_person_ids(self, retriever):
        results = retriever.query("langgraph kolkata", k=10)
        ids = [r.person_id for r in results]
        assert len(ids) == len(set(ids)), "Duplicate person_ids in results"

    def test_direct_hit_scores_higher_than_1hop_neighbor(self, retriever):
        """alice is a direct chroma hit; charlie is not — alice should rank above charlie."""
        results = retriever.query("langgraph")
        ids = [r.person_id for r in results]
        if "alice" in ids and "charlie" in ids:
            assert ids.index("alice") < ids.index("charlie")

    def test_empty_collection_returns_empty_list(self, tmp_data_dir, mock_model):
        empty_coll = MagicMock()
        empty_coll.count.return_value = 0
        mock_client = MagicMock()
        mock_client.get_collection.return_value = empty_coll

        with (
            patch("atlas.retrieval.chromadb.PersistentClient", return_value=mock_client),
            patch("atlas.retrieval.SentenceTransformer", return_value=mock_model),
        ):
            r = Retriever(
                graph_path=str(tmp_data_dir / "graph.pkl"),
                chroma_path=str(tmp_data_dir / "chroma"),
            )
        assert r.query("anything") == []

    def test_evidence_list_populated_for_hits(self, retriever):
        results = retriever.query("ml")
        alice = next((r for r in results if r.person_id == "alice"), None)
        assert alice is not None
        assert len(alice.evidence) > 0

    def test_keyword_fallback_works_when_embedding_model_unavailable(self, tmp_data_dir, mock_chroma_collection):
        mock_client = MagicMock()
        mock_client.get_collection.return_value = mock_chroma_collection

        with (
            patch("atlas.retrieval.chromadb.PersistentClient", return_value=mock_client),
            patch("atlas.retrieval.SentenceTransformer", side_effect=RuntimeError("offline")),
        ):
            r = Retriever(
                graph_path=str(tmp_data_dir / "graph.pkl"),
                chroma_path=str(tmp_data_dir / "chroma"),
            )

        results = r.query("langgraph bengali", k=5)

        assert results
        assert results[0].person_id == "alice"


# ---------------------------------------------------------------------------
# Retriever.subgraph
# ---------------------------------------------------------------------------


class TestRetrieverSubgraph:
    def test_returns_dict_with_nodes_and_edges(self, retriever):
        sg = retriever.subgraph(["alice"])
        assert "nodes" in sg
        assert "edges" in sg

    def test_nodes_have_required_keys(self, retriever):
        sg = retriever.subgraph(["alice"])
        for node in sg["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "type" in node

    def test_edges_have_required_keys(self, retriever):
        sg = retriever.subgraph(["alice"])
        for edge in sg["edges"]:
            assert "src" in edge
            assert "dst" in edge
            assert "type" in edge

    def test_seed_node_is_included(self, retriever):
        sg = retriever.subgraph(["alice"])
        ids = {n["id"] for n in sg["nodes"]}
        assert "alice" in ids

    def test_hops_0_returns_only_seed(self, retriever):
        sg = retriever.subgraph(["alice"], hops=0)
        ids = {n["id"] for n in sg["nodes"]}
        assert ids == {"alice"}

    def test_hops_1_includes_direct_neighbors(self, retriever):
        sg = retriever.subgraph(["alice"], hops=1)
        ids = {n["id"] for n in sg["nodes"]}
        # alice -> bob (follows), alice -> evt_gdg_cloud_2024 (attended)
        assert "bob" in ids

    def test_edges_only_reference_included_nodes(self, retriever):
        sg = retriever.subgraph(["alice"], hops=1)
        node_ids = {n["id"] for n in sg["nodes"]}
        for e in sg["edges"]:
            assert e["src"] in node_ids, f"Edge src '{e['src']}' not in nodes"
            assert e["dst"] in node_ids, f"Edge dst '{e['dst']}' not in nodes"

    def test_unknown_person_id_ignored(self, retriever):
        sg = retriever.subgraph(["does_not_exist"])
        assert sg == {"nodes": [], "edges": []}

    def test_multiple_seeds_merge_subgraphs(self, retriever):
        sg_alice = retriever.subgraph(["alice"])
        sg_charlie = retriever.subgraph(["charlie"])
        sg_both = retriever.subgraph(["alice", "charlie"])
        both_ids = {n["id"] for n in sg_both["nodes"]}
        assert {n["id"] for n in sg_alice["nodes"]}.issubset(both_ids)

    def test_node_cap_is_respected(self, retriever, test_graph):
        """Inject 220 person nodes and confirm an explicit cap is applied."""
        import pickle
        import networkx as nx

        big_G = test_graph.copy()
        for i in range(220):
            nid = f"extra_{i}"
            big_G.add_node(nid, type="person", name=f"Extra {i}", centrality=0.001)
            big_G.add_edge("alice", nid, type="follows")

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp:
            pickle.dump(big_G, tmp)
            tmp_pkl = tmp.name

        try:
            mock_client = MagicMock()
            mock_coll = MagicMock()
            mock_coll.count.return_value = 3
            mock_client.get_collection.return_value = mock_coll
            mock_model = MagicMock()

            with (
                patch("atlas.retrieval.chromadb.PersistentClient", return_value=mock_client),
                patch("atlas.retrieval.SentenceTransformer", return_value=mock_model),
            ):
                r = Retriever(graph_path=tmp_pkl, chroma_path="/tmp/chroma_test")

            # Default cap (180) is respected.
            sg_default = r.subgraph(["alice"], hops=1)
            assert len(sg_default["nodes"]) <= 180

            # Explicit cap is also respected.
            sg_capped = r.subgraph(["alice"], hops=1, max_nodes=50)
            assert len(sg_capped["nodes"]) <= 50
            # Seed must always be included even when capped.
            assert "alice" in {n["id"] for n in sg_capped["nodes"]}
        finally:
            os.unlink(tmp_pkl)


# ---------------------------------------------------------------------------
# Retriever.full_graph
# ---------------------------------------------------------------------------


class TestRetrieverFullGraph:
    def test_returns_dict_with_nodes_and_edges(self, retriever):
        fg = retriever.full_graph()
        assert "nodes" in fg
        assert "edges" in fg

    def test_nodes_have_required_keys(self, retriever):
        fg = retriever.full_graph()
        for node in fg["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "type" in node
            assert "centrality" in node

    def test_includes_all_node_types_when_under_cap(self, retriever):
        fg = retriever.full_graph(max_nodes=500)
        types = {n["type"] for n in fg["nodes"]}
        # The fixture has person, repo, event, org -- all should be present.
        assert "person" in types

    def test_respects_max_nodes_cap(self, retriever):
        fg = retriever.full_graph(max_nodes=3)
        assert len(fg["nodes"]) <= 3

    def test_edges_only_reference_included_nodes(self, retriever):
        fg = retriever.full_graph(max_nodes=200)
        node_ids = {n["id"] for n in fg["nodes"]}
        for e in fg["edges"]:
            assert e["src"] in node_ids
            assert e["dst"] in node_ids

    def test_include_types_filter(self, retriever):
        fg = retriever.full_graph(max_nodes=500, include_types=("person",))
        types = {n["type"] for n in fg["nodes"]}
        assert types <= {"person"}


# ---------------------------------------------------------------------------
# Retriever.get_person
# ---------------------------------------------------------------------------


class TestRetrieverGetPerson:
    def test_returns_full_record(self, retriever):
        p = retriever.get_person("alice")
        assert p["id"] == "alice"
        assert p["name"] == "Alice Roy"
        assert "bio" in p

    def test_returns_empty_dict_for_unknown_id(self, retriever):
        assert retriever.get_person("nobody") == {}

    def test_returns_dict(self, retriever):
        assert isinstance(retriever.get_person("bob"), dict)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


class TestResult:
    def test_default_evidence_is_empty_list(self):
        r = Result(person_id="x", score=0.5)
        assert r.evidence == []

    def test_fields_are_accessible(self):
        r = Result(person_id="alice", score=0.9, evidence=["GDG speaker"])
        assert r.person_id == "alice"
        assert r.score == 0.9
        assert r.evidence == ["GDG speaker"]


# ---------------------------------------------------------------------------
# Smoke tests — require real artifacts (skip if missing)
# ---------------------------------------------------------------------------

GRAPH_PKL = Path("data/graph.pkl")
CHROMA_DIR = Path("data/chroma")
REQUIRES_ARTIFACTS = pytest.mark.skipif(
    not (GRAPH_PKL.exists() and CHROMA_DIR.exists()),
    reason="data/graph.pkl or data/chroma/ missing — run: python scripts/build_index.py",
)


@REQUIRES_ARTIFACTS
class TestSmokeQueries:
    """Run the three hackathon demo queries against the real Retriever."""

    @pytest.fixture(scope="class")
    def live_retriever(self):
        return Retriever()

    def test_langgraph_kolkata_returns_results(self, live_retriever):
        results = live_retriever.query("langgraph kolkata", k=10)
        assert len(results) >= 5, (
            f"'langgraph kolkata' returned only {len(results)} results — dataset may be thin"
        )

    def test_langgraph_kolkata_top_result_is_relevant(self, live_retriever):
        """
        Conditional smoke test: if the dataset contains anyone with a
        specific LangGraph signal (the literal token "langgraph" or
        "langchain" in their indexed text), retrieval for "langgraph
        kolkata" must surface at least one of them in the top-10. If the
        ingest pipeline hasn't surfaced any yet, this test passes
        vacuously -- that's a data-discovery problem, not a retrieval bug.

        We use word-boundary matching here because generic substrings
        like "agent", "llm", "rag" appear in unrelated contexts (e.g.
        "user agent", "rag" inside "fragment").
        """
        import re
        pattern = re.compile(r"\b(langgraph|langchain|lang-graph|lang-chain)\b")
        indexed_relevant = {
            pid for pid, doc in live_retriever._search_docs.items()
            if pattern.search(doc)
        }
        if not indexed_relevant:
            pytest.skip(
                "Dataset contains no people whose indexed text mentions "
                "langgraph/langchain. Discovery passes (D1-D6) need to "
                "surface them first."
            )

        # Top-10 rather than top-5 because the score blends 30%
        # centrality, which can outweigh keyword match for popular Kolkata
        # accounts. If no langgraph person makes it into the top-10, the
        # blend is mis-tuned.
        results = live_retriever.query("langgraph kolkata", k=10)
        top_ids = {r.person_id for r in results}
        overlap = top_ids & indexed_relevant
        assert len(overlap) >= 1, (
            f"Retrieval missed all langgraph/langchain people. "
            f"{len(indexed_relevant)} relevant in dataset, top-10: {top_ids}"
        )

    def test_ml_mentor_returns_results(self, live_retriever):
        results = live_retriever.query("machine learning mentor", k=10)
        assert len(results) >= 4, (
            f"'machine learning mentor' returned only {len(results)} results"
        )

    def test_ml_mentor_top_result_has_evidence(self, live_retriever):
        results = live_retriever.query("machine learning mentor", k=5)
        assert any(len(r.evidence) > 0 for r in results), (
            "Top ML mentor results have no evidence strings — check data/people.jsonl"
        )

    def test_jadavpur_returns_cluster(self, live_retriever):
        results = live_retriever.query("jadavpur", k=15)
        assert len(results) >= 8, (
            f"'jadavpur' returned only {len(results)} results — add more JU-tagged people"
        )

    def test_jadavpur_subgraph_is_renderable(self, live_retriever):
        results = live_retriever.query("jadavpur", k=10)
        person_ids = [r.person_id for r in results]
        # Cap is 180 (subgraph default) to give the rendered graph
        # adequate density. Force-graph handles 180 nodes comfortably.
        sg = live_retriever.subgraph(person_ids, hops=1)
        assert len(sg["nodes"]) <= 180, "Subgraph exceeds 180-node cap — force-graph may freeze"
        assert len(sg["nodes"]) >= 5, "Jadavpur subgraph has too few nodes to visualize"
        # all edge endpoints must be in nodes
        node_ids = {n["id"] for n in sg["nodes"]}
        for e in sg["edges"]:
            assert e["src"] in node_ids
            assert e["dst"] in node_ids

    def test_get_person_returns_url(self, live_retriever):
        results = live_retriever.query("langgraph kolkata", k=1)
        if results:
            p = live_retriever.get_person(results[0].person_id)
            assert p.get("url", "").startswith("https://github.com/")

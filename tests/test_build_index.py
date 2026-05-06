"""
Unit tests for scripts/build_index.py helper functions.

No ChromaDB or sentence-transformer calls here — these test pure graph logic.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import networkx as nx
import pytest

from scripts.build_index import (
    EVENT_META,
    ORG_META,
    TYPE_EVENT,
    TYPE_ORG,
    TYPE_PERSON,
    TYPE_REPO,
    build_graph,
    compute_centrality,
    load_jsonl,
)
from tests.conftest import EDGES, PEOPLE, REPOS


# ---------------------------------------------------------------------------
# load_jsonl
# ---------------------------------------------------------------------------


class TestLoadJsonl:
    def test_reads_all_records(self, tmp_path):
        path = tmp_path / "test.jsonl"
        records = [{"id": "a", "val": 1}, {"id": "b", "val": 2}]
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        result = load_jsonl(path)
        assert result == records

    def test_skips_blank_lines(self, tmp_path):
        path = tmp_path / "blanks.jsonl"
        path.write_text('\n{"id": "x"}\n\n{"id": "y"}\n')
        result = load_jsonl(path)
        assert len(result) == 2

    def test_returns_empty_list_for_empty_file(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        assert load_jsonl(path) == []


# ---------------------------------------------------------------------------
# build_graph — node types
# ---------------------------------------------------------------------------


class TestBuildGraphNodes:
    @pytest.fixture(autouse=True)
    def graph(self):
        self.G = build_graph(PEOPLE, REPOS, EDGES)

    def test_person_nodes_exist(self):
        for p in PEOPLE:
            assert p["id"] in self.G.nodes, f"Person node '{p['id']}' missing"

    def test_person_nodes_have_correct_type(self):
        for p in PEOPLE:
            assert self.G.nodes[p["id"]]["type"] == TYPE_PERSON

    def test_person_node_carries_name(self):
        assert self.G.nodes["alice"]["name"] == "Alice Roy"

    def test_repo_nodes_exist(self):
        for r in REPOS:
            assert r["id"] in self.G.nodes, f"Repo node '{r['id']}' missing"

    def test_repo_nodes_have_correct_type(self):
        for r in REPOS:
            assert self.G.nodes[r["id"]]["type"] == TYPE_REPO

    def test_event_nodes_exist(self):
        for evt_id in EVENT_META:
            assert evt_id in self.G.nodes, f"Event node '{evt_id}' missing"

    def test_event_nodes_have_correct_type(self):
        for evt_id in EVENT_META:
            assert self.G.nodes[evt_id]["type"] == TYPE_EVENT

    def test_event_nodes_carry_name(self):
        # EVENT_META values are dicts of the form {name, patterns}; the
        # display name lives at meta["name"].
        for evt_id, meta in EVENT_META.items():
            expected_name = meta["name"] if isinstance(meta, dict) else meta
            assert self.G.nodes[evt_id]["name"] == expected_name

    def test_org_nodes_exist(self):
        for org_id in ORG_META:
            assert org_id in self.G.nodes, f"Org node '{org_id}' missing"

    def test_org_nodes_have_correct_type(self):
        for org_id in ORG_META:
            assert self.G.nodes[org_id]["type"] == TYPE_ORG

    def test_unknown_dst_is_dropped_in_kolkata_mode(self):
        """
        Strict-mode contract: edges to person IDs that aren't in
        `kolkata_ids` are dropped at build time. This is the gate that
        prevents global-network drift (e.g. Linus Torvalds) from entering
        the graph via `follows` edges from Kolkata seeds.
        """
        extra_edges = [{"src": "alice", "dst": "unknown_global_dev", "type": "follows"}]
        kolkata_ids = {p["id"] for p in PEOPLE}  # excludes 'unknown_global_dev'
        G = build_graph(PEOPLE, REPOS, extra_edges, kolkata_ids=kolkata_ids)
        assert "unknown_global_dev" not in G.nodes
        assert not G.has_edge("alice", "unknown_global_dev")

    def test_unknown_dst_auto_created_when_no_kolkata_gate(self):
        """
        Permissive-mode path: when `kolkata_ids` is None (e.g. a unit-test
        fixture), the gate is open and missing endpoints are materialised
        as person nodes.
        """
        extra_edges = [{"src": "alice", "dst": "unknown_person", "type": "follows"}]
        G = build_graph(PEOPLE, REPOS, extra_edges)
        assert "unknown_person" in G.nodes

    def test_total_node_count_at_least_people_plus_repos(self):
        assert self.G.number_of_nodes() >= len(PEOPLE) + len(REPOS)


# ---------------------------------------------------------------------------
# build_graph — edges
# ---------------------------------------------------------------------------


class TestBuildGraphEdges:
    @pytest.fixture(autouse=True)
    def graph(self):
        self.G = build_graph(PEOPLE, REPOS, EDGES)

    def test_maintains_edge_exists(self):
        assert self.G.has_edge("alice", "alice/langraph-demo")
        assert self.G.edges["alice", "alice/langraph-demo"]["type"] == "maintains"

    def test_follows_edge_exists(self):
        assert self.G.has_edge("alice", "bob")
        assert self.G.edges["alice", "bob"]["type"] == "follows"

    def test_attended_edge_exists(self):
        assert self.G.has_edge("alice", "evt_gdg_cloud_2024")
        assert self.G.edges["alice", "evt_gdg_cloud_2024"]["type"] == "attended"

    def test_member_of_edge_exists(self):
        assert self.G.has_edge("alice", "org_gdg_cloud_kolkata")

    def test_edge_count_matches_input(self):
        assert self.G.number_of_edges() == len(EDGES)

    def test_graph_is_directed(self):
        assert isinstance(self.G, nx.DiGraph)


# ---------------------------------------------------------------------------
# compute_centrality
# ---------------------------------------------------------------------------


class TestComputeCentrality:
    @pytest.fixture(autouse=True)
    def graph_with_centrality(self):
        self.G = build_graph(PEOPLE, REPOS, EDGES)
        compute_centrality(self.G)

    def test_all_nodes_have_centrality(self):
        for n in self.G.nodes:
            assert "centrality" in self.G.nodes[n], f"Node '{n}' missing centrality"

    def test_centrality_is_float(self):
        for n in self.G.nodes:
            assert isinstance(self.G.nodes[n]["centrality"], float)

    def test_centrality_is_positive(self):
        for n in self.G.nodes:
            assert self.G.nodes[n]["centrality"] > 0, f"Node '{n}' has zero centrality"

    def test_centrality_sums_to_approx_one(self):
        total = sum(self.G.nodes[n]["centrality"] for n in self.G.nodes)
        assert abs(total - 1.0) < 1e-6, f"PageRank should sum to ~1.0, got {total}"

    def test_hub_node_has_higher_centrality_than_leaf(self):
        # alice has 2 follows + 2 attended/member_of edges, charlie has fewer
        assert (
            self.G.nodes["alice"]["centrality"] > self.G.nodes["charlie"]["centrality"]
        )


# ---------------------------------------------------------------------------
# Round-trip: pickle dump/load preserves graph
# ---------------------------------------------------------------------------


class TestPickleRoundTrip:
    def test_graph_survives_pickle_round_trip(self, tmp_path):
        G = build_graph(PEOPLE, REPOS, EDGES)
        compute_centrality(G)

        pkl_path = tmp_path / "graph.pkl"
        with open(pkl_path, "wb") as f:
            pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)

        with open(pkl_path, "rb") as f:
            G2 = pickle.load(f)

        assert G2.number_of_nodes() == G.number_of_nodes()
        assert G2.number_of_edges() == G.number_of_edges()
        assert "centrality" in G2.nodes["alice"]
        assert G2.nodes["alice"]["centrality"] == G.nodes["alice"]["centrality"]

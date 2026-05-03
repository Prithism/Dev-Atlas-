"""
Member C contract. Member A imports Retriever from this module.
Do not change the public method signatures without coordinating with Member A.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import chromadb
import networkx as nx
from sentence_transformers import SentenceTransformer


@dataclass
class Result:
    person_id: str
    score: float
    evidence: list[str] = field(default_factory=list)


class Retriever:
    def __init__(self, graph_path: str = "data/graph.pkl", chroma_path: str = "data/chroma"):
        graph_file = Path(graph_path)
        if not graph_file.exists():
            raise FileNotFoundError(
                f"Graph not found at {graph_path}. Run: python scripts/build_index.py"
            )

        with open(graph_file, "rb") as f:
            self.G: nx.DiGraph = pickle.load(f)

        client = chromadb.PersistentClient(path=chroma_path)
        self.coll = client.get_collection("people")
        self.model = SentenceTransformer("all-MiniLM-L6-v2")

        # normalize centrality so it contributes meaningfully to scoring
        centralities = [
            self.G.nodes[n].get("centrality", 0.0) for n in self.G.nodes
        ]
        self._max_centrality = max(centralities) if centralities else 1.0

        # people lookup for get_person()
        self._people: dict[str, dict] = {}
        people_path = graph_file.parent / "people.jsonl"
        if people_path.exists():
            with open(people_path) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        p = json.loads(line)
                        self._people[p["id"]] = p

    # ------------------------------------------------------------------
    # Public API — do not rename or reorder parameters
    # ------------------------------------------------------------------

    def query(self, text: str, k: int = 10) -> list[Result]:
        """Vector search + 1-hop graph expansion, blended with centrality."""
        n_results = min(k * 2, self.coll.count())
        if n_results == 0:
            return []

        embedding = self.model.encode(text).tolist()
        raw = self.coll.query(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["metadatas", "documents", "distances"],
        )

        hits: dict[str, Result] = {}

        ids = raw["ids"][0] if raw["ids"] else []
        distances = raw["distances"][0] if raw["distances"] else []

        for pid, dist in zip(ids, distances):
            vec_score = max(0.0, 1.0 - float(dist))
            centrality = self.G.nodes[pid].get("centrality", 0.0) if pid in self.G else 0.0
            norm_centrality = centrality / self._max_centrality if self._max_centrality else 0.0
            score = 0.7 * vec_score + 0.3 * norm_centrality

            evidence = list(self._people.get(pid, {}).get("evidence", []))
            self._append_event_evidence(pid, evidence)

            hits[pid] = Result(person_id=pid, score=score, evidence=evidence)

            # 1-hop: pull direct neighbors that are also people
            if pid in self.G:
                for nbr in list(self.G.successors(pid)) + list(self.G.predecessors(pid)):
                    if nbr in hits:
                        continue
                    node_data = self.G.nodes.get(nbr, {})
                    if node_data.get("type") != "person":
                        continue
                    nbr_centrality = node_data.get("centrality", 0.0) / self._max_centrality
                    # neighbors score lower than the direct hit
                    nbr_score = 0.7 * vec_score * 0.5 + 0.3 * nbr_centrality
                    nbr_evidence = list(self._people.get(nbr, {}).get("evidence", []))
                    self._append_event_evidence(nbr, nbr_evidence)
                    hits[nbr] = Result(person_id=nbr, score=nbr_score, evidence=nbr_evidence)

        ranked = sorted(hits.values(), key=lambda r: r.score, reverse=True)
        return ranked[:k]

    def subgraph(self, person_ids: list[str], hops: int = 1) -> dict:
        """Return a D3-compatible subgraph dict for the given person IDs."""
        nodes: set[str] = set()
        for pid in person_ids:
            if pid not in self.G:
                continue
            ego = nx.ego_graph(self.G.to_undirected(), pid, radius=hops)
            nodes.update(ego.nodes())

        # cap at 50 nodes by centrality to keep D3 rendering fast
        if len(nodes) > 50:
            nodes = set(
                sorted(
                    nodes,
                    key=lambda n: self.G.nodes[n].get("centrality", 0.0),
                    reverse=True,
                )[:50]
            )

        edges = [
            (s, d)
            for s, d in self.G.edges()
            if s in nodes and d in nodes
        ]

        return {
            "nodes": [
                {
                    "id": n,
                    "label": self.G.nodes[n].get("name", n),
                    "type": self.G.nodes[n].get("type", "person"),
                    "centrality": self.G.nodes[n].get("centrality", 0.0),
                }
                for n in nodes
            ],
            "edges": [
                {
                    "src": s,
                    "dst": d,
                    "type": self.G.edges[s, d].get("type", ""),
                }
                for s, d in edges
            ],
        }

    def get_person(self, person_id: str) -> dict:
        """Full record for the result list. Returns {} if not found."""
        return self._people.get(person_id, {})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_event_evidence(self, pid: str, evidence: list[str]) -> None:
        if pid not in self.G:
            return
        for nbr in list(self.G.successors(pid)):
            node_data = self.G.nodes.get(nbr, {})
            if node_data.get("type") == "event":
                label = node_data.get("name", nbr)
                snippet = f"Attended {label}"
                if snippet not in evidence:
                    evidence.append(snippet)

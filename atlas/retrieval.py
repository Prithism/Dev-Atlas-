"""
Member C contract. Member A imports Retriever from this module.
Do not change the public method signatures without coordinating with Member A.
"""

from __future__ import annotations

import json
import logging
import pickle
import re
from dataclasses import dataclass, field
from pathlib import Path

import chromadb
import networkx as nx
from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)


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
        self.model = None
        try:
            self.model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as exc:
            log.warning(
                "SentenceTransformer unavailable, falling back to keyword retrieval: %s",
                exc,
            )

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

        self._search_docs: dict[str, str] = {}
        for pid, person in self._people.items():
            repo_text = []
            if pid in self.G:
                for nbr in self.G.successors(pid):
                    node_data = self.G.nodes.get(nbr, {})
                    if node_data.get("type") == "repo":
                        repo_text.append(node_data.get("description", ""))
                        repo_text.append(node_data.get("name", nbr))
                    elif node_data.get("type") == "event":
                        repo_text.append(node_data.get("name", nbr))
            self._search_docs[pid] = " ".join(
                filter(
                    None,
                    [
                        person.get("name", ""),
                        person.get("bio", ""),
                        person.get("location", ""),
                        " ".join(person.get("languages", [])),
                        " ".join(person.get("evidence", [])),
                        " ".join(repo_text),
                    ],
                )
            ).lower()

    # ------------------------------------------------------------------
    # Public API — do not rename or reorder parameters
    # ------------------------------------------------------------------

    def query(self, text: str, k: int = 10) -> list[Result]:
        """Vector search + 1-hop graph expansion, blended with centrality."""
        if self.model is not None:
            try:
                n_results = min(k * 2, self.coll.count())
                if n_results > 0:
                    embedding = self.model.encode(text).tolist()
                    raw = self.coll.query(
                        query_embeddings=[embedding],
                        n_results=n_results,
                        include=["metadatas", "documents", "distances"],
                    )
                    return self._expand_ranked_hits(
                        [
                            (pid, max(0.0, 1.0 - float(dist)))
                            for pid, dist in zip(
                                raw["ids"][0] if raw["ids"] else [],
                                raw["distances"][0] if raw["distances"] else [],
                            )
                        ],
                        k=k,
                    )
            except Exception as exc:
                log.warning("Vector retrieval failed, falling back to keyword search: %s", exc)

        return self._keyword_query(text, k=k)

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

    def _expand_ranked_hits(self, ranked_hits: list[tuple[str, float]], k: int) -> list[Result]:
        hits: dict[str, Result] = {}

        for pid, relevance_score in ranked_hits:
            if pid not in self._people:
                continue

            centrality = self.G.nodes[pid].get("centrality", 0.0) if pid in self.G else 0.0
            norm_centrality = centrality / self._max_centrality if self._max_centrality else 0.0
            score = 0.7 * relevance_score + 0.3 * norm_centrality

            evidence = list(self._people.get(pid, {}).get("evidence", []))
            self._append_event_evidence(pid, evidence)
            hits[pid] = Result(person_id=pid, score=score, evidence=evidence)

            if pid in self.G:
                for nbr in list(self.G.successors(pid)) + list(self.G.predecessors(pid)):
                    if nbr in hits:
                        continue
                    node_data = self.G.nodes.get(nbr, {})
                    if node_data.get("type") != "person":
                        continue
                    nbr_centrality = node_data.get("centrality", 0.0) / self._max_centrality
                    nbr_score = 0.7 * relevance_score * 0.5 + 0.3 * nbr_centrality
                    nbr_evidence = list(self._people.get(nbr, {}).get("evidence", []))
                    self._append_event_evidence(nbr, nbr_evidence)
                    hits[nbr] = Result(person_id=nbr, score=nbr_score, evidence=nbr_evidence)

        ranked = sorted(hits.values(), key=lambda r: r.score, reverse=True)
        return ranked[:k]

    def _keyword_query(self, text: str, k: int = 10) -> list[Result]:
        terms = self._tokenize(text)
        if not terms:
            return []

        ranked_hits: list[tuple[str, float]] = []
        for pid, doc in self._search_docs.items():
            if not doc:
                continue
            matches = sum(1 for term in terms if term in doc)
            if matches == 0:
                continue
            relevance = matches / len(terms)
            ranked_hits.append((pid, relevance))

        ranked_hits.sort(key=lambda item: item[1], reverse=True)
        return self._expand_ranked_hits(ranked_hits[: k * 2], k=k)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 1]

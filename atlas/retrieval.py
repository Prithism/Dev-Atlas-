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

        # people lookup for get_person().
        #
        # IMPORTANT: gate on graph membership. The JSONL file is the
        # ingest interchange format and may still contain people who
        # failed the Kolkata signal gate (build_index.py drops them
        # from the graph + Chroma but does not rewrite JSONL). Without
        # this gate, the keyword-boost path in _blend_keyword_match
        # could surface filtered-out global accounts by literal token
        # match in their bios.
        self._people: dict[str, dict] = {}
        people_path = graph_file.parent / "people.jsonl"
        if people_path.exists():
            with open(people_path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    p = json.loads(line)
                    if p["id"] in self.G:
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
        """
        Hybrid search: vector similarity blended with literal keyword
        match, then 1-hop graph expansion with centrality.

        The hybrid blend exists because pure vector retrieval against
        all-MiniLM-L6-v2 gets dominated by the most frequent term in
        a query. For "langgraph kolkata", the embedding leans on
        "Kolkata" (common) and people who literally maintain LangGraph
        repos (rare token) end up outranked. The keyword boost gives
        rare query tokens explicit weight.
        """
        if self.model is not None:
            try:
                # Pull a wider candidate set (k * 4) than we return, so the
                # hybrid re-rank has room to promote keyword-matching
                # candidates from outside the strict vector top-k.
                n_results = min(max(k * 4, 25), self.coll.count())
                if n_results > 0:
                    embedding = self.model.encode(text).tolist()
                    raw = self.coll.query(
                        query_embeddings=[embedding],
                        n_results=n_results,
                        include=["metadatas", "documents", "distances"],
                    )
                    vector_hits = [
                        (pid, max(0.0, 1.0 - float(dist)))
                        for pid, dist in zip(
                            raw["ids"][0] if raw["ids"] else [],
                            raw["distances"][0] if raw["distances"] else [],
                        )
                    ]
                    blended = self._blend_keyword_match(text, vector_hits)
                    return self._expand_ranked_hits(blended, k=k)
            except Exception as exc:
                log.warning("Vector retrieval failed, falling back to keyword search: %s", exc)

        return self._keyword_query(text, k=k)

    def subgraph(
        self,
        person_ids: list[str],
        hops: int = 2,
        max_nodes: int = 180,
    ) -> dict:
        """
        Return a D3-compatible subgraph dict for the given person IDs.

        Defaults are tuned for graph density:
          - hops=2: 1-hop only returns each seed + its repos, which
            produces disconnected stars. 2-hop traverses through shared
            events / orgs and gets a properly interconnected graph.
          - max_nodes=180: enough for a dense rendered graph without
            overwhelming the force-directed layout.

        Selection strategy when capping:
          - Always keep the seed person nodes.
          - Keep all bridge nodes (event, org) inside the ball — these are
            what make the graph look connected.
          - Fill remaining slots by centrality, but **boost nodes that
            connect to multiple seeds** so the result favours bridges over
            leaves.
        """
        if not person_ids:
            return self._empty_subgraph()

        seeds = [pid for pid in person_ids if pid in self.G]
        if not seeds:
            return self._empty_subgraph()

        undirected = self.G.to_undirected()

        # Per-seed shells let us count how many seeds each candidate node
        # is reachable from -- our "shared connection" signal.
        shell_membership: dict[str, set[str]] = {}
        for pid in seeds:
            ego = nx.ego_graph(undirected, pid, radius=hops)
            for n in ego.nodes():
                shell_membership.setdefault(n, set()).add(pid)

        candidate_nodes = set(shell_membership.keys())

        if len(candidate_nodes) <= max_nodes:
            kept = candidate_nodes
        else:
            kept = self._select_subgraph_nodes(
                candidate_nodes,
                seeds=set(seeds),
                shell_membership=shell_membership,
                max_nodes=max_nodes,
            )

        return self._materialise_subgraph(kept)

    def full_graph(
        self,
        max_nodes: int = 350,
        include_types: tuple[str, ...] | None = None,
    ) -> dict:
        """
        Return the entire Kolkata graph for the no-query landing view.

        Selection is connectivity-aware: we pick top persons by centrality,
        keep all bridge nodes (event/org) -- they're cheap and they're what
        make the graph look connected -- and then fill the repo quota with
        repos *owned by the kept persons*. This guarantees every repo on
        screen has a visible owner, so edges are dense and meaningful.

        Without the connectivity awareness, picking top repos and top
        persons independently leaves most repos orphaned: their owner
        didn't make the cut, and the rendered graph looks like a list of
        floating project names.
        """
        all_nodes = list(self.G.nodes(data=True))
        if include_types:
            allowed = set(include_types)
            all_nodes = [(n, d) for n, d in all_nodes if d.get("type") in allowed]

        if len(all_nodes) <= max_nodes:
            return self._materialise_subgraph({n for n, _ in all_nodes})

        by_type: dict[str, list[tuple[str, dict]]] = {}
        for n, d in all_nodes:
            by_type.setdefault(d.get("type", "person"), []).append((n, d))

        # Quotas. People are the point of the atlas; repos exist to
        # support them. Events + orgs are cheap and always included.
        person_quota = max(1, int(max_nodes * 0.65))
        repo_quota = max(1, int(max_nodes * 0.30))
        # bridge quota = whatever is left; in practice always accommodates
        # the small fixed list of event/org nodes.

        def top_by_centrality(items, k):
            return [
                n for n, _ in sorted(
                    items,
                    key=lambda nd: nd[1].get("centrality", 0.0),
                    reverse=True,
                )[:k]
            ]

        kept: set[str] = set()

        # 1. Top persons by centrality.
        top_persons = top_by_centrality(by_type.get("person", []), person_quota)
        kept.update(top_persons)
        kept_persons_set = set(top_persons)

        # 2. ALL bridge nodes (events + orgs). There are at most a few
        #    dozen. They are the connective tissue of the rendered graph.
        for n, _ in by_type.get("event", []):
            kept.add(n)
        for n, _ in by_type.get("org", []):
            kept.add(n)

        # 3. Repos OWNED BY kept persons, ranked by repo centrality.
        #    This is the connectivity-aware part: every repo we show is
        #    guaranteed to have at least one visible owner edge.
        repos_with_owner: list[tuple[str, dict]] = []
        for n, d in by_type.get("repo", []):
            # The owner is the source of a `maintains` edge into this repo.
            owners = [
                src for src, _, edata in self.G.in_edges(n, data=True)
                if edata.get("type") == "maintains"
            ]
            if any(o in kept_persons_set for o in owners):
                repos_with_owner.append((n, d))

        kept.update(top_by_centrality(repos_with_owner, repo_quota))

        # 4. If still under cap, top up with anything else by centrality.
        if len(kept) < max_nodes:
            remaining = [
                (n, d) for n, d in all_nodes if n not in kept
            ]
            slots_left = max_nodes - len(kept)
            kept.update(top_by_centrality(remaining, slots_left))

        # 5. Final safety trim: if step 2 (all bridges) plus minimums
        #    pushed us over the cap (only possible when max_nodes is very
        #    small in tests), keep the top-N by centrality.
        if len(kept) > max_nodes:
            scored = [(n, self.G.nodes[n]) for n in kept]
            kept = set(top_by_centrality(scored, max_nodes))

        return self._materialise_subgraph(kept)

    def get_person(self, person_id: str) -> dict:
        """Full record for the result list. Returns {} if not found."""
        return self._people.get(person_id, {})

    # ------------------------------------------------------------------
    # Subgraph helpers
    # ------------------------------------------------------------------

    def _select_subgraph_nodes(
        self,
        candidates: set[str],
        seeds: set[str],
        shell_membership: dict[str, set[str]],
        max_nodes: int,
    ) -> set[str]:
        """Choose which nodes survive the cap. Always keeps seeds and bridges."""
        # 1. Always keep all seeds (the user explicitly asked for these).
        kept: set[str] = {s for s in seeds if s in candidates}

        # 2. Always keep bridge nodes (event/org) inside the ball -- they
        #    are what makes the graph visibly interconnected.
        for n in candidates:
            ntype = self.G.nodes[n].get("type")
            if ntype in ("event", "org"):
                kept.add(n)

        # 3. Score remaining candidates: shared-with-many-seeds beats
        #    centrality-alone. A repo connected to 3 different seeds is
        #    a stronger story than a high-PageRank node connected to 1.
        remaining = candidates - kept
        scored: list[tuple[str, float]] = []
        for n in remaining:
            shared = len(shell_membership.get(n, ()))
            centrality = self.G.nodes[n].get("centrality", 0.0) / max(self._max_centrality, 1e-9)
            # Heavy weight on shared-seed-count; centrality as tie-breaker.
            score = shared * 1.0 + centrality * 0.25
            scored.append((n, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        budget = max_nodes - len(kept)
        if budget > 0:
            kept.update(n for n, _ in scored[:budget])

        return kept

    def _materialise_subgraph(self, nodes: set[str]) -> dict:
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

    def _empty_subgraph(self) -> dict:
        return {"nodes": [], "edges": []}

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

    # Tokens we ignore for keyword boosting because they're either too
    # generic (everyone in the index has them) or too short to be a
    # meaningful match.
    _KEYWORD_STOPWORDS: frozenset[str] = frozenset({
        "kolkata", "calcutta", "india", "bengal", "west",
        "the", "and", "for", "with", "who", "what", "from",
        "are", "is", "of", "to", "in", "on",
    })

    def _blend_keyword_match(
        self,
        query_text: str,
        vector_hits: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        """
        Add a literal-keyword boost on top of vector scores.

        For every query token that's >= 4 chars and not a stopword, if
        it appears literally in a candidate's indexed text, add 0.18 to
        the candidate's score. The boost is capped at +0.45 so a person
        with one strong technical match can outrank a Kolkata-resident
        with high vector similarity but no token match.

        We also bring in candidates that match keywords but didn't make
        the vector top-N -- this handles the case where the embedding
        shadowed the rare token entirely.
        """
        tokens = [
            t for t in self._tokenize(query_text)
            if len(t) >= 4 and t not in self._KEYWORD_STOPWORDS
        ]
        if not tokens:
            return vector_hits

        boost_per_token = 0.18
        max_boost = 0.45

        scores: dict[str, float] = dict(vector_hits)

        # Boost candidates already in the vector hits.
        for pid, base in vector_hits:
            doc = self._search_docs.get(pid, "")
            if not doc:
                continue
            matches = sum(1 for t in tokens if t in doc)
            if matches:
                scores[pid] = min(1.0, base + min(matches * boost_per_token, max_boost))

        # Pull in keyword-only candidates the vector index missed entirely.
        for pid, doc in self._search_docs.items():
            if pid in scores or not doc:
                continue
            matches = sum(1 for t in tokens if t in doc)
            if matches:
                # No vector score for these; treat as moderate keyword-only
                # relevance. They still need to compete with vector winners
                # via the boost, so cap the floor at 0.4.
                scores[pid] = min(0.85, 0.4 + min(matches * boost_per_token, max_boost))

        merged = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return merged

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

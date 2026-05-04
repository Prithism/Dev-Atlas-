"""
Build graph.pkl and ChromaDB index from data/*.jsonl.

Run once before the sprint, and again if you update the JSONL files:
    python scripts/build_index.py

Outputs:
    data/graph.pkl   — NetworkX DiGraph with centrality attributes
    data/chroma/     — ChromaDB persistent collection "people"
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import chromadb
import networkx as nx
from sentence_transformers import SentenceTransformer

DATA_DIR = Path("data")
GRAPH_PATH = DATA_DIR / "graph.pkl"
CHROMA_PATH = str(DATA_DIR / "chroma")
MODEL_NAME = "all-MiniLM-L6-v2"

# Node type constants
TYPE_PERSON = "person"
TYPE_REPO = "repo"
TYPE_EVENT = "event"
TYPE_ORG = "org"

# Event node metadata (name lookup for evidence strings)
EVENT_META: dict[str, str] = {
    "evt_gdg_cloud_2024": "GDG Cloud Kolkata DevFest 2024",
    "evt_gdg_cloud_2023": "GDG Cloud Kolkata DevFest 2023",
    "evt_pycon_india_2023": "PyCon India 2023",
    "evt_devfest_kolkata_2024": "Devfest Kolkata 2024",
    "evt_bangla_python_2024": "Bangla Python Meetup 2024",
    "evt_fossasia_2024": "FOSSASIA 2024",
    "evt_fossasia_2023": "FOSSASIA 2023",
}

ORG_META: dict[str, str] = {
    "org_gdg_cloud_kolkata": "GDG Cloud Kolkata",
    "org_jadavpur_cs": "Jadavpur University CS Department",
    "org_iit_kgp": "IIT Kharagpur",
    "org_iiit_kalyani": "IIIT Kalyani",
    "org_iiest": "IIEST Shibpur",
    "org_women_techmakers": "Google Women Techmakers Kolkata",
}


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def build_graph(people: list[dict], repos: list[dict], edges: list[dict]) -> nx.DiGraph:
    G = nx.DiGraph()

    for p in people:
        G.add_node(
            p["id"],
            type=TYPE_PERSON,
            name=p.get("name", p["id"]),
            bio=p.get("bio", ""),
            location=p.get("location", ""),
            followers=p.get("followers", 0),
            url=p.get("url", ""),
            languages=p.get("languages", []),
            evidence=p.get("evidence", []),
        )

    for r in repos:
        G.add_node(
            r["id"],
            type=TYPE_REPO,
            name=r.get("id"),
            description=r.get("description", ""),
            stars=r.get("stars", 0),
            language=r.get("language", ""),
            topics=r.get("topics", []),
        )

    for evt_id, evt_name in EVENT_META.items():
        G.add_node(evt_id, type=TYPE_EVENT, name=evt_name)

    for org_id, org_name in ORG_META.items():
        G.add_node(org_id, type=TYPE_ORG, name=org_name)

    for e in edges:
        src, dst, etype = e["src"], e["dst"], e.get("type", "")
        # auto-create unknown nodes rather than dropping the edge
        if src not in G:
            G.add_node(src, type=TYPE_PERSON, name=src)
        if dst not in G:
            G.add_node(dst, type=TYPE_PERSON, name=dst)
        G.add_edge(src, dst, type=etype)

    return G


def compute_centrality(G: nx.DiGraph) -> None:
    """PageRank over undirected projection, stored as node attribute."""
    undirected = G.to_undirected()
    pr = nx.pagerank(undirected, alpha=0.85)
    nx.set_node_attributes(G, pr, "centrality")


def build_chroma(
    people: list[dict],
    repos: list[dict],
    chroma_path: str,
    model: SentenceTransformer,
) -> None:
    client = chromadb.PersistentClient(path=chroma_path)

    # wipe and recreate so re-runs are idempotent
    try:
        client.delete_collection("people")
    except Exception:
        pass

    coll = client.create_collection(
        "people",
        metadata={"hnsw:space": "cosine"},
    )

    repo_by_owner: dict[str, list[dict]] = {}
    for r in repos:
        owner = r.get("owner", "")
        repo_by_owner.setdefault(owner, []).append(r)

    ids, embeddings, metadatas, documents = [], [], [], []

    for p in people:
        pid = p["id"]
        bio = p.get("bio", "")
        langs = " ".join(p.get("languages", []))
        evidence = " ".join(p.get("evidence", []))
        repo_descs = " ".join(
            r.get("description", "") + " " + " ".join(r.get("topics", []))
            for r in repo_by_owner.get(pid, [])
        )
        text = f"{bio} {langs} {evidence} {repo_descs}".strip()

        ids.append(pid)
        embeddings.append(model.encode(text).tolist())
        metadatas.append({"name": p.get("name", pid), "url": p.get("url", "")})
        documents.append(text)

    # batch upsert
    batch_size = 64
    for i in range(0, len(ids), batch_size):
        coll.add(
            ids=ids[i : i + batch_size],
            embeddings=embeddings[i : i + batch_size],
            metadatas=metadatas[i : i + batch_size],
            documents=documents[i : i + batch_size],
        )

    print(f"  Indexed {len(ids)} people into ChromaDB.")


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    for fname in ("people.jsonl", "repos.jsonl", "edges.jsonl"):
        if not (DATA_DIR / fname).exists():
            print(f"ERROR: {DATA_DIR / fname} not found. Run scripts/ingest.py first.")
            sys.exit(1)

    print("Loading JSONL files...")
    people = load_jsonl(DATA_DIR / "people.jsonl")
    repos = load_jsonl(DATA_DIR / "repos.jsonl")
    edges = load_jsonl(DATA_DIR / "edges.jsonl")
    print(f"  {len(people)} people, {len(repos)} repos, {len(edges)} edges")

    print("Building NetworkX graph...")
    G = build_graph(people, repos, edges)
    print(f"  {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    print("Computing PageRank centrality...")
    compute_centrality(G)

    print(f"Saving graph to {GRAPH_PATH}...")
    with open(GRAPH_PATH, "wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)

    print("Loading sentence-transformer model (may download on first run)...")
    model = SentenceTransformer(MODEL_NAME)

    print("Building ChromaDB index...")
    build_chroma(people, repos, CHROMA_PATH, model)

    print("\nDone. Smoke-test the retriever:")
    print("  python -c \"from atlas.retrieval import Retriever; r = Retriever(); print(r.query('langgraph kolkata'))\"")


if __name__ == "__main__":
    main()

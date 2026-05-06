"""
Build graph.pkl and ChromaDB index from data/*.jsonl.

Outputs:
    data/graph.pkl   — NetworkX DiGraph with centrality attributes
    data/chroma/     — ChromaDB persistent collection "people"

This script is also responsible for:
    1. Gating every person on a `kolkata_signal` so non-Kolkata accounts
       never enter the graph or the vector index — even when seed Kolkata
       users follow them.
    2. Synthesising `attended` and `member_of` edges from the evidence
       strings, so people are connected through shared events and orgs
       rather than only through their own repos. Without this the graph
       degenerates to a star pattern: every person points at their own
       repos and nothing else.
"""

from __future__ import annotations

import json
import pickle
import re
import sys
from pathlib import Path

import chromadb
import networkx as nx
from sentence_transformers import SentenceTransformer

DATA_DIR = Path("data")
GRAPH_PATH = DATA_DIR / "graph.pkl"
CHROMA_PATH = str(DATA_DIR / "chroma")
DENYLIST_PATH = DATA_DIR / "denylist.txt"
ALLOWLIST_PATH = DATA_DIR / "kolkata_seeds.txt"
MODEL_NAME = "all-MiniLM-L6-v2"

# Node type constants
TYPE_PERSON = "person"
TYPE_REPO = "repo"
TYPE_EVENT = "event"
TYPE_ORG = "org"

# Event node metadata. The keys are stable IDs; the values are the canonical
# display names. The patterns column is what we look for in evidence strings
# to synthesise an `attended` edge.
EVENT_META: dict[str, dict] = {
    "evt_gdg_cloud_2024": {
        "name": "GDG Cloud Kolkata DevFest 2024",
        "patterns": ["gdg cloud kolkata", "gdg cloud kolkata 2024", "devfest 2024 kolkata"],
    },
    "evt_gdg_cloud_2023": {
        "name": "GDG Cloud Kolkata DevFest 2023",
        "patterns": ["gdg cloud kolkata 2023", "devfest 2023 kolkata"],
    },
    "evt_pycon_india_2023": {
        "name": "PyCon India 2023",
        "patterns": ["pycon india 2023", "pycon india"],
    },
    "evt_devfest_kolkata_2024": {
        "name": "Devfest Kolkata 2024",
        "patterns": ["devfest kolkata 2024", "devfest kolkata"],
    },
    "evt_bangla_python_2024": {
        "name": "Bangla Python Meetup 2024",
        "patterns": ["bangla python 2024", "bangla python meetup", "bangla-python"],
    },
    "evt_fossasia_2024": {
        "name": "FOSSASIA 2024",
        "patterns": ["fossasia 2024"],
    },
    "evt_fossasia_2023": {
        "name": "FOSSASIA 2023",
        "patterns": ["fossasia 2023"],
    },
}

ORG_META: dict[str, dict] = {
    "org_gdg_cloud_kolkata": {
        "name": "GDG Cloud Kolkata",
        "patterns": ["gdg cloud kolkata", "gdg kolkata", "google developer group kolkata"],
    },
    "org_jadavpur_cs": {
        "name": "Jadavpur University CS Department",
        "patterns": ["jadavpur university", "jadavpur", "ju cs", "jadavpur cse"],
    },
    "org_iit_kgp": {
        "name": "IIT Kharagpur",
        "patterns": ["iit kharagpur", "iit kgp", "iitkgp"],
    },
    "org_iiit_kalyani": {
        "name": "IIIT Kalyani",
        "patterns": ["iiit kalyani"],
    },
    "org_iiest": {
        "name": "IIEST Shibpur",
        "patterns": ["iiest shibpur", "iiest", "besu", "be college shibpur"],
    },
    "org_women_techmakers": {
        "name": "Google Women Techmakers Kolkata",
        "patterns": ["women techmakers kolkata", "wtm kolkata"],
    },
}

# Tokens that indicate a Kolkata grounding when found in `location`.
# Order matters only for readability; matching is OR.
KOLKATA_LOCATION_TOKENS: tuple[str, ...] = (
    "kolkata",
    "calcutta",
    "west bengal",
    "wb,",
    " wb ",
    " wb",
    "howrah",
    "salt lake",
    "jadavpur",
    "dum dum",
    "santragachi",
    "barrackpore",
    "siliguri",
    "kharagpur",      # IIT KGP feeds into the Kolkata tech network
    "durgapur",
    "asansol",
    "raniganj",
    "bidhan",
    "birbhum",
    "bardhaman",
    "burdwan",
)

# Identifiers (org/event nodes) that, if connected to a person via edges,
# count as a Kolkata signal even if the person's GitHub location is empty.
KOLKATA_BRIDGE_IDS: frozenset[str] = frozenset(
    [
        "org_gdg_cloud_kolkata",
        "org_jadavpur_cs",
        "org_iiit_kalyani",
        "org_iiest",
        "org_women_techmakers",
        "evt_gdg_cloud_2024",
        "evt_gdg_cloud_2023",
        "evt_devfest_kolkata_2024",
        "evt_bangla_python_2024",
    ]
)


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_id_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


# ---------------------------------------------------------------------------
# Kolkata signal: who counts, who doesn't
# ---------------------------------------------------------------------------


def _evidence_text(person: dict) -> str:
    parts: list[str] = []
    for item in person.get("evidence", []) or []:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            bullet = item.get("bullet")
            if isinstance(bullet, str):
                parts.append(bullet)
    parts.append(person.get("bio") or "")
    return " ".join(parts).lower()


def derive_kolkata_signal(
    person: dict,
    edges_by_src: dict[str, list[dict]],
    allowlist: set[str],
    denylist: set[str],
) -> tuple[str | None, str | None]:
    """
    Returns (kolkata_signal, evidence_url).

    kolkata_signal is one of:
        "github_location"   — `location` field matches a Kolkata token
        "event_attended"    — has an edge to a Kolkata event node
        "org_member"        — has an edge to a Kolkata org node
        "manual_curation"   — id is listed in data/kolkata_seeds.txt
        None                — does not qualify; person will be filtered out

    Returns (None, None) if the person cannot be grounded in Kolkata.
    """
    pid = person.get("id", "")

    if pid in denylist:
        return None, None

    if pid in allowlist:
        return "manual_curation", "data/kolkata_seeds.txt"

    location = (person.get("location") or "").lower()
    if any(token in location for token in KOLKATA_LOCATION_TOKENS):
        return "github_location", person.get("url", "")

    # Edge-based grounding (rarely fires on the current dataset because
    # most attended/member_of edges are synthesised after this gate runs;
    # it's the path that keeps discovery honest as more event/org edges
    # materialise from explicit sources).
    for edge in edges_by_src.get(pid, []):
        dst = edge.get("dst", "")
        if dst in KOLKATA_BRIDGE_IDS:
            etype = edge.get("type", "")
            if etype == "attended":
                return "event_attended", dst
            if etype == "member_of":
                return "org_member", dst
            # any other edge to a known Kolkata bridge node also counts
            return "org_member", dst

    return None, None


# ---------------------------------------------------------------------------
# Edge synthesis: turn evidence strings into attended/member_of edges
# ---------------------------------------------------------------------------


def synthesise_bridge_edges(people: list[dict]) -> list[dict]:
    """
    Read each person's evidence + bio, pattern-match against EVENT_META and
    ORG_META, and emit `attended` / `member_of` edges. Without these, the
    only person-to-person paths in the graph go through `follows`, which is
    sparse (186 edges across 433 people). With these, people who attended
    the same event / studied at the same college share a bridge node and
    become 2-hop reachable. That's what makes the graph look connected.
    """
    out: list[dict] = []
    for person in people:
        pid = person.get("id")
        if not pid:
            continue
        haystack = _evidence_text(person)
        if not haystack:
            continue

        for evt_id, meta in EVENT_META.items():
            if any(p in haystack for p in meta["patterns"]):
                out.append({"src": pid, "dst": evt_id, "type": "attended"})

        for org_id, meta in ORG_META.items():
            if any(p in haystack for p in meta["patterns"]):
                out.append({"src": pid, "dst": org_id, "type": "member_of"})

    return out


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph(
    people: list[dict],
    repos: list[dict],
    edges: list[dict],
    kolkata_ids: set[str] | None = None,
) -> nx.DiGraph:
    """
    Build the NetworkX graph.

    Two modes:
      - `kolkata_ids=None` (permissive, used by unit-test fixtures):
        every person is admitted, and edges whose endpoints we have not
        seen yet are auto-materialised as person nodes.
      - `kolkata_ids=<set>` (strict, production):
        only people in the set become nodes, edges to anyone outside the
        set are dropped. This is what stops global drift (Linus Torvalds
        etc.) from entering via `follows` edges from Kolkata seeds.
    """
    strict = kolkata_ids is not None
    if not strict:
        kolkata_ids = {p["id"] for p in people}

    G = nx.DiGraph()

    # 1. People nodes — only Kolkata-grounded people get a node.
    for p in people:
        pid = p["id"]
        if pid not in kolkata_ids:
            continue
        G.add_node(
            pid,
            type=TYPE_PERSON,
            name=p.get("name", pid),
            bio=p.get("bio", ""),
            location=p.get("location", ""),
            followers=p.get("followers", 0),
            url=p.get("url", ""),
            languages=p.get("languages", []),
            evidence=p.get("evidence", []),
            kolkata_signal=p.get("kolkata_signal"),
        )

    # 2. Repo nodes — only repos owned by a Kolkata person are useful.
    repo_ids_kept: set[str] = set()
    for r in repos:
        rid = r["id"]
        owner = r.get("owner", "")
        if owner and owner not in kolkata_ids:
            # repo belongs to someone we filtered out; drop it
            continue
        G.add_node(
            rid,
            type=TYPE_REPO,
            name=rid,
            description=r.get("description", ""),
            stars=r.get("stars", 0),
            language=r.get("language", ""),
            topics=r.get("topics", []),
        )
        repo_ids_kept.add(rid)

    # 3. Bridge nodes — events and orgs.
    for evt_id, meta in EVENT_META.items():
        G.add_node(evt_id, type=TYPE_EVENT, name=meta["name"])
    for org_id, meta in ORG_META.items():
        G.add_node(org_id, type=TYPE_ORG, name=meta["name"])

    # 4. Edges.
    for e in edges:
        src, dst, etype = e["src"], e["dst"], e.get("type", "")

        if strict:
            # Drop any edge whose endpoint we filtered out.
            src_ok = src in G or src in kolkata_ids
            dst_ok = (
                dst in G
                or dst in kolkata_ids
                or dst in EVENT_META
                or dst in ORG_META
            )
            if not src_ok or not dst_ok:
                continue

            if src not in G and src in kolkata_ids:
                G.add_node(src, type=TYPE_PERSON, name=src)
            if dst not in G:
                if dst in kolkata_ids:
                    G.add_node(dst, type=TYPE_PERSON, name=dst)
                elif dst in EVENT_META:
                    G.add_node(dst, type=TYPE_EVENT, name=EVENT_META[dst]["name"])
                elif dst in ORG_META:
                    G.add_node(dst, type=TYPE_ORG, name=ORG_META[dst]["name"])
        else:
            # legacy: auto-create unknown person nodes rather than dropping.
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
    kolkata_ids: set[str] | None = None,
) -> None:
    if kolkata_ids is None:
        kolkata_ids = {p["id"] for p in people}

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
        if pid not in kolkata_ids:
            continue
        bio = p.get("bio", "")
        langs = " ".join(p.get("languages", []))
        ev_strings: list[str] = []
        for item in p.get("evidence", []) or []:
            if isinstance(item, str):
                ev_strings.append(item)
            elif isinstance(item, dict):
                b = item.get("bullet")
                if isinstance(b, str):
                    ev_strings.append(b)
        evidence = " ".join(ev_strings)
        repo_descs = " ".join(
            r.get("description", "") + " " + " ".join(r.get("topics", []))
            for r in repo_by_owner.get(pid, [])
        )
        text = f"{bio} {langs} {evidence} {repo_descs}".strip()

        ids.append(pid)
        embeddings.append(model.encode(text).tolist())
        metadatas.append({"name": p.get("name", pid), "url": p.get("url", "")})
        documents.append(text)

    batch_size = 64
    for i in range(0, len(ids), batch_size):
        coll.add(
            ids=ids[i : i + batch_size],
            embeddings=embeddings[i : i + batch_size],
            metadatas=metadatas[i : i + batch_size],
            documents=documents[i : i + batch_size],
        )

    print(f"  Indexed {len(ids)} Kolkata-grounded people into ChromaDB.")


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    for fname in ("people.jsonl", "repos.jsonl", "edges.jsonl"):
        if not (DATA_DIR / fname).exists():
            print(f"ERROR: {DATA_DIR / fname} not found. Run scripts/ingest.py first.")
            sys.exit(1)

    print("Loading JSONL files...")
    people = load_jsonl(DATA_DIR / "people.jsonl")
    repos = load_jsonl(DATA_DIR / "repos.jsonl")
    raw_edges = load_jsonl(DATA_DIR / "edges.jsonl")
    allowlist = load_id_set(ALLOWLIST_PATH)
    denylist = load_id_set(DENYLIST_PATH)
    print(
        f"  {len(people)} people, {len(repos)} repos, {len(raw_edges)} raw edges"
        + (f", {len(allowlist)} manual allowlist" if allowlist else "")
        + (f", {len(denylist)} denylist" if denylist else "")
    )

    print("Synthesising event/org bridge edges from evidence...")
    bridge_edges = synthesise_bridge_edges(people)
    edges = raw_edges + bridge_edges
    print(f"  +{len(bridge_edges)} synthesised edges -> {len(edges)} total")

    print("Deriving kolkata_signal for every person...")
    edges_by_src: dict[str, list[dict]] = {}
    for e in edges:
        edges_by_src.setdefault(e["src"], []).append(e)

    kolkata_ids: set[str] = set()
    signal_counts: dict[str, int] = {}
    rejected: list[str] = []

    for p in people:
        signal, evidence_url = derive_kolkata_signal(p, edges_by_src, allowlist, denylist)
        if signal is None:
            rejected.append(p.get("id", "?"))
            continue
        p["kolkata_signal"] = signal
        if evidence_url:
            p["signal_evidence_url"] = evidence_url
        kolkata_ids.add(p["id"])
        signal_counts[signal] = signal_counts.get(signal, 0) + 1

    print(f"  Admitted: {len(kolkata_ids)} people")
    for sig, n in sorted(signal_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {n:4d}  {sig}")
    print(f"  Rejected: {len(rejected)} people (no Kolkata signal)")
    if rejected and len(rejected) <= 20:
        for r in rejected:
            print(f"    - {r}")
    elif rejected:
        for r in rejected[:10]:
            print(f"    - {r}")
        print(f"    ... and {len(rejected) - 10} more")

    print("Building NetworkX graph (Kolkata-only)...")
    G = build_graph(people, repos, edges, kolkata_ids)
    print(f"  {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    n_persons = sum(1 for _, d in G.nodes(data=True) if d.get("type") == TYPE_PERSON)
    n_repos = sum(1 for _, d in G.nodes(data=True) if d.get("type") == TYPE_REPO)
    n_events = sum(1 for _, d in G.nodes(data=True) if d.get("type") == TYPE_EVENT)
    n_orgs = sum(1 for _, d in G.nodes(data=True) if d.get("type") == TYPE_ORG)
    print(f"  Persons: {n_persons}  Repos: {n_repos}  Events: {n_events}  Orgs: {n_orgs}")

    print("Computing PageRank centrality...")
    compute_centrality(G)

    print(f"Saving graph to {GRAPH_PATH}...")
    with open(GRAPH_PATH, "wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)

    print("Loading sentence-transformer model (may download on first run)...")
    model = SentenceTransformer(MODEL_NAME)

    print("Building ChromaDB index...")
    build_chroma(people, repos, CHROMA_PATH, model, kolkata_ids)

    print("\nDone. Smoke-test the retriever:")
    print("  python -c \"from atlas.retrieval import Retriever; r = Retriever(); print(r.query('langgraph kolkata'))\"")


if __name__ == "__main__":
    main()

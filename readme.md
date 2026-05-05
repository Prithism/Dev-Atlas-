# Kolkata Dev Atlas

> A queryable, graph-backed map of the Kolkata developer community.  
> Ask natural-language questions and get ranked people, evidence, and an interactive 3-D network — all in one shot.

---

## Table of Contents

- [What it does](#what-it-does)
- [Live demo queries](#live-demo-queries)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [Building the data artifacts](#building-the-data-artifacts)
  - [Step 1 — Ingest (optional, run to refresh data)](#step-1--ingest-optional-run-to-refresh-data)
  - [Step 2 — Build index (required)](#step-2--build-index-required)
- [Running the server](#running-the-server)
- [API reference](#api-reference)
  - [GET /health](#get-health)
  - [POST /query](#post-query)
- [Frontend](#frontend)
- [Testing](#testing)
  - [Test structure](#test-structure)
  - [Running tests](#running-tests)
  - [Smoke tests](#smoke-tests)
- [Configuration](#configuration)
  - [Kill switch](#kill-switch)
  - [Pipeline timeout](#pipeline-timeout)
- [Data model](#data-model)
  - [people.jsonl](#peoplejsonl)
  - [repos.jsonl](#reposjsonl)
  - [edges.jsonl](#edgesjsonl)
- [Contributing](#contributing)
- [License](#license)

---

## What it does

Kolkata Dev Atlas answers questions like:

- *"Who works on LangGraph in Kolkata?"*
- *"Who mentors junior ML engineers?"*
- *"Show me the Jadavpur developer cluster"*

For each query it returns:

1. **Ranked people results** — name, GitHub URL, relevance score, and 2-3 evidence bullets
2. **1-hop subgraph** — the matched people plus their repos, events, and org memberships, ready for D3 rendering
3. **Interactive 3-D force graph** in the browser that re-clusters around the query's strongest signal

The pipeline is fully resilient: if the LLM-powered Parser or Ranker agents fail, the service automatically falls back to a 2-agent baseline (Retriever + Composer) so the demo never breaks.

---

## Live demo queries

| Query | What it highlights |
|---|---|
| Who works on LangGraph in Kolkata? | LLM/agent tooling cluster |
| Who mentors ML juniors in Kolkata? | Mentor/educator network |
| Show Jadavpur developer network | University alumni cluster |

These three queries are also available as quick-launch buttons in the UI and execute against the live backend. If the backend is unavailable, the UI now shows the failure instead of masking it with canned demo results.

---

## Architecture

```
Browser (3D force-graph)
        │
        │ POST /query   GET /  GET /*.js  GET /*.css
        ▼
┌──────────────────────────────────────────────┐
│              FastAPI  (atlas/main.py)         │
│  CORS · static file routes · lifespan init   │
└───────────────────┬──────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────┐
│           Query Pipeline  (atlas/agents.py)   │
│                                               │
│  [1] Parser Agent  ──► extract skills / role  │
│         │                  (claude-haiku)      │
│         ▼                                     │
│  [2] Retriever Step ──► vector + graph search │
│         │                  (no LLM)           │
│         ▼                                     │
│  [3] Ranker Agent  ──► re-rank top-k          │
│         │                  (claude-haiku)      │
│         ▼                                     │
│  [4] Composer Agent ──► write evidence        │
│                            (claude-haiku)      │
│                                               │
│   ⚡ Fallback: Retriever + Composer only      │
└───────────────┬──────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────┐
│           Retriever  (atlas/retrieval.py)     │
│                                               │
│  ChromaDB (cosine)  ──► semantic similarity   │
│  NetworkX DiGraph   ──► PageRank centrality   │
│  Scoring: 0.7 × vec_score + 0.3 × centrality │
│  Subgraph: 1-hop ego, capped at 50 nodes      │
└───────────────┬──────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────┐
│         Data Layer  (data/)                   │
│  people.jsonl · repos.jsonl · edges.jsonl     │
│  graph.pkl (NetworkX)  ·  chroma/ (ChromaDB)  │
└──────────────────────────────────────────────┘
```

**Offline data pipeline (run once before serving):**

```
scripts/ingest.py       ──► data/raw/  +  data/*.jsonl
       │
       ▼  (harvester + GitHub search + network expansion + event cross-ref)
scripts/build_index.py  ──► data/graph.pkl  +  data/chroma/
```

---

## Tech stack

| Layer | Technology |
|---|---|
| LLM agents | Anthropic claude-haiku-4-5 via `anthropic` Python SDK |
| Backend framework | FastAPI + Uvicorn |
| Graph store | NetworkX DiGraph + PageRank (`networkx`) |
| Vector index | ChromaDB persistent (cosine space) |
| Embeddings | `all-MiniLM-L6-v2` via `sentence-transformers` |
| Data ingest | GitHub REST API via `requests`, `beautifulsoup4`, `rapidfuzz` |
| Harvester LLM | Google Gemini via `GEMINI_API_KEY` |
| Frontend | Vanilla JS, 3d-force-graph, Three.js |
| Testing | pytest, pytest-asyncio, httpx |
| Config | `python-dotenv` |

---

## Repository layout

```
Dev-Atlas/
├── atlas/                   # Backend Python package
│   ├── __init__.py
│   ├── main.py              # FastAPI app, routes, lifespan
│   ├── agents.py            # 4-agent query pipeline + fallback
│   └── retrieval.py         # Retriever class (graph + vector)
│
├── scripts/                 # Offline data pipeline (run before serving)
│   ├── ingest.py            # 5-pass data collector
│   └── harvester_agent.py   # Gemini-powered seed harvester
│
├── frontend/                # Static single-page UI (no build step)
│   ├── index.html
│   ├── app.js               # ForceGraph3D + search logic
│   └── style.css            # Neo-brutalist design system
│
├── data/                    # Source JSONL + generated artifacts
│   ├── people.jsonl         # Person records (source of truth)
│   ├── repos.jsonl          # Repository records
│   ├── edges.jsonl          # Graph edges (maintains/follows/attended/…)
│   ├── graph.pkl            # Generated — NetworkX DiGraph (gitignored)
│   └── chroma/              # Generated — ChromaDB store (gitignored)
│
├── tests/
│   ├── conftest.py          # Shared fixtures (mock retriever, LLM client)
│   ├── test_retrieval.py    # Retriever unit tests
│   ├── test_agents.py       # Agent pipeline unit + timeout tests
│   ├── test_api.py          # FastAPI integration tests
│   ├── test_build_index.py  # Index builder tests
│   ├── test_data.py         # JSONL schema + integrity tests
│   └── test_harvester_agent.py
│
├── .env                     # Secrets — never committed (see .gitignore)
├── pytest.ini
├── requirements.txt
└── README.md
```

---

## Prerequisites

- **Python 3.12+**
- A `.env` file in the project root (see [Environment variables](#environment-variables))
- Internet access for the CDN scripts in the frontend (Three.js, 3d-force-graph)

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-org/kolkata-dev-atlas.git
cd kolkata-dev-atlas

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

> The first run of `build_index.py` will also download the `all-MiniLM-L6-v2`
> sentence-transformer model (~90 MB). This happens automatically.

---

## Environment variables

Create a `.env` file in the project root. The file is gitignored.

```dotenv
# Required for the query pipeline (live LLM calls)
ANTHROPIC_API_KEY=sk-ant-...

# Required only if you run scripts/ingest.py to refresh community data
GH_TOKEN=ghp_...
GEMINI_API_KEY=...

# Optional overrides (defaults shown)
GRAPH_PATH=data/graph.pkl
CHROMA_PATH=data/chroma
```

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (server) | Powers Parser, Ranker, Composer agents |
| `GH_TOKEN` | Only for ingest | GitHub personal access token for API search |
| `GEMINI_API_KEY` | Only for ingest | Google AI Studio key for the harvester agent |
| `GRAPH_PATH` | No | Override path to `graph.pkl` |
| `CHROMA_PATH` | No | Override path to ChromaDB directory |

---

## Building the data artifacts

The backend needs `data/graph.pkl` and `data/chroma/` to start. These are generated from the JSONL source files and are **not** committed to the repository.

### Step 1 — Ingest (optional, run to refresh data)

The ingest script runs a 5-pass data collection pipeline:

| Pass | What it does |
|---|---|
| 1 | Harvester agent: Gemini-powered free-web crawl for seed candidates |
| 2 | Seed hydration + paginated GitHub search expansion (`location:Kolkata`, `location:"West Bengal"`, etc.) |
| 3 | Network expansion via seed followers/following with retained `follows` edges |
| 4 | Repo contributor graph + optional event cross-reference |
| 5 | Schema normalisation → `people.jsonl`, `repos.jsonl`, `edges.jsonl` |

```bash
python scripts/ingest.py
```

> Skip this step if the JSONL files under `data/` are already present.

### Step 2 — Build index (required)

```bash
python scripts/build_index.py
```

This script:

1. Reads `data/people.jsonl`, `data/repos.jsonl`, `data/edges.jsonl`
2. Builds a **NetworkX DiGraph** and computes **PageRank** centrality
3. Saves `data/graph.pkl`
4. Encodes all bios + repo descriptions with `all-MiniLM-L6-v2`
5. Writes a **ChromaDB** cosine collection to `data/chroma/`

Re-run whenever you modify any JSONL source file. The script is idempotent — it recreates the Chroma collection from scratch each time.

**Quick smoke-test after building:**

```bash
python -c "
from atlas.retrieval import Retriever
r = Retriever()
for result in r.query('langgraph kolkata'):
    print(result.person_id, round(result.score, 3))
"
```

---

## Running the server

```bash
uvicorn atlas.main:app --reload --port 8000
```

Open **http://localhost:8000/** in a browser. The FastAPI app serves the frontend static files from the same process, so no separate web server is needed.

The server log confirms successful startup:

```
INFO:     Started server process [...]
INFO:     Uvicorn running on http://0.0.0.0:8000
```

---

## API reference

Interactive docs are available at **http://localhost:8000/docs** (Swagger UI) and **http://localhost:8000/redoc**.

### GET /health

Returns a liveness check.

**Response `200 OK`:**

```json
{"status": "ok"}
```

---

### POST /query

Run the full 4-agent pipeline against the graph and return ranked results plus a subgraph.

**Request body:**

```json
{"q": "Who works on LangGraph in Kolkata?"}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `q` | string | Yes | Natural-language search query |

**Response `200 OK`:**

```json
{
  "results": [
    {
      "id": "rishiraj",
      "name": "Rishiraj Acharya",
      "score": 0.912,
      "evidence": [
        "Maintains rishiraj/langgraph-experiments — 142 stars, covers multi-agent LangGraph workflows",
        "Speaker at GDG Cloud Kolkata DevFest 2024 on agentic RAG pipelines",
        "Active contributor to the LangChain ecosystem"
      ],
      "url": "https://github.com/rishiraj"
    }
  ],
  "subgraph": {
    "nodes": [
      {"id": "rishiraj", "label": "Rishiraj Acharya", "type": "person", "centrality": 0.031},
      {"id": "rishiraj/langgraph-experiments", "label": "rishiraj/langgraph-experiments", "type": "repo", "centrality": 0.009}
    ],
    "edges": [
      {"src": "rishiraj", "dst": "rishiraj/langgraph-experiments", "type": "maintains"}
    ]
  }
}
```

**Response fields:**

| Field | Type | Description |
|---|---|---|
| `results` | array | Ranked people, up to 5 |
| `results[].id` | string | Stable person identifier (GitHub login) |
| `results[].name` | string | Display name |
| `results[].score` | float | Blended relevance score `[0, 1]` |
| `results[].evidence` | string[] | 2–3 human-readable evidence bullets |
| `results[].url` | string | GitHub profile URL |
| `subgraph.nodes` | array | All nodes in the 1-hop ego subgraph (max 50) |
| `subgraph.edges` | array | Edges between those nodes |

**No-results response** also includes a `message` field:

```json
{
  "results": [],
  "subgraph": {"nodes": [], "edges": []},
  "message": "No results found for 'xyz'. Try a broader query."
}
```

**Error responses:**

| Code | Condition |
|---|---|
| `400 Bad Request` | Empty query string |
| `422 Unprocessable Entity` | Missing or malformed request body |
| `500 Internal Server Error` | Unexpected server-side failure |

**cURL example:**

```bash
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"q": "Who mentors junior ML engineers in Kolkata?"}' | python -m json.tool
```

---

## Frontend

The frontend is a **single-page app** — plain JavaScript, no build step, no Node.js required.

- `frontend/index.html` — app shell with topbar search, results panel, and 3-D graph canvas
- `frontend/app.js` — search handler, force-graph rendering, and live backend error states
- `frontend/style.css` — neo-brutalist design system (CSS variables, high-contrast palette)

**How it works:**

1. On page load, the app fires an automatic search for *"Who works on LangGraph in Kolkata"*
2. Posts to `http://localhost:8000/query` and waits up to **45 seconds** for the LLM pipeline to respond
3. Renders ranked cards in the left panel and an interactive force graph on the right
4. A status badge in the top-right corner shows whether live data is loading, active, or failed
5. If the backend is unreachable or returns an error, the UI surfaces the failure so you know the atlas is not using live GitHub-backed results

**Quick-launch buttons** at the top pre-fill and run the three canonical demo queries instantly.

> **Note:** Always open the frontend via `http://localhost:8000/` rather than directly from
> disk (`file:///…`). Opening as a file causes CDN scripts to resolve against the `file:`
> protocol, which breaks the graph library.

---

## Testing

### Test structure

| File | Coverage area |
|---|---|
| `tests/test_retrieval.py` | `Retriever.query()`, `subgraph()`, `get_person()`, keyword fallback, centrality scoring |
| `tests/test_agents.py` | Each agent in isolation, `run_pipeline()`, kill-switch fallback, `run_fallback()` |
| `tests/test_api.py` | FastAPI `/query` and `/health` contracts, response shape, error handling |
| `tests/test_build_index.py` | `build_graph()`, `compute_centrality()`, `build_chroma()` |
| `tests/test_data.py` | JSONL schema integrity, required fields, isolated nodes, duplicate IDs |
| `tests/test_harvester_agent.py` | HarvesterAgent seed shape and deduplication |

### Running tests

```bash
# Run the full suite
pytest

# Verbose output
pytest -v

# A single file
pytest tests/test_retrieval.py -v

# Stop on first failure
pytest -x
```

Expected output (after `build_index.py` has been run):

```
90 passed, 7 skipped in ~6s
```

The 7 skipped tests are **smoke tests** that require `data/graph.pkl` to exist. They run automatically once the index is built.

### Smoke tests

After running `build_index.py`, re-run the suite to exercise the full stack:

```bash
python scripts/build_index.py
pytest -v -k "smoke"
```

---

## Configuration

### Kill switch

`atlas/agents.py` contains a sprint-day kill switch at the top of the file:

```python
# atlas/agents.py
USE_FALLBACK = False   # flip to True to drop to 2-agent baseline immediately
```

Setting `USE_FALLBACK = True` skips the Parser and Ranker agents entirely and routes every query through the 2-agent baseline (Retriever + Composer). Use this if Parser or Ranker output is unstable.

### Frontend request timeout

The browser waits up to **45 seconds** for the backend before falling back to demo data:

```javascript
// frontend/app.js
const QUERY_TIMEOUT_MS = 45000;
```

This accommodates the full 4-agent pipeline (3 sequential LLM calls). If you're using a faster model or the 2-agent fallback, you can lower this value. The **● Live data** badge confirms the backend responded within the window.

---

## Data model

All source data lives in `data/*.jsonl` (one JSON object per line).

### people.jsonl

```jsonc
{
  "id": "rishiraj",                          // stable identifier, matches GitHub login
  "name": "Rishiraj Acharya",
  "bio": "Building multi-agent systems with LangGraph ...",
  "location": "Kolkata, West Bengal",
  "languages": ["Python", "TypeScript"],
  "followers": 312,
  "url": "https://github.com/rishiraj",
  "evidence": ["Speaker at GDG DevFest 2024", "LangChain contributor"]
}
```

### repos.jsonl

```jsonc
{
  "id": "rishiraj/langgraph-experiments",    // full_name = owner/repo
  "owner": "rishiraj",
  "description": "Multi-agent workflow experiments using LangGraph",
  "stars": 142,
  "language": "Python",
  "topics": ["langgraph", "agents", "rag"]
}
```

### edges.jsonl

```jsonc
{"src": "rishiraj",  "dst": "rishiraj/langgraph-experiments", "type": "maintains"}
{"src": "rishiraj",  "dst": "debjit-nag",                    "type": "follows"}
{"src": "rishiraj",  "dst": "evt_gdg_cloud_2024",            "type": "attended"}
{"src": "rishiraj",  "dst": "org_gdg_cloud_kolkata",         "type": "member_of"}
```

**Supported edge types:**

| Type | Meaning |
|---|---|
| `maintains` | Person owns / maintains a repo |
| `follows` | Person follows another person on GitHub |
| `attended` | Person attended an event |
| `member_of` | Person is a member of an organisation |
| `contributed_to` | Person contributed to a repo they don't own |

**Known event node IDs:**

| ID | Name |
|---|---|
| `evt_gdg_cloud_2024` | GDG Cloud Kolkata DevFest 2024 |
| `evt_gdg_cloud_2023` | GDG Cloud Kolkata DevFest 2023 |
| `evt_pycon_india_2023` | PyCon India 2023 |
| `evt_devfest_kolkata_2024` | Devfest Kolkata 2024 |
| `evt_bangla_python_2024` | Bangla Python Meetup 2024 |
| `evt_fossasia_2024` | FOSSASIA 2024 |
| `evt_fossasia_2023` | FOSSASIA 2023 |

**Known org node IDs:**

| ID | Name |
|---|---|
| `org_gdg_cloud_kolkata` | GDG Cloud Kolkata |
| `org_jadavpur_cs` | Jadavpur University CS Department |
| `org_iit_kgp` | IIT Kharagpur |
| `org_iiit_kalyani` | IIIT Kalyani |
| `org_iiest` | IIEST Shibpur |
| `org_women_techmakers` | Google Women Techmakers Kolkata |

---

## Contributing

1. **Keep the public contracts stable.** The three interfaces that cross team boundaries are:
   - `Retriever.query(text, k)` → `list[Result]`
   - `Retriever.subgraph(person_ids, hops)` → `dict`
   - `POST /query` request/response shape

   If you need to change any of these, update the frontend and all test assertions together.

2. **Run the full test suite before opening a PR.**

   ```bash
   pytest -q
   ```

3. **The pipeline has a fallback — fix the root cause.** If Parser or Ranker is producing bad output, diagnose and fix rather than widening the kill switch permanently.

4. **Adding new people / events / edges?**
   - Edit `data/people.jsonl`, `data/repos.jsonl`, and/or `data/edges.jsonl`
   - Re-run `python scripts/build_index.py`
   - Every person must have at least one edge (`test_data.py` will catch isolated nodes)

5. **Do not commit secrets.** `.env`, `data/graph.pkl`, `data/chroma/`, and `data/raw/` are gitignored and must stay that way.

---

## License

MIT — see `LICENSE` for details.

---

*Built at GDG Cloud Kolkata Hackathon, May 2026.*

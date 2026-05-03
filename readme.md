# Dev Atlas

Kolkata Dev Atlas is a local search-and-visualization app for the Kolkata developer community. It combines a prebuilt people/repo/event graph, a ChromaDB index, an LLM-assisted query pipeline, and a static frontend that renders ranked results plus a subgraph for the matched people.

## What It Does

- Accepts natural-language questions such as "Who works on LangGraph in Kolkata?"
- Returns ranked people results with evidence snippets and profile links
- Produces a D3-friendly subgraph payload for the frontend visualization
- Falls back to built-in mock data in the frontend if the backend is unavailable

## Current Status

- The FastAPI backend is implemented with `POST /query` and `GET /health`.
- The query pipeline is implemented as Parser -> Retriever -> Ranker -> Composer, with a timeout guard and a fallback two-agent path.
- The retrieval layer loads a persisted NetworkX graph plus a ChromaDB people index, and can fall back to keyword search if embeddings are unavailable.
- The data build step is implemented in `scripts/build_index.py` and writes `data/graph.pkl` plus the persistent `data/chroma/` store.
- The frontend is a single-page static app that queries the backend when it is running and falls back to mock demo data when it is not.
- Unit and integration tests exist for the graph builder, retriever, agent pipeline, and API contract.

## Repository Layout

- `atlas/` backend, retrieval, and query orchestration
- `scripts/` data ingestion, harvester, and index building scripts
- `frontend/` static demo UI
- `data/` JSONL inputs plus generated graph/index artifacts
- `tests/` unit, integration, and smoke tests

## Requirements

- Python 3.12 or newer
- `ANTHROPIC_API_KEY` for live query runs
- `GRAPH_PATH` and `CHROMA_PATH` only if you want to point the backend at non-default artifact locations
- `GH_TOKEN` and `GEMINI_API_KEY` only if you want to run the offline ingest and harvester scripts

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you use a `.env` file, set `ANTHROPIC_API_KEY` there before starting the backend.

## Build The Index

The backend expects `data/graph.pkl` and `data/chroma/` to exist. Rebuild them if they are missing or if you change the JSONL data.

```bash
python scripts/build_index.py
```

## Run The Backend

```bash
uvicorn atlas.main:app --reload --port 8000
```

The API listens on `http://localhost:8000`.

### Example Request

```bash
curl -X POST http://localhost:8000/query \
	-H "Content-Type: application/json" \
	-d '{"q":"Who works on LangGraph in Kolkata"}'
```

The response includes a `results` array and a `subgraph` object.

## Run The App

The FastAPI app serves the frontend assets at `/`, `/app.js`, and `/style.css`, so the shortest path is:

```bash
uvicorn atlas.main:app --reload --port 8000
```

Then open `http://localhost:8000/` in your browser.

If you prefer to serve `frontend/` separately, that still works too. The browser code automatically points to `http://localhost:8000` when it is opened from `file://`, and otherwise uses the current origin.

The frontend first tries the local backend at `http://localhost:8000/query`. If the backend is not available, it falls back to mock data so the interface still renders during demos.

## Usage Guide

1. Start the backend.
2. Open `http://localhost:8000/`.
3. Try one of the demo queries:
	 - Who works on LangGraph in Kolkata
	 - Who mentors junior ML engineers
	 - Show me the Jadavpur cluster
4. If you update the dataset, rerun `python scripts/build_index.py` before querying again.

## Refreshing Data

The longer-term ingest path lives in `scripts/ingest.py` and `scripts/harvester_agent.py`.

```bash
GH_TOKEN=... GEMINI_API_KEY=... python scripts/ingest.py
```

Those scripts are present for future refreshes, but they still depend on curated source URLs and credentials before they can be used as a fully automated pipeline.

## Tests

```bash
pytest
```

The most relevant slices are `tests/test_api.py`, `tests/test_retrieval.py`, `tests/test_agents.py`, and `tests/test_build_index.py`.
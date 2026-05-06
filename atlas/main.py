"""
FastAPI entry point. Exposes POST /query per the Member A contract.

Run:
    uvicorn atlas.main:app --reload --port 8000

Environment:
    ANTHROPIC_API_KEY=sk-ant-...
    GRAPH_PATH=data/graph.pkl       (optional, defaults shown)
    CHROMA_PATH=data/chroma
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from atlas.agents import run_pipeline
from atlas.retrieval import Retriever

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.retriever = Retriever(
        graph_path=os.getenv("GRAPH_PATH", "data/graph.pkl"),
        chroma_path=os.getenv("CHROMA_PATH", "data/chroma"),
    )
    app.state.client = anthropic.AsyncAnthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )
    yield
    # nothing to clean up


app = FastAPI(title="Kolkata Dev Atlas", version="1.0.0", lifespan=lifespan)

# allow Member B's frontend (served from any origin during local demo)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="frontend-static")


class QueryRequest(BaseModel):
    q: str


@app.post("/query")
async def query(req: QueryRequest):
    if not req.q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    return await run_pipeline(req.q.strip(), app.state.retriever, app.state.client)


@app.get("/graph")
async def graph(
    max_nodes: int = Query(default=350, ge=10, le=1500),
):
    """
    Full Kolkata atlas graph for the no-query landing view.

    No LLM calls. Returns nodes + edges in the same shape as POST /query's
    `subgraph`, so the frontend renderer can consume it without changes.
    """
    retriever = app.state.retriever
    return {
        "subgraph": retriever.full_graph(max_nodes=max_nodes),
        "node_total": retriever.G.number_of_nodes(),
        "edge_total": retriever.G.number_of_edges(),
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def frontend_index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/app.js", include_in_schema=False)
async def frontend_app_js():
    return FileResponse(FRONTEND_DIR / "app.js", media_type="application/javascript")


@app.get("/style.css", include_in_schema=False)
async def frontend_style_css():
    return FileResponse(FRONTEND_DIR / "style.css", media_type="text/css")

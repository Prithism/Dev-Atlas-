"""
Member A: four-agent query pipeline.
Flow: Parser -> Retriever (Member C) -> Ranker -> Composer

Kill switch: set USE_FALLBACK = True during the sprint to drop to the
2-agent baseline (Retriever + Composer) if Parser or Ranker misbehaves.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

from atlas.retrieval import Result, Retriever

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
PIPELINE_TIMEOUT = 8.0  # seconds — flip to baseline beyond this

# Sprint-day kill switch. Flip True if Parser or Ranker breaks on the day.
USE_FALLBACK = False


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


@dataclass
class ParsedQuery:
    skills: list[str]
    role: Optional[str]
    constraints: dict
    raw: str


@dataclass
class ComposedResult:
    id: str
    name: str
    score: float
    evidence: list[str]
    url: str


# ---------------------------------------------------------------------------
# Internal LLM call — single retry on rate limit
# ---------------------------------------------------------------------------


async def _call(client: anthropic.AsyncAnthropic, prompt: str, max_tokens: int = 512) -> str:
    for attempt in range(2):
        try:
            msg = await client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except anthropic.RateLimitError:
            if attempt == 0:
                log.warning("Rate limit hit, retrying in 2s...")
                await asyncio.sleep(2)
                continue
            raise


# ---------------------------------------------------------------------------
# Agent 1: Parser
# ---------------------------------------------------------------------------


async def parser_agent(client: anthropic.AsyncAnthropic, q: str) -> ParsedQuery:
    """Extract structured intent from a natural-language query."""
    prompt = f"""Extract search intent from this query about Kolkata developers.

Query: {q}

Return JSON only, no prose:
{{
  "skills": ["list of technologies or topics mentioned or implied"],
  "role": "engineer" | "mentor" | "organizer" | "researcher" | null,
  "constraints": {{}}
}}"""
    try:
        raw = await _call(client, prompt)
        data = json.loads(raw)
        return ParsedQuery(
            skills=data.get("skills", []),
            role=data.get("role"),
            constraints=data.get("constraints", {}),
            raw=q,
        )
    except Exception as exc:
        log.warning("Parser failed (%s), using raw query as-is", exc)
        return ParsedQuery(skills=[], role=None, constraints={}, raw=q)


# ---------------------------------------------------------------------------
# Agent 2: Retriever (wraps Member C's class — no LLM)
# ---------------------------------------------------------------------------


def retriever_step(parsed: ParsedQuery, retriever: Retriever, k: int = 15) -> list[Result]:
    """Build an enriched search string from parsed intent and call Retriever.query."""
    parts = [parsed.raw] + parsed.skills
    if parsed.role:
        parts.append(parsed.role)
    search_text = " ".join(dict.fromkeys(parts))  # deduplicate, preserve order
    return retriever.query(search_text, k=k)


# ---------------------------------------------------------------------------
# Agent 3: Ranker
# ---------------------------------------------------------------------------


async def ranker_agent(
    client: anthropic.AsyncAnthropic,
    q: str,
    results: list[Result],
    retriever: Retriever,
) -> list[Result]:
    """Re-rank top-k results using LLM judgment. Falls back to original order on error."""
    if not results:
        return results

    profiles = []
    for r in results:
        person = retriever.get_person(r.person_id)
        profiles.append({
            "id": r.person_id,
            "score": round(r.score, 3),
            "bio": person.get("bio", ""),
            "languages": person.get("languages", []),
            "followers": person.get("followers", 0),
            "evidence": r.evidence[:3],
        })

    prompt = f"""Re-rank these Kolkata developer profiles for the query: "{q}"
Pick the 5 most relevant and return them most-relevant first.

Profiles:
{json.dumps(profiles, indent=2)}

Return a JSON array of IDs only: ["id1", "id2", "id3", "id4", "id5"]"""

    try:
        raw = await _call(client, prompt)
        ordered_ids: list[str] = json.loads(raw)
        id_to_result = {r.person_id: r for r in results}
        reranked = [id_to_result[pid] for pid in ordered_ids if pid in id_to_result]
        # keep any results the LLM didn't mention, appended at the end
        seen = set(ordered_ids)
        reranked += [r for r in results if r.person_id not in seen]
        return reranked
    except Exception as exc:
        log.warning("Ranker failed (%s), keeping retrieval order", exc)
        return results


# ---------------------------------------------------------------------------
# Agent 4: Composer
# ---------------------------------------------------------------------------


async def composer_agent(
    client: anthropic.AsyncAnthropic,
    q: str,
    results: list[Result],
    retriever: Retriever,
) -> list[ComposedResult]:
    """Generate polished evidence strings for each result. Falls back to raw evidence."""
    if not results:
        return []

    profiles = []
    for r in results:
        person = retriever.get_person(r.person_id)
        profiles.append({
            "id": r.person_id,
            "name": person.get("name", r.person_id),
            "bio": person.get("bio", ""),
            "existing_evidence": r.evidence[:4],
            "url": person.get("url", ""),
        })

    prompt = f"""Write result evidence for the query: "{q}"
For each developer, produce 2–3 concise bullet points explaining their relevance.
Improve or expand the existing evidence where appropriate. Be specific and factual.

Profiles:
{json.dumps(profiles, indent=2)}

Return JSON array only:
[{{"id": "...", "evidence": ["bullet 1", "bullet 2"]}}]"""

    composed_by_id: dict[str, list[str]] = {}
    try:
        raw = await _call(client, prompt, max_tokens=800)
        for item in json.loads(raw):
            composed_by_id[item["id"]] = item.get("evidence", [])
    except Exception as exc:
        log.warning("Composer failed (%s), using raw evidence", exc)

    output = []
    for r in results:
        person = retriever.get_person(r.person_id)
        evidence = composed_by_id.get(r.person_id) or r.evidence
        output.append(
            ComposedResult(
                id=r.person_id,
                name=person.get("name", r.person_id),
                score=round(r.score, 3),
                evidence=evidence,
                url=person.get("url", ""),
            )
        )
    return output


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


async def run_pipeline(
    q: str,
    retriever: Retriever,
    client: anthropic.AsyncAnthropic,
) -> dict:
    """
    Full 4-agent pipeline.
    Falls back to 2-agent baseline on timeout or when USE_FALLBACK is True.
    """
    if USE_FALLBACK:
        log.info("USE_FALLBACK=True, skipping Parser and Ranker")
        return await run_fallback(q, retriever, client)

    try:
        async with asyncio.timeout(PIPELINE_TIMEOUT):
            parsed = await parser_agent(client, q)
            raw_results = retriever_step(parsed, retriever, k=15)
            ranked = await ranker_agent(client, q, raw_results, retriever)
            composed = await composer_agent(client, q, ranked[:5], retriever)
    except TimeoutError:
        log.warning("Pipeline exceeded %.1fs timeout, falling back to baseline", PIPELINE_TIMEOUT)
        return await run_fallback(q, retriever, client)

    if not composed:
        return _empty_response(q)

    return _format_response(composed, retriever)


async def run_fallback(
    q: str,
    retriever: Retriever,
    client: anthropic.AsyncAnthropic,
) -> dict:
    """2-agent baseline: Retriever + Composer only. Pre-built per PRD §7."""
    raw_results = retriever.query(q, k=10)
    if not raw_results:
        return _empty_response(q)
    composed = await composer_agent(client, q, raw_results[:5], retriever)
    if not composed:
        return _empty_response(q)
    return _format_response(composed, retriever)


def _format_response(composed: list[ComposedResult], retriever: Retriever) -> dict:
    person_ids = [r.id for r in composed]
    subgraph = retriever.subgraph(person_ids, hops=1)
    return {
        "results": [
            {
                "id": r.id,
                "name": r.name,
                "score": r.score,
                "evidence": r.evidence,
                "url": r.url,
            }
            for r in composed
        ],
        "subgraph": subgraph,
    }


def _empty_response(q: str) -> dict:
    return {
        "results": [],
        "subgraph": {"nodes": [], "edges": []},
        "message": f"No results found for '{q}'. Try a broader query.",
    }

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
import os
import re
from dataclasses import dataclass
from typing import Optional

import anthropic

from atlas.retrieval import Result, Retriever

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
PIPELINE_TIMEOUT = float(os.getenv("ATLAS_PIPELINE_TIMEOUT_SECONDS", "18.0"))
JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

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


def _extract_text_content(message) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if not content:
        return ""

    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts).strip()


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = JSON_FENCE_RE.sub("", stripped).strip()
    return stripped


def _load_json_payload(text: str):
    cleaned = _strip_json_fences(text)
    if not cleaned:
        raise ValueError("LLM returned empty text payload")

    decoder = json.JSONDecoder()
    candidates = [cleaned]
    candidates.extend(
        cleaned[index:].strip()
        for index, char in enumerate(cleaned)
        if char in "[{"
    )

    last_error: Exception | None = None
    seen_candidates: set[str] = set()

    for candidate in candidates:
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        try:
            payload, _ = decoder.raw_decode(candidate)
            return payload
        except json.JSONDecodeError as exc:
            last_error = exc

    if last_error:
        raise last_error
    raise ValueError("Unable to find JSON payload in LLM response")


async def _call(client: anthropic.AsyncAnthropic, prompt: str, max_tokens: int = 512) -> str:
    for attempt in range(2):
        try:
            msg = await client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = _extract_text_content(msg)
            if not text:
                raise ValueError("LLM returned no text content")
            return text
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
        data = _load_json_payload(raw)
        if not isinstance(data, dict):
            raise ValueError("Parser expected a JSON object")

        skills = data.get("skills", [])
        if isinstance(skills, str):
            skills = [skills]
        skills = [skill.strip() for skill in skills if isinstance(skill, str) and skill.strip()]

        role = data.get("role")
        if role is not None and not isinstance(role, str):
            role = str(role).strip() or None

        constraints = data.get("constraints", {})
        if not isinstance(constraints, dict):
            constraints = {}

        return ParsedQuery(
            skills=skills,
            role=role,
            constraints=constraints,
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
        payload = _load_json_payload(raw)
        if isinstance(payload, dict):
            payload = payload.get("ids") or payload.get("ordered_ids") or payload.get("ranking") or []
        if not isinstance(payload, list):
            raise ValueError("Ranker expected a JSON array of IDs")

        ordered_ids: list[str] = []
        for item in payload:
            if isinstance(item, str) and item.strip():
                ordered_ids.append(item.strip())
            elif isinstance(item, dict):
                candidate_id = item.get("id") or item.get("person_id")
                if isinstance(candidate_id, str) and candidate_id.strip():
                    ordered_ids.append(candidate_id.strip())

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
        payload = _load_json_payload(raw)
        if isinstance(payload, dict):
            payload = payload.get("results") or payload.get("items") or []
        if not isinstance(payload, list):
            raise ValueError("Composer expected a JSON array of results")

        for item in payload:
            if not isinstance(item, dict):
                continue
            result_id = item.get("id") or item.get("person_id")
            if not isinstance(result_id, str) or not result_id.strip():
                continue
            evidence = item.get("evidence", [])
            if isinstance(evidence, str):
                evidence = [evidence]
            if not isinstance(evidence, list):
                evidence = []
            composed_by_id[result_id.strip()] = [
                bullet.strip() for bullet in evidence if isinstance(bullet, str) and bullet.strip()
            ]
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

"""
PRD §11 Harvester agent.

Stateless and idempotent seed discovery for the ingest pipeline:
- scrape public web search results from a free HTML endpoint
- optionally poll configured public source URLs
- use the Gemini API to extract structured seed candidates
- write raw JSONL output for downstream normalization
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

load_dotenv(ROOT_DIR / ".env")

FREE_SEARCH_ENDPOINT = os.getenv("FREE_SEARCH_ENDPOINT", "https://html.duckduckgo.com/html/")
FREE_SEARCH_FALLBACK_ENDPOINTS = [
    "https://lite.duckduckgo.com/lite/",
]
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GH_TOKEN = os.getenv("GH_TOKEN")
HARVEST_MAX_RESULTS_PER_QUERY = int(os.getenv("HARVEST_MAX_RESULTS_PER_QUERY", "10"))
HARVEST_MIN_CONFIDENCE = float(os.getenv("HARVEST_MIN_CONFIDENCE", "0.65"))
HARVEST_SOURCE_TEXT_LIMIT = int(os.getenv("HARVEST_SOURCE_TEXT_LIMIT", "12000"))
HARVEST_TIMEOUT_SECONDS = int(os.getenv("HARVEST_TIMEOUT_SECONDS", "30"))
HARVEST_USER_AGENT = os.getenv(
    "HARVEST_USER_AGENT",
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 KolkataDevAtlasHarvester/1.0"
    ),
)

DEFAULT_SEARCH_QUERIES = [
    'site:github.com "Kolkata" "developer"',
    'site:github.com "Calcutta" "engineer"',
    'site:github.com "West Bengal" "Python"',
    'site:github.com "Jadavpur" "developer"',
    'site:github.com "GDG Cloud Kolkata" github',
    'site:github.com "Women Techmakers Kolkata" github',
    'site:github.com "Bengali NLP" github',
]

DEFAULT_GITHUB_BOOTSTRAP_QUERIES = [
    "location:Kolkata followers:>=5 repos:>=3",
    "location:Calcutta followers:>=5 repos:>=3",
    '"West Bengal" followers:>=5 repos:>=3',
    "location:Jadavpur followers:>=5 repos:>=3",
]

KOLKATA_SIGNALS = {
    "kolkata",
    "calcutta",
    "west bengal",
    "jadavpur",
    "bengal",
    "bangla",
    "bengali",
    "gdg cloud kolkata",
    "women techmakers kolkata",
}

GITHUB_RESERVED_SEGMENTS = {
    "about",
    "account",
    "apps",
    "blog",
    "business",
    "collections",
    "contact",
    "customer-stories",
    "enterprise",
    "events",
    "explore",
    "features",
    "github-copilot",
    "issues",
    "login",
    "marketplace",
    "new",
    "notifications",
    "orgs",
    "pricing",
    "pulls",
    "search",
    "security",
    "settings",
    "signup",
    "site",
    "solutions",
    "sponsors",
    "team",
    "topics",
    "trending",
}

SEED_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "github_handle": {"type": "string"},
                    "github_url": {"type": ["string", "null"]},
                    "primary_signal": {"type": "string"},
                    "source_url": {"type": "string"},
                    "search_query": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "name",
                    "github_handle",
                    "github_url",
                    "primary_signal",
                    "source_url",
                    "search_query",
                    "confidence",
                ],
            },
        }
    },
    "required": ["candidates"],
}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    query: str
    source: str = "duckduckgo_html"


@dataclass
class SeedCandidate:
    name: str
    github_handle: str
    primary_signal: str
    source_url: str
    confidence: float
    github_url: str | None = None
    search_query: str | None = None


def _parse_csv_env(name: str) -> list[str]:
    raw_value = os.getenv(name, "")
    if not raw_value.strip():
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def extract_github_handle(url: str) -> str | None:
    """Return a GitHub profile handle if the URL points to a user profile."""
    if not url:
        return None

    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        wrapped = parse_qs(parsed.query).get("uddg")
        if wrapped:
            return extract_github_handle(unquote(wrapped[0]))

    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1:
        return None

    handle = parts[0].strip()
    if not handle or handle.lower() in GITHUB_RESERVED_SEGMENTS:
        return None
    return handle


def parse_search_results(html: str, query: str, limit: int) -> list[SearchResult]:
    """Parse free HTML search results into normalized search result records."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []
    seen_urls: set[str] = set()

    anchors = soup.select("a.result__a, a.result-link, a[href]")
    for anchor in anchors:
        href = anchor.get("href", "").strip()
        title = anchor.get_text(" ", strip=True)
        if not href or not title:
            continue

        parsed_url = _unwrap_search_result_url(href)
        if not parsed_url or parsed_url in seen_urls:
            continue
        seen_urls.add(parsed_url)

        result_node = (
            anchor.find_parent(class_=re.compile(r"\bresult\b"))
            or anchor.find_parent("tr")
            or anchor.parent
        )
        snippet_tag = None
        if result_node is not None:
            snippet_tag = (
                result_node.select_one(".result__snippet")
                or result_node.select_one(".snippet")
                or result_node.select_one(".result-snippet")
            )
        snippet = snippet_tag.get_text(" ", strip=True) if snippet_tag is not None else ""

        if not _looks_like_search_result(parsed_url, title):
            continue

        results.append(SearchResult(title=title, url=parsed_url, snippet=snippet, query=query))
        if len(results) >= limit:
            break

    return results


def merge_seed_candidates(
    candidates: Iterable[SeedCandidate],
    min_confidence: float = HARVEST_MIN_CONFIDENCE,
) -> list[dict]:
    """Deduplicate seed candidates by GitHub handle and merge evidence traces."""
    merged: dict[str, dict] = {}

    for candidate in candidates:
        handle = candidate.github_handle.strip().lower()
        if not handle or candidate.confidence < min_confidence:
            continue

        existing = merged.get(handle)
        if existing is None:
            merged[handle] = {
                "name": candidate.name.strip() or handle,
                "github_handle": handle,
                "primary_signal": candidate.primary_signal.strip(),
                "github_url": candidate.github_url or f"https://github.com/{handle}",
                "source_urls": [candidate.source_url] if candidate.source_url else [],
                "search_queries": [candidate.search_query] if candidate.search_query else [],
                "confidence": candidate.confidence,
            }
            continue

        if candidate.name.strip() and existing["name"] == handle:
            existing["name"] = candidate.name.strip()
        if candidate.primary_signal.strip() and candidate.primary_signal.strip() not in existing["primary_signal"]:
            existing["primary_signal"] = " | ".join(
                sorted(
                    {
                        signal.strip()
                        for signal in [existing["primary_signal"], candidate.primary_signal]
                        if signal.strip()
                    }
                )
            )
        if candidate.source_url and candidate.source_url not in existing["source_urls"]:
            existing["source_urls"].append(candidate.source_url)
        if candidate.search_query and candidate.search_query not in existing["search_queries"]:
            existing["search_queries"].append(candidate.search_query)
        if candidate.confidence > existing["confidence"]:
            existing["confidence"] = candidate.confidence
        if not existing["github_url"] and candidate.github_url:
            existing["github_url"] = candidate.github_url

    return sorted(merged.values(), key=lambda item: (-item["confidence"], item["github_handle"]))


class HarvesterAgent:
    """
    PRD-style Harvester.

    The agent is intentionally stateless:
    every run fetches public sources, extracts candidates, and overwrites
    deterministic raw output files under data/raw/.
    """

    def __init__(
        self,
        search_queries: list[str] | None = None,
        source_urls: list[str] | None = None,
        max_results_per_query: int = HARVEST_MAX_RESULTS_PER_QUERY,
        min_confidence: float = HARVEST_MIN_CONFIDENCE,
        timeout_seconds: int = HARVEST_TIMEOUT_SECONDS,
    ) -> None:
        self.search_queries = search_queries or _parse_csv_env("HARVEST_SEARCH_QUERIES") or DEFAULT_SEARCH_QUERIES
        self.source_urls = source_urls or _parse_csv_env("HARVEST_SOURCE_URLS")
        self.max_results_per_query = max_results_per_query
        self.min_confidence = min_confidence
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": HARVEST_USER_AGENT})

    def run(self) -> list[dict]:
        RAW_DIR.mkdir(parents=True, exist_ok=True)

        raw_search_results: list[dict] = []
        candidates: list[SeedCandidate] = []

        for query in self.search_queries:
            search_results = self._search_public_web(query)
            print(f"  Search query '{query}' -> {len(search_results)} scraped results")
            raw_search_results.extend(asdict(result) for result in search_results)
            heuristic = self._extract_heuristic_candidates(query, search_results)
            if heuristic:
                print(f"    heuristic seeds: {len(heuristic)}")
                candidates.extend(heuristic)
            if GEMINI_API_KEY:
                try:
                    ai_candidates = self._extract_candidates_from_search(query, search_results)
                    if ai_candidates:
                        print(f"    gemini seeds: {len(ai_candidates)}")
                        candidates.extend(ai_candidates)
                except Exception as exc:
                    print(f"    Gemini extraction failed for query '{query}': {exc}")

        for source_url in self.source_urls:
            page_text = self._fetch_page_text(source_url)
            if not page_text:
                continue
            if GEMINI_API_KEY:
                try:
                    source_candidates = self._extract_candidates_from_source_page(source_url, page_text)
                    if source_candidates:
                        print(f"  Source page '{source_url}' -> {len(source_candidates)} seeds")
                        candidates.extend(source_candidates)
                except Exception as exc:
                    print(f"  Gemini extraction failed for source page {source_url}: {exc}")

        merged = merge_seed_candidates(candidates, min_confidence=self.min_confidence)
        if not merged:
            print("  Free-web scrape produced 0 seeds; falling back to GitHub bootstrap search")
            merged = self._bootstrap_from_github_search()

        _write_jsonl(RAW_DIR / "harvester_search_results.jsonl", raw_search_results)
        _write_jsonl(RAW_DIR / "seed_candidates.jsonl", merged)

        print(
            f"[Pass 1] Harvester extracted {len(merged)} seed candidates -> "
            f"{RAW_DIR / 'seed_candidates.jsonl'}"
        )

        return [
            {
                "name": item["name"],
                "github_handle": item["github_handle"],
                "primary_signal": item["primary_signal"],
            }
            for item in merged
        ]

    def _search_public_web(self, query: str) -> list[SearchResult]:
        endpoints = [FREE_SEARCH_ENDPOINT, *FREE_SEARCH_FALLBACK_ENDPOINTS]
        best_results: list[SearchResult] = []

        for endpoint in endpoints:
            try:
                response = self.session.get(
                    endpoint,
                    params={"q": query},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                results = parse_search_results(response.text, query, self.max_results_per_query)
                if results:
                    return results
                best_results = results
            except Exception as exc:
                print(f"  Free search request failed for {endpoint}: {exc}")

        return best_results

    def _fetch_page_text(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
        except Exception as exc:
            print(f"  Could not fetch source page {url}: {exc}")
            return ""

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        return text[:HARVEST_SOURCE_TEXT_LIMIT]

    def _extract_candidates_from_search(
        self,
        query: str,
        search_results: list[SearchResult],
    ) -> list[SeedCandidate]:
        if not search_results:
            return []

        prompt = (
            "You are the Harvester agent from the Kolkata Dev Atlas PRD. "
            "Extract seed candidates for a Kolkata-focused developer graph from these public web search results.\n\n"
            "Rules:\n"
            "- Return only people, not repos, companies, or GitHub organizations.\n"
            "- Only include a candidate if a GitHub handle or GitHub profile URL is explicit in the result URL or text.\n"
            "- Prefer Kolkata, Calcutta, West Bengal, Jadavpur, Bangla/Bengali NLP, GDG Cloud Kolkata, "
            "Women Techmakers Kolkata, PyCon India, FOSSASIA, and local civic tech signals.\n"
            "- Keep primary_signal short, factual, and grounded in the supplied result.\n"
            "- Do not guess missing handles.\n"
            "- Confidence must be between 0 and 1.\n\n"
            f"Search query: {query}\n"
            f"Results JSON:\n{json.dumps([asdict(result) for result in search_results], ensure_ascii=True)}"
        )
        payload = self._call_gemini(prompt)
        return self._payload_to_candidates(payload)

    def _extract_heuristic_candidates(
        self,
        query: str,
        search_results: list[SearchResult],
    ) -> list[SeedCandidate]:
        candidates: list[SeedCandidate] = []
        for result in search_results:
            handle = extract_github_handle(result.url)
            if not handle:
                continue

            text = " ".join([result.title, result.snippet, query]).strip()
            confidence = 0.72 if _has_kolkata_signal(text) else 0.66
            title = re.sub(r"\s*[-|]\s*GitHub.*$", "", result.title, flags=re.IGNORECASE).strip()

            candidates.append(
                SeedCandidate(
                    name=title or handle,
                    github_handle=handle,
                    github_url=result.url,
                    primary_signal=_summarize_signal(result.snippet, query),
                    source_url=result.url,
                    search_query=query,
                    confidence=confidence,
                )
            )
        return candidates

    def _extract_candidates_from_source_page(self, source_url: str, page_text: str) -> list[SeedCandidate]:
        prompt = (
            "You are the Harvester agent from the Kolkata Dev Atlas PRD. "
            "Extract candidate developers from this public source page.\n\n"
            "Rules:\n"
            "- Only include people with an explicit GitHub handle or GitHub profile URL in the text.\n"
            "- Ignore people without a GitHub handle.\n"
            "- Keep primary_signal short and factual.\n"
            "- Confidence must be between 0 and 1.\n\n"
            f"Source URL: {source_url}\n"
            f"Page text:\n{page_text}"
        )
        payload = self._call_gemini(prompt)
        return self._payload_to_candidates(payload, default_source_url=source_url)

    def _payload_to_candidates(
        self,
        payload: dict,
        default_source_url: str | None = None,
    ) -> list[SeedCandidate]:
        candidates: list[SeedCandidate] = []
        for item in payload.get("candidates", []):
            github_url = item.get("github_url") or None
            handle = (item.get("github_handle") or "").strip() or extract_github_handle(github_url or "")
            if not handle:
                continue
            candidates.append(
                SeedCandidate(
                    name=(item.get("name") or handle).strip(),
                    github_handle=handle,
                    github_url=github_url or f"https://github.com/{handle}",
                    primary_signal=(item.get("primary_signal") or "").strip(),
                    source_url=(item.get("source_url") or default_source_url or "").strip(),
                    search_query=item.get("search_query"),
                    confidence=float(item.get("confidence", 0.0)),
                )
            )
        return candidates

    def _call_gemini(self, prompt: str) -> dict:
        response = self.session.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": GEMINI_API_KEY or "",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": SEED_RESPONSE_SCHEMA,
                },
            },
            timeout=max(self.timeout_seconds, 60),
        )
        response.raise_for_status()
        body = response.json()
        text = _extract_text_response(body)
        return json.loads(_strip_json_fences(text))

    def _bootstrap_from_github_search(self) -> list[dict]:
        if not GH_TOKEN:
            return []

        profiles = self._github_bootstrap_profiles()
        if not profiles:
            return []

        candidates: list[SeedCandidate] = self._heuristic_candidates_from_github_profiles(profiles)
        if GEMINI_API_KEY:
            try:
                payload = self._call_gemini(
                    "You are the Harvester agent from the Kolkata Dev Atlas PRD. "
                    "Extract seed candidates from these GitHub user profiles.\n\n"
                    "Rules:\n"
                    "- Return only likely Kolkata-adjacent developers.\n"
                    "- Keep primary_signal short and factual.\n"
                    "- Do not invent handles.\n"
                    "- Confidence must be between 0 and 1.\n\n"
                    f"Profiles JSON:\n{json.dumps(profiles, ensure_ascii=True)}"
                )
                candidates.extend(self._payload_to_candidates(payload))
            except Exception as exc:
                print(f"  Gemini extraction failed for GitHub bootstrap profiles: {exc}")

        merged = merge_seed_candidates(candidates, min_confidence=self.min_confidence)
        _write_jsonl(RAW_DIR / "github_bootstrap_profiles.jsonl", profiles)
        return merged

    def _github_bootstrap_profiles(self) -> list[dict]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GH_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        profiles: list[dict] = []
        seen_handles: set[str] = set()

        for query in DEFAULT_GITHUB_BOOTSTRAP_QUERIES:
            try:
                response = self.session.get(
                    "https://api.github.com/search/users",
                    headers=headers,
                    params={
                        "q": query,
                        "sort": "followers",
                        "per_page": min(self.max_results_per_query, 20),
                    },
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
            except Exception as exc:
                print(f"  GitHub bootstrap search failed for '{query}': {exc}")
                continue

            items = response.json().get("items", [])
            print(f"  GitHub bootstrap query '{query}' -> {len(items)} profiles")
            for item in items:
                handle = item.get("login", "").strip()
                if not handle or handle.lower() in seen_handles:
                    continue
                seen_handles.add(handle.lower())
                profile = self._fetch_github_profile(headers, handle)
                if profile:
                    profiles.append(profile)

        return profiles

    def _fetch_github_profile(self, headers: dict[str, str], handle: str) -> dict | None:
        try:
            response = self.session.get(
                f"https://api.github.com/users/{handle}",
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except Exception as exc:
            print(f"  Could not fetch GitHub profile for {handle}: {exc}")
            return None

        profile = response.json()
        return {
            "name": profile.get("name") or handle,
            "github_handle": handle,
            "github_url": profile.get("html_url") or f"https://github.com/{handle}",
            "bio": profile.get("bio") or "",
            "location": profile.get("location") or "",
            "followers": profile.get("followers") or 0,
        }

    def _heuristic_candidates_from_github_profiles(self, profiles: list[dict]) -> list[SeedCandidate]:
        candidates: list[SeedCandidate] = []
        for profile in profiles:
            text = " ".join(
                [
                    profile.get("location", ""),
                    profile.get("bio", ""),
                ]
            ).strip()
            if not _has_kolkata_signal(text):
                continue
            primary_signal = _summarize_signal(profile.get("bio", ""), profile.get("location", "Kolkata"))
            candidates.append(
                SeedCandidate(
                    name=profile.get("name") or profile["github_handle"],
                    github_handle=profile["github_handle"],
                    github_url=profile["github_url"],
                    primary_signal=primary_signal,
                    source_url=profile["github_url"],
                    search_query="github_bootstrap",
                    confidence=0.85,
                )
            )
        return candidates


def _unwrap_search_result_url(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" not in parsed.netloc:
        return url
    wrapped = parse_qs(parsed.query).get("uddg")
    if wrapped:
        return unquote(wrapped[0])
    return url


def _extract_text_response(body: dict) -> str:
    for candidate in body.get("candidates", []):
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        texts = [part.get("text", "") for part in parts if part.get("text")]
        if texts:
            return "".join(texts)
    raise ValueError(f"Gemini returned no text payload: {body}")


def _strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _looks_like_search_result(url: str, title: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    lowered_title = title.lower()
    if any(token in lowered_title for token in {"duckduckgo", "all regions", "any time"}):
        return False
    return True


def _has_kolkata_signal(text: str) -> bool:
    lowered = text.lower()
    return any(signal in lowered for signal in KOLKATA_SIGNALS)


def _summarize_signal(snippet: str, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", (snippet or "").strip())
    if cleaned:
        return cleaned[:160]
    return fallback[:160]


def _write_jsonl(path: Path, records: Iterable[dict]) -> None:
    with open(path, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def main() -> None:
    seeds = HarvesterAgent().run()
    print(f"Harvest completed with {len(seeds)} seeds")


if __name__ == "__main__":
    main()

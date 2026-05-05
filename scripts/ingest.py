"""
GitHub-backed data collection pipeline.

Run this BEFORE starting the API if you want fresh live data:
    python scripts/ingest.py

Environment:
    GH_TOKEN=...                 required, GitHub personal access token
    GEMINI_API_KEY=...           optional, improves seed discovery

Key behavior:
    1. Harvest seed handles from public sources + optional Gemini extraction
    2. Hydrate those seeds directly from GitHub so seed users always make it in
    3. Expand with paginated GitHub user search queries
    4. Pull follower/following edges for the strongest seeds
    5. Add contributor edges from tracked repos back into the graph
    6. Normalize to people.jsonl, repos.jsonl, edges.jsonl
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz

try:
    from .harvester_agent import HarvesterAgent
except ImportError:
    from harvester_agent import HarvesterAgent

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

load_dotenv(ROOT_DIR / ".env")

GH_TOKEN = os.getenv("GH_TOKEN", "").strip()
GH_API_BASE = "https://api.github.com"

SEARCH_QUERIES = [
    "location:Kolkata followers:>=5 repos:>=3",
    "location:Calcutta followers:>=5 repos:>=3",
    'location:"West Bengal" followers:>=5 repos:>=3',
    "location:Jadavpur followers:>=5 repos:>=3",
    'location:"Salt Lake" followers:>=5 repos:>=3 language:Python',
    'location:"New Town" followers:>=5 repos:>=3',
]
SEARCH_QUERIES = [
    item.strip()
    for item in os.getenv("GITHUB_SEARCH_QUERIES", ",".join(SEARCH_QUERIES)).split(",")
    if item.strip()
]

MIN_FOLLOWERS = int(os.getenv("GITHUB_MIN_FOLLOWERS", "5"))
MIN_REPOS = int(os.getenv("GITHUB_MIN_REPOS", "3"))
SEARCH_PAGES = max(1, int(os.getenv("GITHUB_SEARCH_PAGES", "5")))
SEARCH_PER_PAGE = min(100, max(1, int(os.getenv("GITHUB_SEARCH_PER_PAGE", "50"))))
MAX_USERS = max(1, int(os.getenv("GITHUB_MAX_USERS", "400")))
MAX_REPOS_PER_USER = max(1, int(os.getenv("GITHUB_MAX_REPOS_PER_USER", "10")))
MAX_NETWORK_PER_SEED = max(1, int(os.getenv("GITHUB_MAX_NETWORK_PER_SEED", "120")))
NETWORK_SEED_LIMIT = max(1, int(os.getenv("GITHUB_NETWORK_SEED_LIMIT", "50")))
NETWORK_MIN_SHARED = max(1, int(os.getenv("GITHUB_NETWORK_MIN_SHARED", "2")))
NETWORK_MAX_USERS = max(1, int(os.getenv("GITHUB_NETWORK_MAX_USERS", "250")))
MAX_CONTRIBUTOR_REPOS_PER_USER = max(
    0, int(os.getenv("GITHUB_MAX_CONTRIBUTOR_REPOS_PER_USER", "2"))
)
MAX_CONTRIBUTORS_PER_REPO = max(0, int(os.getenv("GITHUB_MAX_CONTRIBUTORS_PER_REPO", "15")))
MAX_CONTRIBUTOR_REPOS_TOTAL = max(
    0, int(os.getenv("GITHUB_MAX_CONTRIBUTOR_REPOS_TOTAL", "120"))
)
MAX_STARS_FOR_CONTRIBUTOR_SCAN = max(
    0, int(os.getenv("GITHUB_MAX_STARS_FOR_CONTRIBUTOR_SCAN", "5000"))
)
CONTRIBUTOR_REQUEST_RETRIES = max(
    1, int(os.getenv("GITHUB_CONTRIBUTOR_REQUEST_RETRIES", "1"))
)
CONTRIBUTOR_RATE_LIMIT_MAX_SLEEP_SECONDS = max(
    1, int(os.getenv("GITHUB_CONTRIBUTOR_RATE_LIMIT_MAX_SLEEP_SECONDS", "15"))
)
REQUEST_TIMEOUT_SECONDS = max(5, int(os.getenv("GITHUB_REQUEST_TIMEOUT_SECONDS", "30")))
REQUEST_RETRIES = max(1, int(os.getenv("GITHUB_REQUEST_RETRIES", "3")))
RATE_LIMIT_MAX_SLEEP_SECONDS = max(
    5, int(os.getenv("GITHUB_RATE_LIMIT_MAX_SLEEP_SECONDS", "300"))
)

KOLKATA_SIGNALS = {
    "kolkata",
    "calcutta",
    "west bengal",
    "jadavpur",
    "salt lake",
    "new town",
    "bengal",
    "bangla",
    "bengali",
    "howrah",
    "barasat",
    "serampore",
    "hooghly",
    "durgapur",
    "siliguri",
}

EVENT_SOURCES = json.loads(os.getenv("EVENT_SOURCES_JSON", "[]") or "[]")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _write_jsonl(path: Path, records: Iterable[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _normalize_login(value: str | None) -> str:
    return (value or "").strip().lower()


def _unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return unique


def _has_kolkata_signal(text: str) -> bool:
    lowered = text.lower()
    return any(signal in lowered for signal in KOLKATA_SIGNALS)


def _profile_signal_text(profile: dict) -> str:
    return " ".join(
        filter(
            None,
            [
                profile.get("name", ""),
                profile.get("bio", ""),
                profile.get("location", ""),
                profile.get("company", ""),
                profile.get("blog", ""),
            ],
        )
    )


def _dedupe_repos(repos: Iterable[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for repo in repos:
        repo_id = (repo.get("full_name") or repo.get("id") or "").strip().lower()
        if not repo_id:
            continue
        existing = merged.get(repo_id)
        if existing is None:
            merged[repo_id] = {
                "name": repo.get("name") or repo_id.split("/", 1)[-1],
                "full_name": repo_id,
                "owner": _normalize_login(repo.get("owner") or repo_id.split("/", 1)[0]),
                "description": repo.get("description") or "",
                "language": repo.get("language") or "",
                "stars": int(repo.get("stars", 0) or 0),
                "topics": _unique_strings(repo.get("topics") or []),
            }
            continue

        if len(repo.get("description") or "") > len(existing.get("description") or ""):
            existing["description"] = repo.get("description") or ""
        if repo.get("language") and not existing.get("language"):
            existing["language"] = repo["language"]
        existing["stars"] = max(existing.get("stars", 0), int(repo.get("stars", 0) or 0))
        existing["topics"] = _unique_strings([*existing.get("topics", []), *(repo.get("topics") or [])])

    return sorted(
        merged.values(),
        key=lambda item: (-item.get("stars", 0), item["full_name"]),
    )


def _merge_user_records(existing: dict, incoming: dict) -> dict:
    for field in ("name", "location", "company", "blog", "twitter", "url"):
        if incoming.get(field) and not existing.get(field):
            existing[field] = incoming[field]

    if len(incoming.get("bio", "")) > len(existing.get("bio", "")):
        existing["bio"] = incoming["bio"]

    for field in ("followers", "following", "public_repos"):
        existing[field] = max(int(existing.get(field, 0) or 0), int(incoming.get(field, 0) or 0))

    existing["search_queries"] = _unique_strings(
        [*existing.get("search_queries", []), *incoming.get("search_queries", [])]
    )
    existing["source_urls"] = _unique_strings(
        [*existing.get("source_urls", []), *incoming.get("source_urls", [])]
    )
    existing["seed_signals"] = _unique_strings(
        [*existing.get("seed_signals", []), *incoming.get("seed_signals", [])]
    )
    existing["repos"] = _dedupe_repos([*existing.get("repos", []), *incoming.get("repos", [])])
    return existing


def _upsert_user(users_by_id: dict[str, dict], user: dict) -> None:
    login = _normalize_login(user.get("id"))
    if not login:
        return
    user["id"] = login
    current = users_by_id.get(login)
    if current is None:
        users_by_id[login] = user
        return
    users_by_id[login] = _merge_user_records(current, user)


def _build_person_evidence(user: dict) -> list[str]:
    evidence: list[str] = []

    for signal in user.get("seed_signals", []):
        evidence.append(signal)
    if user.get("location"):
        evidence.append(f"GitHub location: {user['location']}")
    if user.get("company"):
        evidence.append(f"GitHub company: {user['company']}")
    if user.get("followers"):
        evidence.append(f"GitHub followers: {user['followers']}")
    for repo in _dedupe_repos(user.get("repos", []))[:3]:
        repo_label = repo.get("full_name") or repo.get("name") or "repo"
        stars = int(repo.get("stars", 0) or 0)
        if stars > 0:
            evidence.append(f"Top repo: {repo_label} ({stars} stars)")
        elif repo.get("description"):
            evidence.append(f"Repo: {repo_label} - {repo['description'][:100]}")
        else:
            evidence.append(f"Repo: {repo_label}")
    for query in user.get("search_queries", [])[:1]:
        evidence.append(f"Matched GitHub search: {query}")

    return _unique_strings(evidence)[:6]


class GitHubClient:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Dev-Atlas-Ingest/2.0",
            }
        )

    def _request(
        self,
        path: str,
        params: dict | None = None,
        *,
        retries: int | None = None,
        rate_limit_max_sleep_seconds: int | None = None,
    ) -> requests.Response:
        url = path if path.startswith("http") else f"{GH_API_BASE}{path}"
        last_error: Exception | None = None
        retries = max(1, retries or REQUEST_RETRIES)
        rate_limit_max_sleep_seconds = max(
            1, rate_limit_max_sleep_seconds or RATE_LIMIT_MAX_SLEEP_SECONDS
        )

        for attempt in range(1, retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                last_error = exc
                sleep_seconds = min(attempt * 2, 10)
                print(f"  GitHub request failed for {url}: {exc} (retrying in {sleep_seconds}s)")
                time.sleep(sleep_seconds)
                continue

            if response.ok:
                return response

            if response.status_code in {403, 429}:
                remaining = response.headers.get("X-RateLimit-Remaining", "")
                retry_after = response.headers.get("Retry-After")
                reset_at = response.headers.get("X-RateLimit-Reset")
                if retry_after:
                    sleep_seconds = min(int(retry_after), rate_limit_max_sleep_seconds)
                elif reset_at and reset_at.isdigit():
                    sleep_seconds = min(
                        max(1, int(reset_at) - int(time.time()) + 1),
                        rate_limit_max_sleep_seconds,
                    )
                elif remaining == "0":
                    sleep_seconds = min(60, rate_limit_max_sleep_seconds)
                else:
                    sleep_seconds = min(attempt * 5, rate_limit_max_sleep_seconds)

                if attempt == retries:
                    response.raise_for_status()
                print(
                    f"  GitHub rate limit/abuse guard hit for {url}; "
                    f"sleeping {sleep_seconds}s before retry"
                )
                time.sleep(sleep_seconds)
                continue

            if response.status_code >= 500 and attempt < REQUEST_RETRIES:
                sleep_seconds = min(attempt * 2, 10)
                print(
                    f"  GitHub server error {response.status_code} for {url}; "
                    f"retrying in {sleep_seconds}s"
                )
                time.sleep(sleep_seconds)
                continue

            response.raise_for_status()

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"GitHub request failed for {url}")

    @staticmethod
    def _response_json(
        response: requests.Response,
        *,
        default,
        context: str,
    ):
        if not response.content or not response.text.strip():
            return default

        try:
            return response.json()
        except requests.exceptions.JSONDecodeError:
            preview = " ".join(response.text.split())[:180] or "<empty>"
            print(
                f"  GitHub returned non-JSON for {context} "
                f"(status {response.status_code}); skipping. Body preview: {preview}"
            )
            return default

    def get_authenticated_user(self) -> dict:
        return self._request("/user").json()

    def get_rate_limit(self) -> dict:
        return self._request("/rate_limit").json()

    def search_users(self, query: str, page: int, per_page: int) -> list[dict]:
        response = self._request(
            "/search/users",
            params={
                "q": query,
                "sort": "followers",
                "order": "desc",
                "page": page,
                "per_page": per_page,
            },
        )
        payload = self._response_json(
            response,
            default={},
            context=f"GitHub user search for '{query}'",
        )
        if not isinstance(payload, dict):
            return []
        return payload.get("items", [])

    def get_user_profile(self, login: str) -> dict | None:
        try:
            return self._request(f"/users/{login}").json()
        except requests.HTTPError as exc:
            print(f"  Could not fetch GitHub profile for {login}: {exc}")
            return None

    def get_user_repos(self, login: str, limit: int) -> list[dict]:
        repos: list[dict] = []
        page = 1
        per_page = min(100, max(1, limit))

        while len(repos) < limit:
            response = self._request(
                f"/users/{login}/repos",
                params={
                    "type": "owner",
                    "sort": "updated",
                    "page": page,
                    "per_page": per_page,
                },
            )
            items = self._response_json(
                response,
                default=[],
                context=f"repo list for {login}",
            )
            if not isinstance(items, list):
                break
            if not items:
                break

            for item in items:
                owner = _normalize_login(item.get("owner", {}).get("login"))
                name = (item.get("name") or "").strip()
                if not owner or not name:
                    continue
                repos.append(
                    {
                        "name": name,
                        "full_name": f"{owner}/{name}".lower(),
                        "owner": owner,
                        "description": item.get("description") or "",
                        "language": item.get("language") or "",
                        "stars": int(item.get("stargazers_count", 0) or 0),
                        "topics": _unique_strings(item.get("topics") or []),
                    }
                )
                if len(repos) >= limit:
                    break

            if len(items) < per_page:
                break
            page += 1

        return _dedupe_repos(repos)[:limit]

    def get_user_connections(self, login: str, relationship: str, limit: int) -> list[str]:
        logins: list[str] = []
        page = 1
        per_page = min(100, max(1, limit))

        while len(logins) < limit:
            response = self._request(
                f"/users/{login}/{relationship}",
                params={"page": page, "per_page": per_page},
            )
            items = self._response_json(
                response,
                default=[],
                context=f"{relationship} list for {login}",
            )
            if not isinstance(items, list):
                break
            if not items:
                break

            for item in items:
                other_login = _normalize_login(item.get("login"))
                if other_login:
                    logins.append(other_login)
                if len(logins) >= limit:
                    break

            if len(items) < per_page:
                break
            page += 1

        return _unique_strings(logins)[:limit]

    def get_repo_contributors(self, repo_full_name: str, limit: int) -> list[str]:
        logins: list[str] = []
        page = 1
        per_page = min(100, max(1, limit))

        while len(logins) < limit:
            response = self._request(
                f"/repos/{repo_full_name}/contributors",
                params={"page": page, "per_page": per_page},
                retries=CONTRIBUTOR_REQUEST_RETRIES,
                rate_limit_max_sleep_seconds=CONTRIBUTOR_RATE_LIMIT_MAX_SLEEP_SECONDS,
            )
            items = self._response_json(
                response,
                default=[],
                context=f"contributors for {repo_full_name}",
            )
            if not isinstance(items, list):
                break
            if not items:
                break

            for item in items:
                login = _normalize_login(item.get("login"))
                if login:
                    logins.append(login)
                if len(logins) >= limit:
                    break

            if len(items) < per_page:
                break
            page += 1

        return _unique_strings(logins)[:limit]


def _profile_to_user_record(
    profile: dict,
    repos: list[dict],
    *,
    seed_signals: list[str] | None = None,
    source_urls: list[str] | None = None,
    search_queries: list[str] | None = None,
    fallback_name: str | None = None,
) -> dict:
    login = _normalize_login(profile.get("login"))
    return {
        "id": login,
        "github_id": int(profile.get("id", 0) or 0),
        "name": profile.get("name") or fallback_name or login,
        "bio": profile.get("bio") or "",
        "location": profile.get("location") or "",
        "company": profile.get("company") or "",
        "blog": profile.get("blog") or "",
        "twitter": profile.get("twitter_username") or "",
        "followers": int(profile.get("followers", 0) or 0),
        "following": int(profile.get("following", 0) or 0),
        "public_repos": int(profile.get("public_repos", 0) or 0),
        "url": profile.get("html_url") or f"https://github.com/{login}",
        "source_urls": _unique_strings(source_urls or [profile.get("html_url") or ""]),
        "search_queries": _unique_strings(search_queries or []),
        "seed_signals": _unique_strings(seed_signals or []),
        "repos": _dedupe_repos(repos),
    }


# ---------------------------------------------------------------------------
# Pass 1: Harvester agent
# ---------------------------------------------------------------------------


def harvest_seeds() -> list[dict]:
    """
    Run the harvester and return the richer merged seed records emitted into data/raw/.
    """
    HarvesterAgent().run()
    seed_path = RAW_DIR / "seed_candidates.jsonl"
    seeds = load_jsonl(seed_path)
    print(f"[Pass 1] Prepared {len(seeds)} seed candidates for GitHub hydration")
    return seeds


# ---------------------------------------------------------------------------
# Pass 2: hydrate seeds + GitHub search expansion
# ---------------------------------------------------------------------------


def hydrate_seed_users(client: GitHubClient, seeds: list[dict]) -> dict[str, dict]:
    users_by_id: dict[str, dict] = {}
    hydrated: list[dict] = []

    for seed in seeds:
        handle = _normalize_login(seed.get("github_handle"))
        if not handle:
            continue

        profile = client.get_user_profile(handle)
        if not profile:
            continue

        repos = client.get_user_repos(handle, MAX_REPOS_PER_USER)
        user = _profile_to_user_record(
            profile,
            repos,
            seed_signals=[seed.get("primary_signal", "")],
            source_urls=seed.get("source_urls") or [seed.get("github_url", "")],
            search_queries=seed.get("search_queries") or [],
            fallback_name=seed.get("name"),
        )
        _upsert_user(users_by_id, user)
        hydrated.append(user)

    _write_jsonl(RAW_DIR / "seed_users.jsonl", hydrated)
    print(f"[Pass 2a] Hydrated {len(hydrated)} seed users from GitHub")
    return users_by_id


def fetch_github_users(client: GitHubClient, users_by_id: dict[str, dict]) -> list[dict]:
    discovered = 0

    for query in SEARCH_QUERIES:
        print(f"  Searching GitHub users: {query}")
        for page in range(1, SEARCH_PAGES + 1):
            if len(users_by_id) >= MAX_USERS:
                break

            items = client.search_users(query, page=page, per_page=SEARCH_PER_PAGE)
            if not items:
                break

            print(f"    page {page}: {len(items)} candidates")
            for item in items:
                login = _normalize_login(item.get("login"))
                if not login:
                    continue

                if login in users_by_id:
                    users_by_id[login]["search_queries"] = _unique_strings(
                        [*users_by_id[login].get("search_queries", []), query]
                    )
                    continue

                profile = client.get_user_profile(login)
                if not profile:
                    continue
                if profile.get("type") != "User":
                    continue
                if int(profile.get("followers", 0) or 0) < MIN_FOLLOWERS:
                    continue
                if int(profile.get("public_repos", 0) or 0) < MIN_REPOS:
                    continue

                repos = client.get_user_repos(login, MAX_REPOS_PER_USER)
                user = _profile_to_user_record(
                    profile,
                    repos,
                    source_urls=[profile.get("html_url") or ""],
                    search_queries=[query],
                )
                _upsert_user(users_by_id, user)
                discovered += 1

                if len(users_by_id) >= MAX_USERS:
                    break

            if len(items) < SEARCH_PER_PAGE:
                break

        if len(users_by_id) >= MAX_USERS:
            break

    users = sorted(
        users_by_id.values(),
        key=lambda item: (-item.get("followers", 0), item["id"]),
    )
    _write_jsonl(RAW_DIR / "github_users.jsonl", users)
    print(f"[Pass 2b] Collected {len(users)} unique GitHub users ({discovered} search additions)")
    return users


# ---------------------------------------------------------------------------
# Pass 3: network expansion via seed follows
# ---------------------------------------------------------------------------


def expand_via_networks(
    client: GitHubClient,
    seeds: list[dict],
    users_by_id: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    shared_networks: dict[str, set[str]] = defaultdict(set)
    follows_edges: set[tuple[str, str, str]] = set()

    ranked_seeds = sorted(
        seeds,
        key=lambda item: float(item.get("confidence", 0.0) or 0.0),
        reverse=True,
    )
    ranked_seeds = ranked_seeds[:NETWORK_SEED_LIMIT]

    for seed in ranked_seeds:
        handle = _normalize_login(seed.get("github_handle"))
        if not handle:
            continue

        followers = client.get_user_connections(handle, "followers", MAX_NETWORK_PER_SEED)
        following = client.get_user_connections(handle, "following", MAX_NETWORK_PER_SEED)
        print(
            f"  Seed network {handle}: sampled {len(followers)} followers + "
            f"{len(following)} following"
        )

        for follower in followers:
            shared_networks[follower].add(handle)
            if follower != handle:
                follows_edges.add((follower, handle, "follows"))

        for followee in following:
            shared_networks[followee].add(handle)
            if followee != handle:
                follows_edges.add((handle, followee, "follows"))

    candidate_logins = [
        login
        for login, connected_seeds in shared_networks.items()
        if len(connected_seeds) >= NETWORK_MIN_SHARED
    ]
    candidate_logins.sort(key=lambda login: (-len(shared_networks[login]), login))

    network_added: list[dict] = []

    for login in candidate_logins[:NETWORK_MAX_USERS]:
        shared_count = len(shared_networks[login])
        signal = f"Shared GitHub network with {shared_count} Kolkata seeds"

        if login in users_by_id:
            users_by_id[login]["seed_signals"] = _unique_strings(
                [*users_by_id[login].get("seed_signals", []), signal]
            )
            continue

        profile = client.get_user_profile(login)
        if not profile:
            continue
        if int(profile.get("public_repos", 0) or 0) < 1:
            continue

        profile_text = _profile_signal_text(profile)
        if shared_count <= NETWORK_MIN_SHARED and not _has_kolkata_signal(profile_text):
            continue

        repos = client.get_user_repos(login, MAX_REPOS_PER_USER)
        user = _profile_to_user_record(
            profile,
            repos,
            seed_signals=[signal],
            source_urls=[profile.get("html_url") or ""],
        )
        _upsert_user(users_by_id, user)
        network_added.append(user)

    known_people = set(users_by_id)
    filtered_follows = [
        {"src": src, "dst": dst, "type": etype}
        for src, dst, etype in sorted(follows_edges)
        if src in known_people and dst in known_people and src != dst
    ]

    _write_jsonl(RAW_DIR / "network_expanded.jsonl", network_added)
    _write_jsonl(RAW_DIR / "network_edges.jsonl", filtered_follows)
    print(
        f"[Pass 3] Added {len(network_added)} users from seed networks and "
        f"retained {len(filtered_follows)} follows edges"
    )

    users = sorted(
        users_by_id.values(),
        key=lambda item: (-item.get("followers", 0), item["id"]),
    )
    return users, filtered_follows


# ---------------------------------------------------------------------------
# Pass 4: contributor edges + optional event cross-reference
# ---------------------------------------------------------------------------


def build_contribution_edges(client: GitHubClient, all_users: list[dict]) -> list[dict]:
    if MAX_CONTRIBUTOR_REPOS_PER_USER <= 0 or MAX_CONTRIBUTORS_PER_REPO <= 0:
        return []

    known_people = {user["id"] for user in all_users}
    seen_repo_ids: set[str] = set()
    scanned_repos: set[str] = set()
    contribution_edges: set[tuple[str, str, str]] = set()
    raw_rows: list[dict] = []
    skipped_large_repos = 0
    reached_scan_cap = False

    for user in all_users:
        top_repos = _dedupe_repos(user.get("repos", []))[:MAX_CONTRIBUTOR_REPOS_PER_USER]
        for repo in top_repos:
            repo_id = repo.get("full_name", "")
            if not repo_id or repo_id in seen_repo_ids:
                continue
            seen_repo_ids.add(repo_id)

            repo_stars = int(repo.get("stars", 0) or 0)
            if MAX_STARS_FOR_CONTRIBUTOR_SCAN and repo_stars > MAX_STARS_FOR_CONTRIBUTOR_SCAN:
                skipped_large_repos += 1
                print(
                    f"  Skipping contributor scan for {repo_id}: "
                    f"{repo_stars} stars exceeds cap {MAX_STARS_FOR_CONTRIBUTOR_SCAN}"
                )
                continue

            if MAX_CONTRIBUTOR_REPOS_TOTAL and len(scanned_repos) >= MAX_CONTRIBUTOR_REPOS_TOTAL:
                reached_scan_cap = True
                break

            try:
                contributors = client.get_repo_contributors(repo_id, MAX_CONTRIBUTORS_PER_REPO)
            except requests.HTTPError as exc:
                print(f"  Could not fetch contributors for {repo_id}: {exc}")
                continue

            scanned_repos.add(repo_id)

            tracked_contributors = [
                login
                for login in contributors
                if login in known_people and login != repo.get("owner")
            ]
            if tracked_contributors:
                raw_rows.append({"repo": repo_id, "contributors": tracked_contributors})

            for contributor in tracked_contributors:
                contribution_edges.add((contributor, repo_id, "contributed_to"))

        if reached_scan_cap:
            break

    if reached_scan_cap:
        print(
            f"  Contributor scan cap reached after {len(scanned_repos)} repos; "
            "skipping the remaining candidates"
        )

    edges = [
        {"src": src, "dst": dst, "type": etype}
        for src, dst, etype in sorted(contribution_edges)
    ]
    _write_jsonl(RAW_DIR / "repo_contributors.jsonl", raw_rows)
    print(
        f"[Pass 4a] Added {len(edges)} contributor edges from {len(scanned_repos)} repos "
        f"({skipped_large_repos} oversized repos skipped)"
    )
    return edges


def crossref_events(all_users: list[dict]) -> list[dict]:
    """
    Optional public event enrichment. Configure EVENT_SOURCES_JSON in .env with:
        [{"url":"https://...", "event_id":"evt_...", "event_name":"..."}]
    """
    if not EVENT_SOURCES:
        return []

    name_to_id = {u["name"].lower(): u["id"] for u in all_users if u.get("name")}
    attended_edges: list[dict] = []

    for source in EVENT_SOURCES:
        evt_id = source["event_id"]
        url = source.get("url", "")
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            attendee_names = _parse_attendee_list(resp.text)
        except Exception as exc:
            print(f"  Could not fetch event source {url}: {exc}")
            continue

        for attendee in attendee_names:
            best_score, best_id = 0, None
            for known_name, uid in name_to_id.items():
                score = fuzz.token_sort_ratio(attendee.lower(), known_name)
                if score > best_score:
                    best_score, best_id = score, uid

            if best_score >= 85 and best_id:
                attended_edges.append({"src": best_id, "dst": evt_id, "type": "attended"})

    print(f"[Pass 4b] Cross-referenced {len(attended_edges)} event edges")
    return attended_edges


def _parse_attendee_list(html: str) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    names: list[str] = []
    selectors = [
        ".attendee-name",
        "[data-attendee-name]",
        ".speaker-name",
        ".member-name",
    ]

    for selector in selectors:
        for tag in soup.select(selector):
            text = tag.get_text(" ", strip=True)
            if text:
                names.append(text)

    return _unique_strings(names)


# ---------------------------------------------------------------------------
# Pass 5: normalize to schema
# ---------------------------------------------------------------------------


def normalize(all_users: list[dict], extra_edges: list[dict]) -> None:
    people: list[dict] = []
    repos_by_id: dict[str, dict] = {}
    edge_keys: set[tuple[str, str, str]] = set()
    edges: list[dict] = []

    for user in sorted(all_users, key=lambda item: (-item.get("followers", 0), item["id"])):
        pid = _normalize_login(user.get("id"))
        repos = _dedupe_repos(user.get("repos", []))
        languages = sorted({repo["language"] for repo in repos if repo.get("language")})

        people.append(
            {
                "id": pid,
                "name": user.get("name", pid) or pid,
                "bio": user.get("bio", ""),
                "location": user.get("location", ""),
                "languages": languages,
                "followers": int(user.get("followers", 0) or 0),
                "url": user.get("url", f"https://github.com/{pid}"),
                "evidence": _build_person_evidence(user),
                "company": user.get("company", ""),
                "blog": user.get("blog", ""),
                "twitter": user.get("twitter", ""),
                "search_queries": user.get("search_queries", []),
                "source_urls": user.get("source_urls", []),
            }
        )

        for repo in repos:
            repo_id = repo["full_name"]
            repos_by_id[repo_id] = {
                "id": repo_id,
                "owner": _normalize_login(repo.get("owner") or pid),
                "description": repo.get("description", ""),
                "stars": int(repo.get("stars", 0) or 0),
                "language": repo.get("language", ""),
                "topics": _unique_strings(repo.get("topics") or []),
            }
            edge_key = (pid, repo_id, "maintains")
            if edge_key not in edge_keys:
                edge_keys.add(edge_key)
                edges.append({"src": pid, "dst": repo_id, "type": "maintains"})

    person_ids = {person["id"] for person in people}
    repo_ids = set(repos_by_id)

    for edge in extra_edges:
        src = _normalize_login(edge.get("src"))
        dst = edge.get("dst", "")
        etype = edge.get("type", "")
        if not src or not dst or not etype:
            continue

        if etype == "follows":
            dst = _normalize_login(dst)
            if src not in person_ids or dst not in person_ids or src == dst:
                continue
        elif etype == "contributed_to":
            dst = dst.strip().lower()
            if src not in person_ids or dst not in repo_ids:
                continue
        elif etype == "attended":
            if src not in person_ids:
                continue
        elif etype == "member_of":
            if src not in person_ids:
                continue
        else:
            continue

        edge_key = (src, dst, etype)
        if edge_key in edge_keys:
            continue
        edge_keys.add(edge_key)
        edges.append({"src": src, "dst": dst, "type": etype})

    repos = sorted(repos_by_id.values(), key=lambda item: (-item["stars"], item["id"]))
    edges.sort(key=lambda item: (item["type"], item["src"], item["dst"]))

    _write_jsonl(DATA_DIR / "people.jsonl", people)
    _write_jsonl(DATA_DIR / "repos.jsonl", repos)
    _write_jsonl(DATA_DIR / "edges.jsonl", edges)

    print(
        f"[Pass 5] Wrote {len(people)} people, {len(repos)} repos, {len(edges)} edges to {DATA_DIR}/"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if not GH_TOKEN:
        print("ERROR: set GH_TOKEN in .env")
        sys.exit(1)

    DATA_DIR.mkdir(exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    client = GitHubClient(GH_TOKEN)
    auth_user = client.get_authenticated_user()
    rate_limit = client.get_rate_limit()
    core = rate_limit.get("resources", {}).get("core", {})
    print(f"Authenticated as: {auth_user.get('login', 'unknown')}")
    print(f"Rate limit remaining: {core.get('remaining', '?')}/{core.get('limit', '?')}\n")

    seeds = harvest_seeds()
    users_by_id = hydrate_seed_users(client, seeds)
    raw_users = fetch_github_users(client, users_by_id)
    all_users, follows_edges = expand_via_networks(client, seeds, users_by_id)
    contributor_edges = build_contribution_edges(client, all_users)
    event_edges = crossref_events(all_users)
    normalize(all_users, [*follows_edges, *contributor_edges, *event_edges])

    print(
        "\nNext step: python scripts/build_index.py\n"
        f"Tracked users after ingest: {len(all_users)} (search snapshot: {len(raw_users)})"
    )


if __name__ == "__main__":
    main()

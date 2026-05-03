"""
5-pass data collection pipeline. Run this BEFORE the sprint.
Requires: GH_TOKEN environment variable (GitHub personal access token).

Usage:
    export GH_TOKEN=ghp_...
    python scripts/ingest.py

Passes:
    1. Seed list from data/seeds.csv
    2. GitHub search expansion (location:Kolkata etc.)
    3. Network expansion via seed followers/following
    4. Event cross-reference via public attendee lists
    5. Schema normalization -> people.jsonl, repos.jsonl, edges.jsonl
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

import requests
from rapidfuzz import fuzz

try:
    from github import Github, RateLimitExceededException
except ImportError:
    print("ERROR: pip install PyGithub")
    sys.exit(1)

DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"

GH_TOKEN = os.getenv("GH_TOKEN")
if not GH_TOKEN:
    print("ERROR: set GH_TOKEN environment variable")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Pass 1: seed list
# ---------------------------------------------------------------------------

def load_seeds(path: Path = DATA_DIR / "seeds.csv") -> list[dict]:
    """
    Expects CSV with columns: name, github_handle, primary_signal
    Example: Rishiraj Acharya, rishiraj, GDG organizer
    """
    if not path.exists():
        print(f"No seeds file at {path}. Create it from the manual spreadsheet first.")
        return []
    seeds = []
    with open(path) as f:
        for row in csv.DictReader(f):
            seeds.append(row)
    print(f"[Pass 1] Loaded {len(seeds)} seeds from {path}")
    return seeds


# ---------------------------------------------------------------------------
# Pass 2: GitHub search expansion
# ---------------------------------------------------------------------------

SEARCH_QUERIES = [
    "location:Kolkata",
    "location:Calcutta",
    'location:"West Bengal"',
    "location:Jadavpur",
    'location:"Salt Lake" language:Python',
]

MIN_FOLLOWERS = 5
MIN_REPOS = 3


def fetch_github_users(g: Github) -> list[dict]:
    """Search GitHub for Kolkata devs; fetch profile + top repos."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    seen_ids: set[int] = set()
    users: list[dict] = []

    for query in SEARCH_QUERIES:
        print(f"  Searching: {query}")
        try:
            results = g.search_users(query, sort="followers")
            for u in results:
                if u.id in seen_ids:
                    continue
                if u.followers < MIN_FOLLOWERS or u.public_repos < MIN_REPOS:
                    continue
                seen_ids.add(u.id)
                user_data = _extract_user(u)
                users.append(user_data)
                if len(users) % 50 == 0:
                    print(f"    {len(users)} users collected so far...")
        except RateLimitExceededException:
            print("  Rate limit hit, sleeping 60s...")
            time.sleep(60)

    raw_path = RAW_DIR / "github_users.jsonl"
    with open(raw_path, "w") as f:
        for u in users:
            f.write(json.dumps(u) + "\n")
    print(f"[Pass 2] Saved {len(users)} users to {raw_path}")
    return users


def _extract_user(u) -> dict:
    repos = []
    try:
        top_repos = sorted(
            u.get_repos(),
            key=lambda r: r.stargazers_count,
            reverse=True,
        )[:5]
        for r in top_repos:
            repos.append({
                "name": r.name,
                "full_name": r.full_name,
                "description": r.description or "",
                "language": r.language or "",
                "stars": r.stargazers_count,
                "topics": r.get_topics(),
            })
    except Exception:
        pass

    return {
        "id": u.login,
        "github_id": u.id,
        "name": u.name or u.login,
        "bio": u.bio or "",
        "location": u.location or "",
        "blog": u.blog or "",
        "twitter": u.twitter_username or "",
        "followers": u.followers,
        "following": u.following,
        "public_repos": u.public_repos,
        "url": u.html_url,
        "repos": repos,
    }


# ---------------------------------------------------------------------------
# Pass 3: network expansion via seed follows
# ---------------------------------------------------------------------------

def expand_via_networks(g: Github, seeds: list[dict], raw_users: list[dict]) -> list[dict]:
    """
    For each seed, pull followers + following up to 100 each.
    Add anyone who appears in 2+ seed networks AND has a Kolkata-adjacent signal.
    """
    seed_handles = {s["github_handle"].lower() for s in seeds}
    network_counts: dict[str, int] = {}
    kolkata_signals = {"kolkata", "calcutta", "west bengal", "bengal", "bangla", "bengali"}

    def has_kolkata_signal(user) -> bool:
        loc = (user.location or "").lower()
        bio = (user.bio or "").lower()
        return any(s in loc or s in bio for s in kolkata_signals)

    for handle in seed_handles:
        try:
            u = g.get_user(handle)
            neighbors = list(u.get_followers()[:100]) + list(u.get_following()[:100])
            for nbr in neighbors:
                login = nbr.login.lower()
                network_counts[login] = network_counts.get(login, 0) + 1
        except Exception:
            continue

    existing_ids = {u["id"].lower() for u in raw_users}
    new_users: list[dict] = []

    for login, count in network_counts.items():
        if count < 2 or login in existing_ids:
            continue
        try:
            u = g.get_user(login)
            if has_kolkata_signal(u):
                user_data = _extract_user(u)
                new_users.append(user_data)
                existing_ids.add(login)
        except Exception:
            continue

    raw_path = RAW_DIR / "network_expanded.jsonl"
    with open(raw_path, "w") as f:
        for u in new_users:
            f.write(json.dumps(u) + "\n")
    print(f"[Pass 3] Found {len(new_users)} new users via network expansion -> {raw_path}")
    return raw_users + new_users


# ---------------------------------------------------------------------------
# Pass 4: event cross-reference
# ---------------------------------------------------------------------------

EVENT_SOURCES = [
    # Add public attendee/speaker list URLs here.
    # Format: {"url": "...", "event_id": "evt_gdg_cloud_2024", "event_name": "..."}
    # Example (replace with real scrape-able URLs or local CSV exports):
    # {"url": "https://gdg.community.dev/events/details/...", "event_id": "evt_gdg_cloud_2024"},
]


def crossref_events(all_users: list[dict]) -> list[dict]:
    """
    Fuzzy-match event attendee names against our user set.
    Returns a list of edge dicts {"src": person_id, "dst": event_id, "type": "attended"}.
    """
    name_to_id = {u["name"].lower(): u["id"] for u in all_users}
    attended_edges: list[dict] = []

    for source in EVENT_SOURCES:
        evt_id = source["event_id"]
        url = source.get("url", "")
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            attendee_names = _parse_attendee_list(resp.text)
        except Exception as e:
            print(f"  Could not fetch {url}: {e}")
            continue

        for attendee in attendee_names:
            best_score, best_id = 0, None
            for known_name, uid in name_to_id.items():
                score = fuzz.token_sort_ratio(attendee.lower(), known_name)
                if score > best_score:
                    best_score, best_id = score, uid
            # only accept high-confidence matches; manually verify borderline ones
            if best_score >= 85 and best_id:
                attended_edges.append({"src": best_id, "dst": evt_id, "type": "attended"})

    print(f"[Pass 4] Cross-referenced {len(attended_edges)} attended edges from events")
    return attended_edges


def _parse_attendee_list(html: str) -> list[str]:
    """Stub — customize per event source."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    names = []
    for tag in soup.find_all(class_="attendee-name"):
        text = tag.get_text(strip=True)
        if text:
            names.append(text)
    return names


# ---------------------------------------------------------------------------
# Pass 5: normalize to schema
# ---------------------------------------------------------------------------

def normalize(all_users: list[dict], extra_edges: list[dict]) -> None:
    """Write normalized people.jsonl, repos.jsonl, edges.jsonl."""
    people: list[dict] = []
    repos: list[dict] = []
    edges: list[dict] = list(extra_edges)

    for u in all_users:
        pid = u["id"]
        langs = list({r["language"] for r in u.get("repos", []) if r.get("language")})

        people.append({
            "id": pid,
            "name": u.get("name", pid),
            "bio": u.get("bio", ""),
            "location": u.get("location", ""),
            "languages": langs,
            "followers": u.get("followers", 0),
            "url": u.get("url", f"https://github.com/{pid}"),
            "evidence": [],
        })

        for r in u.get("repos", []):
            repo_id = r.get("full_name", f"{pid}/{r['name']}")
            repos.append({
                "id": repo_id,
                "owner": pid,
                "description": r.get("description", ""),
                "stars": r.get("stars", 0),
                "language": r.get("language", ""),
                "topics": r.get("topics", []),
            })
            edges.append({"src": pid, "dst": repo_id, "type": "maintains"})

    _write_jsonl(DATA_DIR / "people.jsonl", people)
    _write_jsonl(DATA_DIR / "repos.jsonl", repos)
    _write_jsonl(DATA_DIR / "edges.jsonl", edges)

    print(
        f"[Pass 5] Wrote {len(people)} people, {len(repos)} repos, {len(edges)} edges to {DATA_DIR}/"
    )


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    g = Github(GH_TOKEN)
    print(f"Authenticated as: {g.get_user().login}")
    print(f"Rate limit remaining: {g.get_rate_limit().core.remaining}/5000\n")

    seeds = load_seeds()
    raw_users = fetch_github_users(g)
    all_users = expand_via_networks(g, seeds, raw_users)
    extra_edges = crossref_events(all_users)
    normalize(all_users, extra_edges)

    print("\nNext step: python scripts/build_index.py")


if __name__ == "__main__":
    main()

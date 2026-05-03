from __future__ import annotations

from scripts.harvester_agent import (
    SeedCandidate,
    extract_github_handle,
    merge_seed_candidates,
    parse_search_results,
)


def test_extract_github_handle_from_profile_url():
    assert extract_github_handle("https://github.com/Kabyik-Kayal") == "Kabyik-Kayal"
    assert extract_github_handle("https://github.com/Kabyik-Kayal?tab=repositories") == "Kabyik-Kayal"


def test_extract_github_handle_rejects_non_profile_urls():
    assert extract_github_handle("https://github.com/Kabyik-Kayal/dev-atlas") is None
    assert extract_github_handle("https://github.com/topics/langgraph") is None
    assert extract_github_handle("https://example.com/Kabyik-Kayal") is None


def test_parse_search_results_extracts_title_url_and_snippet():
    html = """
    <html>
      <body>
        <div class="result">
          <a class="result__a" href="https://github.com/alice">Alice Roy</a>
          <div class="result__snippet">Kolkata ML engineer and GDG Cloud volunteer</div>
        </div>
        <div class="result">
          <a class="result__a" href="https://github.com/bob">Bob Das</a>
          <div class="result__snippet">Bengali NLP researcher from Jadavpur</div>
        </div>
      </body>
    </html>
    """

    results = parse_search_results(html, 'site:github.com "Kolkata"', limit=5)

    assert len(results) == 2
    assert results[0].title == "Alice Roy"
    assert results[0].url == "https://github.com/alice"
    assert "Kolkata ML engineer" in results[0].snippet
    assert results[0].query == 'site:github.com "Kolkata"'


def test_merge_seed_candidates_dedupes_and_merges_evidence():
    candidates = [
        SeedCandidate(
            name="Alice Roy",
            github_handle="Alice",
            github_url="https://github.com/Alice",
            primary_signal="GDG Cloud Kolkata volunteer",
            source_url="https://example.com/a",
            search_query='site:github.com "Kolkata"',
            confidence=0.91,
        ),
        SeedCandidate(
            name="Alice Roy",
            github_handle="alice",
            github_url="https://github.com/alice",
            primary_signal="Jadavpur ML engineer",
            source_url="https://example.com/b",
            search_query='site:github.com "Jadavpur"',
            confidence=0.88,
        ),
        SeedCandidate(
            name="Low Confidence",
            github_handle="skipme",
            github_url="https://github.com/skipme",
            primary_signal="Weak match",
            source_url="https://example.com/c",
            search_query='site:github.com "Calcutta"',
            confidence=0.20,
        ),
    ]

    merged = merge_seed_candidates(candidates, min_confidence=0.65)

    assert len(merged) == 1
    assert merged[0]["github_handle"] == "alice"
    assert "GDG Cloud Kolkata volunteer" in merged[0]["primary_signal"]
    assert "Jadavpur ML engineer" in merged[0]["primary_signal"]
    assert merged[0]["source_urls"] == ["https://example.com/a", "https://example.com/b"]
    assert merged[0]["search_queries"] == [
        'site:github.com "Kolkata"',
        'site:github.com "Jadavpur"',
    ]

"""
Unit tests for atlas/agents.py.

All LLM calls are mocked — no network, no API key needed.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import atlas.agents as agents_module
from atlas.agents import (
    ComposedResult,
    ParsedQuery,
    _call,
    composer_agent,
    parser_agent,
    ranker_agent,
    retriever_step,
    run_fallback,
    run_pipeline,
)
from atlas.retrieval import Result


# ---------------------------------------------------------------------------
# _call — shared LLM helper
# ---------------------------------------------------------------------------


class TestCall:
    async def test_returns_text_from_response(self, mock_anthropic_client, llm_response):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response("hello world")
        )
        result = await _call(mock_anthropic_client, "prompt")
        assert result == "hello world"

    async def test_joins_multiple_text_blocks(self, mock_anthropic_client):
        block_one = MagicMock(text="```json")
        block_two = MagicMock(text='{"skills": ["langgraph"]}')
        block_three = MagicMock(text="```")
        mock_message = MagicMock()
        mock_message.content = [block_one, block_two, block_three]
        mock_anthropic_client.messages.create = AsyncMock(return_value=mock_message)

        result = await _call(mock_anthropic_client, "prompt")

        assert '{"skills": ["langgraph"]}' in result

    async def test_retries_once_on_rate_limit(self, mock_anthropic_client, llm_response):
        import anthropic

        mock_anthropic_client.messages.create = AsyncMock(
            side_effect=[
                anthropic.RateLimitError("rate limit", response=MagicMock(), body={}),
                llm_response("ok after retry"),
            ]
        )
        with patch("atlas.agents.asyncio.sleep", new_callable=AsyncMock):
            result = await _call(mock_anthropic_client, "prompt")
        assert result == "ok after retry"

    async def test_raises_on_second_rate_limit(self, mock_anthropic_client):
        import anthropic

        mock_anthropic_client.messages.create = AsyncMock(
            side_effect=anthropic.RateLimitError("rate limit", response=MagicMock(), body={})
        )
        with patch("atlas.agents.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(anthropic.RateLimitError):
                await _call(mock_anthropic_client, "prompt")


# ---------------------------------------------------------------------------
# parser_agent
# ---------------------------------------------------------------------------


class TestParserAgent:
    async def test_extracts_skills(self, mock_anthropic_client, llm_response):
        body = '{"skills": ["langgraph", "rag"], "role": "engineer", "constraints": {}}'
        mock_anthropic_client.messages.create = AsyncMock(return_value=llm_response(body))

        result = await parser_agent(mock_anthropic_client, "who works on langgraph in Kolkata")

        assert isinstance(result, ParsedQuery)
        assert "langgraph" in result.skills
        assert result.role == "engineer"

    async def test_extracts_mentor_role(self, mock_anthropic_client, llm_response):
        body = '{"skills": ["machine learning"], "role": "mentor", "constraints": {}}'
        mock_anthropic_client.messages.create = AsyncMock(return_value=llm_response(body))

        result = await parser_agent(mock_anthropic_client, "who mentors ML engineers")

        assert result.role == "mentor"
        assert "machine learning" in result.skills

    async def test_extracts_json_from_fenced_prose_response(
        self, mock_anthropic_client, llm_response
    ):
        body = """Here is the structured output:

```json
{"skills": ["langgraph", "rag"], "role": "engineer", "constraints": {}}
```"""
        mock_anthropic_client.messages.create = AsyncMock(return_value=llm_response(body))

        result = await parser_agent(mock_anthropic_client, "who works on langgraph in Kolkata")

        assert result.skills == ["langgraph", "rag"]
        assert result.role == "engineer"

    async def test_falls_back_on_invalid_json(self, mock_anthropic_client, llm_response):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response("not valid json at all")
        )
        result = await parser_agent(mock_anthropic_client, "some query")

        assert isinstance(result, ParsedQuery)
        assert result.skills == []
        assert result.role is None
        assert result.raw == "some query"

    async def test_falls_back_on_llm_exception(self, mock_anthropic_client):
        mock_anthropic_client.messages.create = AsyncMock(side_effect=Exception("network error"))

        result = await parser_agent(mock_anthropic_client, "some query")

        assert result.skills == []
        assert result.raw == "some query"

    async def test_raw_always_contains_original_query(self, mock_anthropic_client, llm_response):
        body = '{"skills": [], "role": null, "constraints": {}}'
        mock_anthropic_client.messages.create = AsyncMock(return_value=llm_response(body))

        result = await parser_agent(mock_anthropic_client, "jadavpur cluster")

        assert result.raw == "jadavpur cluster"


# ---------------------------------------------------------------------------
# retriever_step
# ---------------------------------------------------------------------------


class TestRetrieverStep:
    def test_builds_search_text_from_skills(self, mock_retriever):
        parsed = ParsedQuery(
            skills=["langgraph", "nlp"], role="engineer", constraints={}, raw="original"
        )
        retriever_step(parsed, mock_retriever, k=10)

        call_args = mock_retriever.query.call_args
        search_text = call_args[0][0]
        assert "langgraph" in search_text
        assert "nlp" in search_text

    def test_includes_role_in_search_text(self, mock_retriever):
        parsed = ParsedQuery(skills=[], role="mentor", constraints={}, raw="find mentors")
        retriever_step(parsed, mock_retriever)

        search_text = mock_retriever.query.call_args[0][0]
        assert "mentor" in search_text

    def test_falls_back_to_raw_when_no_skills(self, mock_retriever):
        parsed = ParsedQuery(skills=[], role=None, constraints={}, raw="jadavpur developers")
        retriever_step(parsed, mock_retriever)

        search_text = mock_retriever.query.call_args[0][0]
        assert "jadavpur" in search_text

    def test_passes_k_to_retriever(self, mock_retriever):
        parsed = ParsedQuery(skills=[], role=None, constraints={}, raw="q")
        retriever_step(parsed, mock_retriever, k=7)

        assert mock_retriever.query.call_args[1]["k"] == 7 or \
               mock_retriever.query.call_args[0][1] == 7

    def test_returns_retriever_results(self, mock_retriever):
        parsed = ParsedQuery(skills=[], role=None, constraints={}, raw="q")
        results = retriever_step(parsed, mock_retriever)

        assert results == mock_retriever.query.return_value


# ---------------------------------------------------------------------------
# ranker_agent
# ---------------------------------------------------------------------------


class TestRankerAgent:
    async def test_reorders_results_by_llm_preference(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response('["bob", "alice"]')
        )
        results = [
            Result("alice", 0.9, ["evidence a"]),
            Result("bob", 0.7, ["evidence b"]),
        ]
        reranked = await ranker_agent(mock_anthropic_client, "nlp", results, mock_retriever)

        assert reranked[0].person_id == "bob"
        assert reranked[1].person_id == "alice"

    async def test_ignores_unknown_ids_from_llm(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response('["alice", "unknown_person"]')
        )
        results = [Result("alice", 0.9, [])]
        reranked = await ranker_agent(mock_anthropic_client, "q", results, mock_retriever)

        ids = [r.person_id for r in reranked]
        assert "unknown_person" not in ids

    async def test_appends_results_llm_omitted(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response('["alice"]')
        )
        results = [Result("alice", 0.9, []), Result("bob", 0.7, [])]
        reranked = await ranker_agent(mock_anthropic_client, "q", results, mock_retriever)

        ids = [r.person_id for r in reranked]
        assert "bob" in ids

    async def test_accepts_wrapped_ranker_payload(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response('{"ids": ["bob", "alice"]}')
        )
        results = [
            Result("alice", 0.9, []),
            Result("bob", 0.7, []),
        ]

        reranked = await ranker_agent(mock_anthropic_client, "q", results, mock_retriever)

        assert [r.person_id for r in reranked[:2]] == ["bob", "alice"]

    async def test_falls_back_to_original_order_on_invalid_json(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response("not json")
        )
        results = [Result("alice", 0.9, []), Result("bob", 0.7, [])]
        reranked = await ranker_agent(mock_anthropic_client, "q", results, mock_retriever)

        assert reranked[0].person_id == "alice"
        assert reranked[1].person_id == "bob"

    async def test_returns_empty_for_empty_input(
        self, mock_anthropic_client, mock_retriever
    ):
        result = await ranker_agent(mock_anthropic_client, "q", [], mock_retriever)
        assert result == []
        mock_anthropic_client.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# composer_agent
# ---------------------------------------------------------------------------


class TestComposerAgent:
    async def test_returns_list_of_composed_results(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        body = '[{"id": "alice", "evidence": ["LangGraph expert", "GDG organizer"]}]'
        mock_anthropic_client.messages.create = AsyncMock(return_value=llm_response(body))
        results = [Result("alice", 0.9, [])]

        composed = await composer_agent(mock_anthropic_client, "langgraph", results, mock_retriever)

        assert len(composed) == 1
        assert isinstance(composed[0], ComposedResult)

    async def test_enriches_evidence_from_llm(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        body = '[{"id": "alice", "evidence": ["Built Bengali LangGraph pipeline"]}]'
        mock_anthropic_client.messages.create = AsyncMock(return_value=llm_response(body))
        results = [Result("alice", 0.9, ["raw evidence"])]

        composed = await composer_agent(mock_anthropic_client, "q", results, mock_retriever)

        assert "Built Bengali LangGraph pipeline" in composed[0].evidence

    async def test_falls_back_to_raw_evidence_on_invalid_json(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response("invalid json")
        )
        results = [Result("alice", 0.9, ["raw evidence A", "raw evidence B"])]

        composed = await composer_agent(mock_anthropic_client, "q", results, mock_retriever)

        assert composed[0].evidence == ["raw evidence A", "raw evidence B"]

    async def test_result_has_name_and_url_from_retriever(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        body = '[{"id": "alice", "evidence": ["LangGraph"]}]'
        mock_anthropic_client.messages.create = AsyncMock(return_value=llm_response(body))
        results = [Result("alice", 0.88, [])]

        composed = await composer_agent(mock_anthropic_client, "q", results, mock_retriever)

        assert composed[0].name == "Alice Roy"
        assert composed[0].url == "https://github.com/alice"

    async def test_score_is_rounded(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        body = '[{"id": "alice", "evidence": ["x"]}]'
        mock_anthropic_client.messages.create = AsyncMock(return_value=llm_response(body))
        results = [Result("alice", 0.876543, [])]

        composed = await composer_agent(mock_anthropic_client, "q", results, mock_retriever)

        assert composed[0].score == round(0.876543, 3)

    async def test_accepts_wrapped_composer_payload_and_string_evidence(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        body = '{"results": [{"person_id": "alice", "evidence": "Built Bengali LangGraph tooling"}]}'
        mock_anthropic_client.messages.create = AsyncMock(return_value=llm_response(body))
        results = [Result("alice", 0.88, ["raw evidence"])]

        composed = await composer_agent(mock_anthropic_client, "q", results, mock_retriever)

        assert composed[0].evidence == ["Built Bengali LangGraph tooling"]

    async def test_returns_empty_for_empty_input(
        self, mock_anthropic_client, mock_retriever
    ):
        composed = await composer_agent(mock_anthropic_client, "q", [], mock_retriever)
        assert composed == []
        mock_anthropic_client.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# run_pipeline
# ---------------------------------------------------------------------------


class TestRunPipeline:
    async def test_returns_results_and_subgraph(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        # parser -> valid JSON, ranker -> valid order, composer -> enriched
        responses = [
            llm_response('{"skills": ["langgraph"], "role": "engineer", "constraints": {}}'),
            llm_response('["alice", "bob"]'),
            llm_response('[{"id": "alice", "evidence": ["LangGraph engineer"]}]'),
        ]
        mock_anthropic_client.messages.create = AsyncMock(side_effect=responses)

        result = await run_pipeline("langgraph kolkata", mock_retriever, mock_anthropic_client)

        assert "results" in result
        assert "subgraph" in result

    async def test_results_have_required_fields(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        responses = [
            llm_response('{"skills": [], "role": null, "constraints": {}}'),
            llm_response('["alice"]'),
            llm_response('[{"id": "alice", "evidence": ["evidence"]}]'),
        ]
        mock_anthropic_client.messages.create = AsyncMock(side_effect=responses)

        result = await run_pipeline("q", mock_retriever, mock_anthropic_client)

        for item in result["results"]:
            for field in ("id", "name", "score", "evidence", "url"):
                assert field in item, f"Missing field '{field}' in result item"

    async def test_subgraph_has_nodes_and_edges(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        responses = [
            llm_response('{"skills": [], "role": null, "constraints": {}}'),
            llm_response('["alice"]'),
            llm_response('[{"id": "alice", "evidence": ["x"]}]'),
        ]
        mock_anthropic_client.messages.create = AsyncMock(side_effect=responses)

        result = await run_pipeline("q", mock_retriever, mock_anthropic_client)

        assert "nodes" in result["subgraph"]
        assert "edges" in result["subgraph"]

    async def test_uses_fallback_when_flag_set(
        self, mock_anthropic_client, mock_retriever, llm_response, monkeypatch
    ):
        monkeypatch.setattr(agents_module, "USE_FALLBACK", True)
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response('[{"id": "alice", "evidence": ["x"]}]')
        )

        result = await run_pipeline("q", mock_retriever, mock_anthropic_client)

        assert "results" in result
        # Parser and Ranker should NOT have been called (only 1 LLM call for Composer)
        assert mock_anthropic_client.messages.create.call_count == 1

    async def test_full_pipeline_runs_all_four_agents(
        self, mock_anthropic_client, mock_retriever, llm_response, monkeypatch
    ):
        monkeypatch.setattr(agents_module, "USE_FALLBACK", False)

        responses = [
            llm_response('{"skills": ["python"], "role": null, "constraints": {}}'),
            llm_response('["alice"]'),
            llm_response('[{"id": "alice", "evidence": ["x"]}]'),
        ]
        mock_anthropic_client.messages.create = AsyncMock(side_effect=responses)

        result = await run_pipeline("q", mock_retriever, mock_anthropic_client)

        # Parser + Ranker + Composer = 3 LLM calls
        assert mock_anthropic_client.messages.create.call_count == 3
        assert "results" in result

    async def test_empty_retrieval_returns_message(
        self, mock_anthropic_client, mock_retriever, llm_response, monkeypatch
    ):
        monkeypatch.setattr(agents_module, "USE_FALLBACK", True)
        mock_retriever.query.return_value = []

        result = await run_pipeline("q", mock_retriever, mock_anthropic_client)

        assert result["results"] == []
        assert "message" in result


# ---------------------------------------------------------------------------
# run_fallback
# ---------------------------------------------------------------------------


class TestRunFallback:
    async def test_returns_correct_shape(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response('[{"id": "alice", "evidence": ["x"]}]')
        )
        result = await run_fallback("q", mock_retriever, mock_anthropic_client)

        assert "results" in result
        assert "subgraph" in result

    async def test_calls_retriever_with_raw_query(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response('[{"id": "alice", "evidence": ["x"]}]')
        )
        await run_fallback("jadavpur cluster", mock_retriever, mock_anthropic_client)

        mock_retriever.query.assert_called_once_with("jadavpur cluster", k=10)

    async def test_empty_retrieval_returns_message(
        self, mock_anthropic_client, mock_retriever
    ):
        mock_retriever.query.return_value = []

        result = await run_fallback("q", mock_retriever, mock_anthropic_client)

        assert result["results"] == []
        assert "message" in result
        mock_anthropic_client.messages.create.assert_not_called()

    async def test_does_not_call_parser_or_ranker(
        self, mock_anthropic_client, mock_retriever, llm_response
    ):
        mock_anthropic_client.messages.create = AsyncMock(
            return_value=llm_response('[{"id": "alice", "evidence": ["x"]}]')
        )
        await run_fallback("q", mock_retriever, mock_anthropic_client)

        # only the Composer calls the LLM — exactly once
        assert mock_anthropic_client.messages.create.call_count == 1

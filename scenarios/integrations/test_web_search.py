"""Web search (`integrations.WebSearch`), one smoke per engine, each
under the key its own variable holds — TAVILY_API_KEY, EXA_API_KEY —
and skipping where it is not set: a query answers pages, each with its
title, and snippets the model would read."""

import pytest

from otaku.integrations import IntegrationConfig, WebSearch
from scenarios.support.live import require_env

pytestmark = [pytest.mark.live, pytest.mark.integration]

KEYS = {"tavily": "TAVILY_API_KEY", "exa": "EXA_API_KEY"}


class TestWebSearch:
    @pytest.mark.parametrize("variable", KEYS.values(), ids=KEYS.keys())
    def test_a_query_answers_pages_with_snippets(self, variable: str) -> None:
        search = WebSearch(IntegrationConfig(WebSearch.id, "", require_env(variable)))
        results = search.search("the highest mountain on Earth", max_results=3)
        assert results
        assert all(result.title for result in results)
        assert any(result.snippet for result in results)

"""Web search: the top results' snippets for a query, never a full page,
under ONE key — the engine asked is the one the key belongs to, told
from the key's own shape when a search is asked (`_get_search_engine`),
so nothing outside this module names an engine."""

from dataclasses import dataclass

from otaku.integrations.base import Integration, IntegrationError, post_json

_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_EXA_ENDPOINT = "https://api.exa.ai/search"
_TAVILY_KEY_PREFIX = "tvly"  # what Tavily's keys start with; Exa's carry no mark


@dataclass(frozen=True)
class SearchResult:
    """One result of a web search: the page's title and the snippet the
    engine chose for the query."""

    title: str
    snippet: str


class WebSearch(Integration):
    """Web search as an integration: its section holds the one key, and
    `search` asks the engine that key belongs to."""

    id = "web_search"
    label = "Web search"

    def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        """The top results for `query`. Raises IntegrationError where the
        engine could not be asked — no request at all without a key."""
        if not self.api_key:
            raise IntegrationError("the key is missing")
        if _get_search_engine(self.api_key) == "tavily":
            return _search_tavily(self.api_key, query, max_results)
        return _search_exa(self.api_key, query, max_results)


def _get_search_engine(key: str) -> str:
    """The engine `key` belongs to, told from its shape: "tavily" for a
    key carrying Tavily's mark, "exa" for any other."""
    return "tavily" if key.startswith(_TAVILY_KEY_PREFIX) else "exa"


def _search_tavily(key: str, query: str, max_results: int) -> list[SearchResult]:
    """Tavily: query-focused snippets, a couple of chunks per source, no
    synthesized answer and never a full page."""
    data = post_json(
        _TAVILY_ENDPOINT,
        {"Authorization": f"Bearer {key}"},
        {
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "chunks_per_source": 2,
            "include_answer": False,
            "include_raw_content": False,
        },
    )
    return [
        SearchResult(str(r.get("title", "")), str(r.get("content") or ""))
        for r in _results_of(data)
    ]


def _search_exa(key: str, query: str, max_results: int) -> list[SearchResult]:
    """Exa: extractive highlights chosen to match the query, never a full
    page; moderation off keeps a story's immersion."""
    data = post_json(
        _EXA_ENDPOINT,
        {"x-api-key": key},
        {
            "query": query,
            "type": "auto",
            "numResults": max_results,
            "moderation": False,
            "contents": {"highlights": {"query": query, "maxCharacters": 500}},
        },
    )
    out: list[SearchResult] = []
    for r in _results_of(data):
        highlights = r.get("highlights")
        found = [str(h) for h in highlights] if isinstance(highlights, list) else []
        out.append(SearchResult(str(r.get("title", "")), " … ".join(found)))
    return out


def _results_of(data: object) -> list[dict[str, object]]:
    """The `results` list of an engine's answer, each a dict; raises
    IntegrationError where the answer has none."""
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise IntegrationError("the service answered without results")
    return [r for r in results if isinstance(r, dict)]

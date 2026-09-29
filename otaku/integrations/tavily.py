"""Tavily: query-focused snippets, a couple of chunks per source, no
synthesized answer and never a full page."""

from otaku.integrations.base import SearchEngine, SearchResult, post_json, results_of


class Tavily(SearchEngine):
    id = "tavily"
    label = "Tavily"
    endpoint = "https://api.tavily.com/search"
    key_prefix = "tvly"

    def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        data = post_json(
            self.url,
            {"Authorization": f"Bearer {self.api_key}"},
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
            for r in results_of(data)
        ]

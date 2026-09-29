"""Exa: extractive highlights chosen to match the query, never a full
page; moderation off keeps a story's immersion."""

from otaku.integrations.base import SearchEngine, SearchResult, post_json, results_of


class Exa(SearchEngine):
    id = "exa"
    label = "Exa"
    endpoint = "https://api.exa.ai/search"

    def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        data = post_json(
            self.url,
            {"x-api-key": self.api_key},
            {
                "query": query,
                "type": "auto",
                "numResults": max_results,
                "moderation": False,
                "contents": {"highlights": {"query": query, "maxCharacters": 500}},
            },
        )
        out: list[SearchResult] = []
        for r in results_of(data):
            highlights = r.get("highlights")
            found = [str(h) for h in highlights] if isinstance(highlights, list) else []
            out.append(SearchResult(str(r.get("title", "")), " … ".join(found)))
        return out

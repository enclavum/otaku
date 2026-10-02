"""What an integration is: a service otaku asks for something other than
a completion — a web search engine, so far — named by its section in
integrations.toml, its endpoint fixed by its class and its key the
section's; and the one transport they share. Which integrations there
are is the package's own registry (`ALL_INTEGRATIONS`)."""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import ClassVar

from otaku.settings.integrations import IntegrationConfig

TIMEOUT = 15.0  # the longest a service is waited on


class IntegrationError(Exception):
    """The service could not be asked, or answered nothing readable."""


class Integration:
    """One integration, declared by its class — its `id` (the section
    name), its `label` (what a frontend calls it) and its fixed
    `endpoint` — and bound, as an instance, to its section: the `url` in
    force and the `api_key`."""

    id: ClassVar[str] = ""
    label: ClassVar[str] = ""
    endpoint: ClassVar[str] = ""  # the service's fixed url, what a section is founded with
    # What the service's keys start with, where they carry a mark ("tvly"
    # on Tavily's); "" where they carry none — how a key typed with no
    # service named is told (`search_engine_of_key`).
    key_prefix: ClassVar[str] = ""

    url: str
    api_key: str

    def __init__(self, config: IntegrationConfig) -> None:
        self.url = config.url or self.endpoint
        self.api_key = config.api_key

    @classmethod
    def default_section(cls) -> IntegrationConfig:
        """The integration's section as founded: its endpoint, no key —
        what first run writes, and what a later launch founds where
        missing, so the file shows where a key goes. Nothing is detected:
        an external service has no port or key on this machine to find."""
        return IntegrationConfig(name=cls.id, url=cls.endpoint)


@dataclass(frozen=True)
class SearchResult:
    """One result of a web search: the page's title and the snippet the
    engine chose for the query."""

    title: str
    snippet: str


class SearchEngine(Integration):
    """An integration that searches the web: `search` answers a query
    with the top results' snippets, never a full page. Raises
    IntegrationError where the engine could not be asked."""

    def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        raise NotImplementedError(f"{self.id} is no search engine")


def post_json(url: str, headers: dict[str, str], body: dict[str, object]) -> object:
    """POST `body` as JSON and parse the answer. Every failure is an
    IntegrationError with the sentence for it."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:
            return json.loads(reply.read())
    except urllib.error.HTTPError as e:
        # 401 is the key's verdict, whatever the service: a key was sent
        # (a missing one never reaches a service), and it is not valid.
        if e.code == 401:
            raise IntegrationError("the key is not valid") from e
        raise IntegrationError(f"the service answered {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise IntegrationError(f"could not reach the service ({reason})") from e
    except ValueError as e:
        raise IntegrationError("the service's answer was not JSON") from e


def results_of(data: object) -> list[dict[str, object]]:
    """The `results` list of a search engine's answer, each a dict;
    raises IntegrationError where the answer has none."""
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise IntegrationError("the service answered without results")
    return [r for r in results if isinstance(r, dict)]

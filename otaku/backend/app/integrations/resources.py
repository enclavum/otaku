"""The integrations as a frontend reaches them (`IntegrationsResource`):
web search, its ONE key in either frontend — sealed into its section of
integrations.toml and live in the app at once, never read back out — and
otaku's own support desk, a reader's request sent through the site. The
work is the domain's (`app.integrations.services`)."""

from __future__ import annotations

from otaku.backend.app.core.services import App
from otaku.backend.app.integrations import services as domain_integrations
from otaku.backend.app.integrations.schemas import WebSearch
from otaku.backend.base import Changed, Resource


class IntegrationsResource(Resource):
    """/integrations — what otaku asks of a service besides the model."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.web_search = WebSearchResource(app)
        self.support_requests = SupportRequestsResource(app)


class WebSearchResource(Resource):
    """/integrations/web-search — the web search integration."""

    def get(self) -> WebSearch:
        """Where web search stands — whether it holds a key, never the
        key."""
        return WebSearch(domain_integrations.get_web_search(self._app))

    def update(self, *, api_key: str) -> Changed[WebSearch]:
        """The web search key: sealed into its section (founded where a
        hand edit dropped it), live in the app at once; "" forgets it, and
        forgetting none says so. Raises Refused for a key that could not
        be sealed and a write the file would not take — nothing
        changed."""
        said = domain_integrations.update_web_search(self._app, api_key)
        return Changed(self.get(), said)


class SupportRequestsResource(Resource):
    """/integrations/support-requests — a reader's request to otaku's
    author."""

    def create(self, *, body: str, email: str = "") -> Changed[None]:
        """Send `body`, and `email` where one is given, to otaku's
        author. Raises Refused for an empty or too long body, an address
        that is none, and a request that could not be sent."""
        return Changed(
            None, domain_integrations.create_support_request(self._app, body, email=email)
        )

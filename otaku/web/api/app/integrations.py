"""The integrations, as the page asks for them: web search — whether it
holds a key, and its key saved, one box on the Tools tab — and a support
request sent from the Support docket."""

from typing import Any

from otaku.backend.api import Api
from otaku.web.api._request import Ask, Route, changed, plain


def _send_support_request(api: Api, ask: Ask) -> dict[str, Any]:
    # The message must be there, "" the backend's to refuse in words; the
    # address may be left out or null, which is none given.
    email = ask.body.get("email")
    return changed(
        api.integrations.support_requests.create(
            body=ask.need("body"), email=email if isinstance(email, str) else ""
        )
    )


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    # Whether a key is held — the key's value never crosses.
    ("GET", "/api/integrations/web-search"): lambda api, ask: plain(
        api.integrations.web_search.get()
    ),
    # `need`, not a bare str(): a null here would seal the literal key
    # "None" (`Ask.need`). "" forgets the key.
    ("PATCH", "/api/integrations/web-search"): lambda api, ask: changed(
        api.integrations.web_search.update(api_key=ask.need("api_key"))
    ),
    ("POST", "/api/integrations/support-requests"): _send_support_request,
}

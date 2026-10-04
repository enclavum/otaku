"""Web search as the page asks for it: where it stands, and its key
saved — one box on the Tools tab."""

from typing import Any

from otaku.backend.api import integrations as api_integrations
from otaku.backend.session import Session
from otaku.web.api.request import Ask, Route


def _web_search(session: Session) -> dict[str, Any]:
    """Whether web search holds a key; the key's value is never sent."""
    return {"has_key": api_integrations.web_search(session).has_key}


def _save_web_search_key(session: Session, ask: Ask) -> str:
    # `need`, not a bare str(): a null here would seal the literal key
    # "None" (`Ask.need`). "" forgets the key.
    return api_integrations.set_web_search_key(session, ask.need("api_key"))


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/integrations/web_search"): lambda session, ask: _web_search(session),
    ("PATCH", "/api/integrations/web_search"): _save_web_search_key,
}

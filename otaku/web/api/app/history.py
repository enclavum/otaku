"""The composer's history, as the page walks it: the lines submitted,
most recent first — the same store-backed lines the terminal prompt
walks, so a reload, or the other frontend, starts with the history it
left — and one more line kept."""

from otaku.web.api._request import Route, changed

# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/history"): lambda api, ask: {"items": api.history.list()},
    # Fired by the page beside every submission; blanks and immediate
    # repeats are the backend's to skip.
    ("POST", "/api/history"): lambda api, ask: changed(api.history.create(text=ask.need("text"))),
}

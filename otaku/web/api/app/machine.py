"""The machine a model fills, as the page watches it: its memory alone —
one syscall, where asking the providers would re-probe every one of
them — read again while a model loads."""

from otaku.web.api._request import Route, plain

# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/app/machine"): lambda api, ask: plain(api.app.machine.get()),
}

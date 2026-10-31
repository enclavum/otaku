"""The story's typed language, as the page reads it: the openers a line
may start with and the inliners it may carry, tokens and argument
shapes, and what a line with none of them does — what the composer's
menu offers and the help sheet lists. What each one MEANS in a menu is
the page's own caption; the rules a line not stored yet is read by are
copied in the page, citing `backend.story.language`."""

from otaku.web.api._request import Route, plain

# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/language"): lambda api, ask: plain(api.language.get()),
}

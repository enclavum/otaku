"""The integrations, typed, carried out — the twin of
`api.app.integrations`: a support request sent to otaku's author
(`/support TEXT`) — the whole line the request, an address in it
included."""

from collections.abc import Callable

from otaku.terminal.chat.chat import Chat

# ---------- the rows ----------


HANDLERS: dict[str, Callable[[Chat, str], str | None]] = {
    # the whole line is the request, an address to answer at included
    "/support": lambda chat, raw: chat.api.integrations.support_requests.create(body=raw).notice,
}

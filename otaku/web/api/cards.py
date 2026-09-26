"""Character cards, as the page imports them: read and vetted in one
request, landed with the persona's answer in the next — the flow that
waits in `Pending` on its token."""

import base64
from typing import Any

from otaku.backend.api import cards as api_cards
from otaku.backend.session import Refused, Session
from otaku.web.api.request import Ask, Created, Flow, Pending


def _prepare_card(session: Session, ask: Ask, pending: Pending) -> Created:
    """Everything before the persona ask. The prepared card is OPAQUE —
    it waits in `Pending` and the page hands back only the token and the
    answer. Bytes-shaped for the same reason as an import: the web
    uploads what it read, because a path over HTTP would name a file on
    the SERVER."""
    prepared = api_cards.prepare(
        session,
        base64.b64decode(ask.need("data")),
        ask.need("name"),
        ask.text("rename"),
    )
    token = pending.hold_card(prepared)
    return Created(
        "",
        f"/api/cards/{token}",
        {
            "token": token,
            "card": {
                "name": prepared.card.name,
                "notes": list(prepared.notes),
                "tokens": prepared.block_tokens,
                "large": prepared.large,
                "persona": api_cards.remembered_persona(session),
            },
        },
    )


def _add_card(session: Session, ask: Ask, pending: Pending) -> dict[str, Any]:
    """Everything after it. Nothing waiting — a page that asked twice, or
    a reload between the two halves — is an expected decline, and refuses
    the way every other one does."""
    prepared = pending.take_card(ask.params["token"])
    if prepared is None:
        raise Refused("No card is waiting.")
    return {"notice": api_cards.add(session, prepared, ask.need("persona")).report}


# ---------- the rows ----------


FLOWS: dict[tuple[str, str], Flow] = {
    ("POST", "/api/cards"): _prepare_card,
    ("PUT", "/api/cards/{token}"): _add_card,
}

"""A support request as the page sends it from its Support docket: the
message, and the address to answer at where the reader gave one."""

from otaku.backend.api import support as api_support
from otaku.backend.session import Session
from otaku.web.api.request import Ask, Route


def _send(session: Session, ask: Ask) -> str:
    # The message must be there, "" the backend's to refuse in words; the
    # address may be left out or null, which is none given.
    email = ask.body.get("email")
    return api_support.send_support_request(
        session, ask.need("message"), email if isinstance(email, str) else ""
    )


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("POST", "/api/support"): _send,
}

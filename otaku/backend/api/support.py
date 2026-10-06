"""A support request: a reader's message to otaku's author, and the
address to answer at where one is given, sent through the site (the
session's support desk, `integrations.SupportDesk`). Checked here before anything leaves, so a
refusal is a sentence and never a mail the endpoint drops or bounces."""

import re

from otaku import __version__
from otaku.backend.session import Refused, Session
from otaku.integrations import IntegrationError, SupportDesk

# What the endpoint takes for an address — its rule, copied (the otaku.sh
# repository's src/index.js). It would drop one that fails and keep the
# message; refused here instead, since an answer is why it was given.
_EMAIL = re.compile(r"[^@\s]+@[^@\s.]+\.[^@\s]+")
_MAX_EMAIL = 200  # the endpoint's cut


def send_support_request(session: Session, body: str, email: str = "") -> str:
    """Send `body`, and `email` where one is given, to otaku's author.
    Returns the confirmation; raises Refused for an empty or too long
    body, an address that is none, and a request that could not be
    sent."""
    text = body.strip()
    address = email.strip()
    if not text:
        raise Refused("Nothing to send: write what you need help with.")
    if len(text) > SupportDesk.max_message:
        raise Refused(
            f"The request is {len(text):,} characters long; it can be at most "
            f"{SupportDesk.max_message:,}."
        )
    if address and (len(address) > _MAX_EMAIL or not _EMAIL.fullmatch(address)):
        raise Refused(f"{address} is not an email address.")
    desk = session.integrations.get(SupportDesk)
    try:
        desk.send(text, address, user_agent=f"otaku/{__version__}")
    except IntegrationError as e:
        raise Refused(f"The request could not be sent: {e}.") from e
    return "Your request was sent."

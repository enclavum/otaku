"""otaku's own support desk: a request a reader sends from the app,
posted to the site (`SUPPORT_ENDPOINT`, the Worker in the otaku.sh
repository), which mails it to the author through Resend — so the app
holds no key, and the desk has no section in integrations.toml and no
place in the registry. The endpoint takes the message and, where one is
given, the address to answer at; it answers 204, and refuses an empty
message or one over `MAX_SUPPORT_MESSAGE` characters."""

from otaku.integrations.base import post_json

SUPPORT_ENDPOINT = "https://otaku.sh/api/support"
MAX_SUPPORT_MESSAGE = 5000  # the endpoint's own limit, its form's maxlength


def post_support_request(message: str, email: str, *, user_agent: str) -> None:
    """Post one request — `email` "" where none is given. Raises
    IntegrationError where it could not be sent."""
    body: dict[str, object] = {"message": message}
    if email:
        body["email"] = email
    post_json(SUPPORT_ENDPOINT, {"User-Agent": user_agent}, body)

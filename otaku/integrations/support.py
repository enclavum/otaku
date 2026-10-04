"""otaku's own support desk: a request a reader sends from the app,
posted to the site (`SupportDesk.endpoint`, the Worker in the otaku.sh
repository), which mails it to the author through Resend — so the app
holds no key, and the desk has no section in integrations.toml. The
endpoint takes the message and, where one is given, the address to
answer at; it answers 204, and refuses an empty message or one over
`SupportDesk.max_message` characters."""

from typing import ClassVar

from otaku.integrations.base import Integration, post_json


class SupportDesk(Integration):
    """The support desk as an integration: its fixed endpoint, no key and
    no section — bound to the one it is founded with."""

    id = "support"
    label = "Support"
    configurable = False
    endpoint = "https://otaku.sh/api/support"
    max_message: ClassVar[int] = 5000  # the endpoint's own limit, its form's maxlength

    def send(self, message: str, email: str, *, user_agent: str) -> None:
        """Post one request — `email` "" where none is given. Raises
        IntegrationError where it could not be sent."""
        body: dict[str, object] = {"message": message}
        if email:
            body["email"] = email
        post_json(self.url, {"User-Agent": user_agent}, body)

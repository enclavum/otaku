"""What an integration is: something otaku asks for other than a
completion — web search, the support desk, the sky's facts — its
endpoint fixed by its class, bound to its section's url and key;
and the one transport they share. Which integrations there are is the
package's own registry (`ALL_INTEGRATIONS`)."""

from typing import ClassVar

import httpx

from otaku.formatting import Money
from otaku.settings.integrations import IntegrationConfig

TIMEOUT = 15.0  # the longest a service is waited on


class IntegrationError(Exception):
    """The service could not be asked, or answered nothing readable."""


class Integration:
    """One integration, declared by its class — its `id` (its section's
    name), its `label` (what a frontend calls it) and its fixed
    `endpoint` — and bound, as an instance, to its section: the `url` in
    force and the `api_key`."""

    id: ClassVar[str] = ""
    label: ClassVar[str] = ""
    endpoint: ClassVar[str] = ""  # the service's fixed url, what a section is founded with
    # Whether integrations.toml gets a section for it.
    configurable: ClassVar[bool] = True

    url: str
    api_key: str

    def __init__(self, config: IntegrationConfig) -> None:
        self.url = config.url or self.endpoint
        self.api_key = config.api_key

    @classmethod
    def default_section(cls) -> IntegrationConfig:
        """The integration's section as founded: its endpoint, no key —
        what first run writes, and what a later launch founds where
        missing, so the file shows where a key goes. Nothing is detected:
        an external service has no port or key on this machine to find."""
        return IntegrationConfig(name=cls.id, url=cls.endpoint)

    def balance(self) -> Money | None:
        """What the account behind the integration has left — None where
        there is no account, or it does not say. Declared for a report to
        come: no integration answers one yet."""
        return None


def post_json(url: str, headers: dict[str, str], body: dict[str, object]) -> object:
    """POST `body` as JSON and parse the answer — None where the service
    answers 204, no content. Every failure is an IntegrationError with
    the sentence for it."""
    try:
        reply = httpx.post(url, json=body, headers=headers, timeout=TIMEOUT)
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        raise IntegrationError(f"could not reach the service ({e})") from e
    # 401 is the key's verdict, whatever the service: a key was sent (a
    # missing one never reaches a service), and it is not valid.
    if reply.status_code == 401:
        raise IntegrationError("the key is not valid")
    if not reply.is_success:
        raise IntegrationError(f"the service answered {reply.status_code}")
    if reply.status_code == 204:
        return None
    try:
        return reply.json()
    except ValueError as e:
        raise IntegrationError("the service's answer was not JSON") from e

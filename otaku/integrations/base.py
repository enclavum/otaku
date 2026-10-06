"""What an integration is: something otaku asks for other than a
completion — web search, the support desk, the sky's facts — its
endpoint fixed by its class, bound to its section's url and key;
and the one transport they share. Which integrations there are is the
package's own registry (`ALL_INTEGRATIONS`)."""

import json
import urllib.error
import urllib.request
from typing import ClassVar

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
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:
            if reply.status == 204:
                return None
            return json.loads(reply.read())
    except urllib.error.HTTPError as e:
        # 401 is the key's verdict, whatever the service: a key was sent
        # (a missing one never reaches a service), and it is not valid.
        if e.code == 401:
            raise IntegrationError("the key is not valid") from e
        raise IntegrationError(f"the service answered {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise IntegrationError(f"could not reach the service ({reason})") from e
    except ValueError as e:
        raise IntegrationError("the service's answer was not JSON") from e

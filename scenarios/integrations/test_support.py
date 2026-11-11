"""otaku's support desk (`integrations.SupportDesk`), asked without
sending a mail: an empty message, which the site's endpoint refuses
before anything is sent — so the endpoint is there and judging what it
is sent."""

import pytest

from otaku import __version__
from otaku.integrations import IntegrationError, SupportDesk

pytestmark = [pytest.mark.live, pytest.mark.integration]


class TestSupportDesk:
    def test_an_empty_message_is_refused_and_nothing_sent(self) -> None:
        desk = SupportDesk(SupportDesk.default_section())
        with pytest.raises(IntegrationError, match="answered 400"):
            desk.send("", "", user_agent=f"otaku/{__version__}")

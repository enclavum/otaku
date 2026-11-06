"""The card sites, as far as a source is told without asking any site:
an address that is no web address, and a known site's page that is no
character's — or, on Civitai, no picture's — refused before a request
goes out (`CardSourceError`). What the sites answer is the integrations
suite's (`scenarios/integrations/test_cards.py`)."""

import pytest

from otaku.integrations import Cards, CardSourceError
from otaku.settings.integrations import IntegrationConfig

_AGENT = "otaku/test"


class TestSources:
    @pytest.mark.parametrize(
        "source", ["", "   ", "chub.ai/characters/a/b", "ftp://chub.ai/characters/a/b", "https://"]
    )
    def test_no_web_address_is_refused(self, source: str) -> None:
        with pytest.raises(CardSourceError):
            _cards().fetch(source, user_agent=_AGENT)

    @pytest.mark.parametrize(
        "source",
        [
            "https://chub.ai/",
            "https://chub.ai/search",
            "https://character-tavern.com/user/someone",
            "https://realm.risuai.net/character/not-an-id!",
            "https://pygmalion.chat/user/someone",
            "https://app.wyvern.chat/explore",
            "https://civitai.com/models/12",
            "https://civitai.red/images/not-a-number",
        ],
    )
    def test_a_known_sites_page_that_is_none_of_a_cards_is_refused(self, source: str) -> None:
        with pytest.raises(CardSourceError):
            _cards().fetch(source, user_agent=_AGENT)


def _cards() -> Cards:
    return Cards(IntegrationConfig(name="cards", url=""))

"""The card sites, as far as a source is told without asking any site:
the sites listed by name, alphabetically, each host one site's; an
address that is no web address, and a known site's page that is no
character's — or, on Civitai, no picture's — refused before a request
goes out (`CardSourceError`). What the sites answer is the integrations
suite's (`scenarios/integrations/test_cards.py`)."""

import pytest

from otaku.integrations import Cards, CardSourceError
from otaku.settings.integrations import IntegrationConfig

_AGENT = "otaku/test"


class TestSites:
    def test_the_sites_are_listed_by_name_alphabetically(self) -> None:
        names = list(Cards.sites())
        assert names and names == sorted(names, key=str.casefold)

    def test_each_host_is_one_sites(self) -> None:
        hosts = [host for hosts in Cards.sites().values() for host in hosts]
        assert all(Cards.sites().values()) and len(hosts) == len(set(hosts))

    def test_every_host_listed_is_one_a_page_is_fetched_from(self) -> None:
        # its home page is no character's: refused for the site, never fetched as a file
        for hosts in Cards.sites().values():
            for host in hosts:
                with pytest.raises(CardSourceError):
                    _cards().fetch(f"https://{host}/", user_agent=_AGENT)


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

"""The card sites (`integrations.cards`): a link to a card pinned on each
gives a file that reads as a card and carries its picture — and on the
sites that list one, Chub and Wyvern, the line it is listed under — a
picture's link a picture alone the intake takes, and a link to a card
that is not there is nothing there. A pinned card its creator
deletes fails as "nothing is at …" — pin another of that site's, as
popular and as old as can be found."""

import pytest

from otaku import __version__
from otaku.backend.app.pictures.services import RawFile, read_picture
from otaku.backend.formats.cards import (
    CardError,
    is_charx,
    pack_charx,
    read_card,
    read_main_picture,
)
from otaku.integrations import Cards, CardSourceError

pytestmark = [pytest.mark.live, pytest.mark.integration]

USER_AGENT = f"otaku/{__version__}"

# One card per site, each a character's page as a reader copies it.
CARDS = {
    "chub": "https://chub.ai/characters/horcocks/yes-my-liege",
    "character tavern": "https://character-tavern.com/character/beliy/sylvia__potionomics",
    "risurealm": "https://realm.risuai.net/character/0a225d75-525c-4bd1-8fe8-20c97dab7dfa",
    "pygmalion": "https://pygmalion.chat/character/870b199e-4a8d-4871-9b5b-7a639741b25a",
    "wyvern": "https://app.wyvern.chat/characters/_yejexLP13MAPHTCEnQD7F",
    "a file's own link": (
        "https://raw.githubusercontent.com/enclavum/otaku/main/scenarios/fixtures/seraphina.png"
    ),
}
# A picture on each of Civitai's two sites: a picture alone, no card.
PICTURES = {
    "civitai": "https://civitai.com/images/12097475",
    "civitai red": "https://civitai.red/images/12097475",
}
# The sites that list a card under a line of their own, its tagline.
TAGLINES = ("chub", "wyvern")
# A RisuRealm card whose creator allows no PNG: it comes as a CHARX.
CHARX_ONLY = "https://realm.risuai.net/character/2012ab3a-0da1-41e5-8969-305996173e67"
# A page of each site's own shape that names no card.
MISSING = {
    "chub": "https://chub.ai/characters/nobody-zz/no-such-card-zz",
    "character tavern": "https://character-tavern.com/character/nobody_zz/no_such_card_zz",
    "risurealm": "https://realm.risuai.net/character/00000000-0000-4000-8000-000000000000",
    "pygmalion": "https://pygmalion.chat/character/00000000-0000-4000-8000-000000000000",
    "wyvern": "https://app.wyvern.chat/characters/_noSuchCharacterZZ",
    "civitai": "https://civitai.com/images/999999999",
}


class TestCards:
    @pytest.mark.parametrize("link", CARDS.values(), ids=CARDS.keys())
    def test_a_link_gives_a_card_and_its_picture(self, link: str) -> None:
        data = _card_file(link)
        card, _ = read_card(data)
        assert card.name
        assert read_main_picture(data) is not None

    @pytest.mark.parametrize("site", TAGLINES)
    def test_a_site_that_lists_a_card_under_a_line_gives_it(self, site: str) -> None:
        assert _cards().fetch(CARDS[site], user_agent=USER_AGENT).tagline.strip()

    def test_a_card_that_allows_no_png_comes_as_a_charx(self) -> None:
        data = _card_file(CHARX_ONLY)
        assert is_charx(data)
        card, _ = read_card(data)
        assert card.name
        assert read_main_picture(data) is not None

    @pytest.mark.parametrize("link", PICTURES.values(), ids=PICTURES.keys())
    def test_a_picture_link_gives_a_picture_alone(self, link: str) -> None:
        got = _cards().fetch(link, user_agent=USER_AGENT)
        assert got.picture is None
        with pytest.raises(CardError):
            read_card(got.data)
        assert read_picture(RawFile(got.data, got.file_name)).width

    @pytest.mark.parametrize("link", MISSING.values(), ids=MISSING.keys())
    def test_a_card_that_is_not_there_is_nothing_there(self, link: str) -> None:
        with pytest.raises(CardSourceError):
            _cards().fetch(link, user_agent=USER_AGENT)


def _cards() -> Cards:
    return Cards(Cards.default_section())


def _card_file(link: str) -> bytes:
    """The card file the link gives, as the codex imports it: a JSON card
    and the picture beside it packed into one CHARX
    (`codex.cards.services.fetch_card`)."""
    got = _cards().fetch(link, user_agent=USER_AGENT)
    return pack_charx(got.data, got.picture) if got.picture is not None else got.data

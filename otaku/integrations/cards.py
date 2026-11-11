"""Character cards on the sites that share them: a link to one turned
into the card's file, fetched. A site is known by its hosts and the
shape of a character's page, and gives the card its own way — Chub and
Character Tavern a PNG with the card in it, RisuRealm one too or a CHARX
where the card's creator allows no PNG, Pygmalion and Wyvern a JSON card
with its picture beside it — and Civitai a picture alone, which the codex
makes a blank card of. Any other link is the card's file itself, a
picture alone included. Chub and Wyvern also answer the line they show
a card under in their lists, its tagline. No site is named outside this
module, and what a refusal says is the sentence the reader is shown —
the link and its host are on the screen beside it."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import SplitResult, unquote, urlsplit

import httpx

from otaku.integrations.base import Integration, IntegrationError

# How long a site may stay silent — connecting, or between two parts of
# an answer; a large CHARX takes as long as it streams.
_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_RISU_ID = re.compile(r"[0-9a-f-]+", re.IGNORECASE)
# What a site answers for a page that is not there.
_GONE = (404, 410)
# What a refusal says, the reader's sentences.
_NOT_A_LINK = "Not a link."
_NOT_A_PAGE = "Not a character's page."
_NOTHING_THERE = "Nothing at this link."
_NOT_A_CARD = "Not a card."
_NO_ANSWER = "The site didn't answer."
_NO_CARD_ANSWERED = "The site answered no card."
_NOT_A_PICTURE_PAGE = "Not a picture's page."
_NOT_A_PICTURE = "Not a picture."
# A Civitai picture as otaku keeps one: its long edge, the intake's
# (`backend.app.pictures.services.WIRE_EDGE`), asked of Civitai's own
# resizing, so an original past the intake's size limit still comes in.
_CIVITAI_WIDTH = 1568


class CardSourceError(ValueError):
    """A source no card is fetched from: no web address, a site's page
    that is no character's or picture's, nothing there, a video where a
    picture was linked, or a web page where a file was."""


@dataclass(frozen=True)
class FetchedCard:
    """A card as a link gives it: the file — a PNG, JSON or CHARX card, or
    whatever a file's own link holds — the name it goes by, the card's
    picture where the site keeps it beside a JSON card (None otherwise),
    and the line the site shows the card under, as the site wrote it (""
    for none)."""

    data: bytes
    file_name: str
    picture: bytes | None = None
    tagline: str = ""


class Cards(Integration):
    """The card sites as an integration: no key and no section, each
    site's address fixed here."""

    id = "cards"
    label = "Cards"
    configurable = False
    max_bytes: ClassVar[int] = 256_000_000  # of one file; a CHARX holds every picture of its card

    @classmethod
    def sites(cls) -> dict[str, tuple[str, ...]]:
        """Every site a card's page is fetched from, by its name,
        alphabetically: its hosts, the main one first — what a link may
        name beside a card file's own link."""
        named = sorted(_SITES, key=lambda site: site[0].casefold())
        return {name: hosts for name, hosts, _ in named}

    def fetch(self, url: str, *, user_agent: str) -> FetchedCard:
        """The card at `url` — a site's character page, or a file's own
        link. Raises CardSourceError for a link no card is fetched from,
        IntegrationError for a site that could not be reached, refused,
        or answered no card."""
        link = url.strip()
        parts = urlsplit(link)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise CardSourceError(_NOT_A_LINK)
        fetch = _Fetch(link, user_agent, self.max_bytes)
        by_site = _FETCH_BY_HOST.get(parts.hostname.lower().removeprefix("www."))
        return by_site(fetch, parts) if by_site is not None else _fetch_direct(fetch)


@dataclass(frozen=True)
class _Reply:
    """What a GET answered: its status, its bytes, its content type, and
    the address it came from, redirects followed."""

    status: int
    data: bytes
    content_type: str
    url: str


@dataclass(frozen=True)
class _Fetch:
    """The one transport the sites share, for one `link`: a GET under
    otaku's name, its answer cut at `max_bytes`."""

    link: str
    user_agent: str
    max_bytes: int

    def get(self, url: str) -> _Reply:
        """The answer, whatever its status. Raises IntegrationError where
        the host cannot be reached or sends too much."""
        try:
            with httpx.stream(
                "GET",
                url,
                headers={"User-Agent": self.user_agent},
                timeout=_TIMEOUT,
                follow_redirects=True,
            ) as reply:
                declared = reply.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self.max_bytes:
                    raise IntegrationError(self._too_big())
                chunks: list[bytes] = []
                size = 0
                for chunk in reply.iter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise IntegrationError(self._too_big())
                    chunks.append(chunk)
                return _Reply(
                    reply.status_code,
                    b"".join(chunks),
                    reply.headers.get("content-type", ""),
                    str(reply.url),
                )
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            raise IntegrationError(_NO_ANSWER) from e

    def ok(self, url: str) -> _Reply:
        """The answer, refused where it is no success (`checked`)."""
        return self.checked(self.get(url))

    def checked(self, reply: _Reply) -> _Reply:
        """`reply`, refused where it is no success: CardSourceError where
        nothing is there — whichever address the site keeps the card at,
        it is the link that holds nothing — IntegrationError for any
        other."""
        if reply.status in _GONE:
            raise CardSourceError(_NOTHING_THERE)
        if not 200 <= reply.status < 300:
            raise IntegrationError(f"The site answered {reply.status}.")
        return reply

    def _too_big(self) -> str:
        return f"The file is over {self.max_bytes // 1_000_000} MB."


# ---------- the sites ----------


def _fetch_chub(fetch: _Fetch, parts: SplitResult) -> FetchedCard:
    """Chub: a character's page is `/characters/CREATOR/SLUG` (or
    `/CREATOR/SLUG`); its card is a PNG on the avatar host, its tagline
    the API's — asked after the card, and none where the API fails."""
    path = _segments(parts)
    if path[:1] == ["characters"]:
        path = path[1:]
    elif len(path) != 2:
        path = []
    if len(path) < 2:
        raise CardSourceError(_NOT_A_PAGE)
    creator, slug = path[:2]
    reply = fetch.ok(f"https://avatars.charhub.io/avatars/{creator}/{slug}/chara_card_v2.png")
    return FetchedCard(
        reply.data, f"{unquote(slug)}.png", tagline=_chub_tagline(fetch, creator, slug)
    )


def _chub_tagline(fetch: _Fetch, creator: str, slug: str) -> str:
    """The line Chub shows a card under; "" where the API cannot say —
    the card is there, and lacks nothing it needs."""
    try:
        reply = fetch.get(f"https://api.chub.ai/api/characters/{creator}/{slug}?full=false")
        node = json.loads(reply.data).get("node") if reply.status == 200 else None
    except (IntegrationError, ValueError, AttributeError):
        return ""
    tagline = node.get("tagline") if isinstance(node, dict) else None
    return tagline if isinstance(tagline, str) else ""


def _fetch_character_tavern(fetch: _Fetch, parts: SplitResult) -> FetchedCard:
    """Character Tavern: a character's page is `/character/AUTHOR/NAME`;
    its card is a PNG on the storage host."""
    path = _segments(parts)
    if len(path) < 3 or path[0] != "character":
        raise CardSourceError(_NOT_A_PAGE)
    author, name = path[1:3]
    reply = fetch.ok(f"https://ct-cards.storage.character-tavern.com/{author}/{name}.png")
    return FetchedCard(reply.data, f"{unquote(name)}.png")


def _fetch_risu(fetch: _Fetch, parts: SplitResult) -> FetchedCard:
    """RisuRealm: a character's page is `/character/ID`; its card a PNG,
    or a CHARX where its creator allows no PNG — which the realm answers
    with 403."""
    path = _segments(parts)
    if len(path) < 2 or path[0] != "character" or not _RISU_ID.fullmatch(path[1]):
        raise CardSourceError(_NOT_A_PAGE)
    card_id = path[1]
    base = "https://realm.risuai.net/api/v1/download"
    reply = fetch.get(f"{base}/png-v3/{card_id}?non_commercial=true")
    if reply.status == 403:
        reply = fetch.ok(f"{base}/charx-v3/{card_id}?non_commercial=true")
        return FetchedCard(reply.data, f"{card_id}.charx")
    return FetchedCard(fetch.checked(reply).data, f"{card_id}.png")


def _fetch_pygmalion(fetch: _Fetch, parts: SplitResult) -> FetchedCard:
    """Pygmalion: a character's page names its id, a UUID; its card is the
    export's JSON, its picture at the address the card names."""
    found = _UUID.search(parts.path)
    if found is None:
        raise CardSourceError(_NOT_A_PAGE)
    character_id = found.group(0)
    reply = fetch.ok(f"https://server.pygmalion.chat/api/export/character/{character_id}/v2")
    card = _json_object(reply).get("character")
    if not isinstance(card, dict):
        raise IntegrationError(_NO_CARD_ANSWERED)
    data = card.get("data")
    avatar = data.get("avatar") if isinstance(data, dict) else None
    return FetchedCard(json.dumps(card).encode(), f"{character_id}.json", _picture(fetch, avatar))


# A Wyvern character's fields as a V2 card's, by the card's name.
_WYVERN_FIELDS = {
    "name": "name",
    "description": "description",
    "personality": "personality",
    "scenario": "scenario",
    "first_mes": "first_mes",
    "mes_example": "mes_example",
    "creator_notes": "creator_notes",
    "system_prompt": "pre_history_instructions",
    "post_history_instructions": "post_history_instructions",
    "alternate_greetings": "alternate_greetings",
}


def _fetch_wyvern(fetch: _Fetch, parts: SplitResult) -> FetchedCard:
    """Wyvern: a character's page is `/characters/ID`; the API answers the
    character in fields of its own, made a V2 card here, its picture at
    the address it names, and its tagline."""
    path = _segments(parts)
    if len(path) < 2 or path[0] != "characters":
        raise CardSourceError(_NOT_A_PAGE)
    character_id = path[1]
    record = _json_object(fetch.ok(f"https://api.wyvern.chat/characters/{character_id}"))
    card = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {field: record[key] for field, key in _WYVERN_FIELDS.items() if key in record},
    }
    tagline = record.get("tagline")
    return FetchedCard(
        json.dumps(card).encode(),
        f"{unquote(character_id)}.json",
        _picture(fetch, record.get("avatar")),
        tagline if isinstance(tagline, str) else "",
    )


def _fetch_civitai(fetch: _Fetch, parts: SplitResult) -> FetchedCard:
    """Civitai, either of its two sites: a picture's page is `/images/ID`;
    the site's API answers where the picture is — every rating asked for,
    so none is hidden — and the picture is fetched resized, a JPEG at
    otaku's own width where it is wider. A video is refused: it is no
    picture."""
    path = _segments(parts)
    if len(path) < 2 or path[0] != "images" or not path[1].isdigit():
        raise CardSourceError(_NOT_A_PICTURE_PAGE)
    picture_id = path[1]
    reply = fetch.ok(f"https://{parts.hostname}/api/v1/images?imageId={picture_id}&nsfw=X")
    items = _json_object(reply).get("items")
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if item is None:
        raise CardSourceError(_NOTHING_THERE)
    url, width = item.get("url"), item.get("width")
    if item.get("type") != "image" or not isinstance(url, str):
        raise CardSourceError(_NOT_A_PICTURE)
    if isinstance(width, int) and width > _CIVITAI_WIDTH:
        url = url.replace("/original=true/", f"/width={_CIVITAI_WIDTH}/")
    picture = fetch.ok(url)
    return FetchedCard(picture.data, f"civitai-{picture_id}")


def _fetch_direct(fetch: _Fetch) -> FetchedCard:
    """A file's own link: the file, named after the link's last part —
    refused where the link is a web page."""
    reply = fetch.ok(fetch.link)
    if reply.content_type.split(";")[0].strip().lower() == "text/html":
        raise CardSourceError(_NOT_A_CARD)
    final = urlsplit(reply.url)
    return FetchedCard(reply.data, unquote(final.path.rpartition("/")[2]) or final.hostname or "")


# The sites a card's page is fetched from: each its name, its hosts — the
# main one first — and its fetch. Any other host's link is the file's own
# (`_fetch_direct`).
_SITES: tuple[tuple[str, tuple[str, ...], Callable[[_Fetch, SplitResult], FetchedCard]], ...] = (
    ("Character Tavern", ("character-tavern.com",), _fetch_character_tavern),
    ("Chub", ("chub.ai", "characterhub.org", "venus.chub.ai"), _fetch_chub),
    ("Civitai", ("civitai.com", "civitai.red"), _fetch_civitai),
    ("Pygmalion", ("pygmalion.chat",), _fetch_pygmalion),
    ("RisuRealm", ("realm.risuai.net",), _fetch_risu),
    ("Wyvern", ("wyvern.chat", "app.wyvern.chat"), _fetch_wyvern),
)
# A site's fetch, by the host its links name.
_FETCH_BY_HOST = {host: fetch for _, hosts, fetch in _SITES for host in hosts}


# ---------- helpers ----------


def _segments(parts: SplitResult) -> list[str]:
    """A link's path, its empty parts left out — as written, so a part
    goes into another address unchanged."""
    return [segment for segment in parts.path.split("/") if segment]


def _json_object(reply: _Reply) -> dict[str, object]:
    try:
        payload = json.loads(reply.data)
    except ValueError as e:
        raise IntegrationError(_NO_CARD_ANSWERED) from e
    if not isinstance(payload, dict):
        raise IntegrationError(_NO_CARD_ANSWERED)
    return payload


def _picture(fetch: _Fetch, url: object) -> bytes | None:
    """The picture at the address a card names; None for none, and for one
    that cannot be fetched — the card is the card without it."""
    if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
        return None
    try:
        return fetch.ok(url).data
    except (CardSourceError, IntegrationError):
        return None

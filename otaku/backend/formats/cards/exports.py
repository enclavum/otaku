"""A card written as a file of a kind: the file it was imported from,
its text patched where otaku's fields differ — or, with none, the text
written whole — in a PNG beside its picture, a CHARX beside its pictures,
or alone as JSON."""

import json
from typing import Literal

from otaku.backend.formats.cards import _charx, _json, _png
from otaku.backend.formats.cards._charx import Asset, is_charx
from otaku.context.cards import CharacterProfile

# What a card file is: a card is read from and written as each of them.
CardFileKind = Literal["png", "json", "charx"]


def write_card(
    written: CardFileKind,
    profile: CharacterProfile,
    line: str,
    *,
    original: bytes | None,
    pictures: list[Asset],
    keep_pictures: bool,
) -> bytes:
    """A card as a file of the `written` kind: the text of the file it was
    imported from (`original`, None for none) with `profile` and the
    tagline `line` laid over it where they differ — every copy a PNG
    holds — or, with no original, the text written whole. A PNG carries
    the first of `pictures`, a PNG's bytes, the card in both its V2 and
    its V3 chunk; a CHARX is the original as it came, its text replaced,
    where it is one and `keep_pictures` says its pictures stand, else the
    text and `pictures`; a JSON is the text alone, in its own spec where
    the original is one, else in V2's. A card nothing was changed in, of
    the kind it came as, its pictures standing, is its original as it
    came."""
    texts = _texts(original) if original is not None else [("", _json.write_text(profile, line))]
    laid = [(keyword, _json.overlay(text, profile, line)) for keyword, text in texts]
    standing = written == "json" or keep_pictures or (pictures and pictures[0].data == original)
    if original is not None and laid == texts and written == _kind(original) and standing:
        return original
    first = next((text for keyword, text in laid if keyword == "chara"), laid[0][1])
    if written == "json":
        own = original is not None and not _png.is_png(original) and not is_charx(original)
        return json.dumps(first if own else _json.as_v2(first)).encode()
    if written == "png":
        if original is not None and _png.is_png(original):
            keywords = {keyword for keyword, _ in laid}
            cards = [*laid]
            if "chara" not in keywords:
                cards.insert(0, ("chara", _json.as_v2(first)))
            if "ccv3" not in keywords:
                cards.append(("ccv3", _json.as_v3(first)))
        else:
            cards = [("chara", _json.as_v2(first)), ("ccv3", _json.as_v3(first))]
        return _png.write_cards(pictures[0].data, cards)
    if original is not None and is_charx(original) and keep_pictures:
        return _charx.replace_text(original, _json.as_v3(laid[0][1]))
    return _charx.write_charx(_json.as_v3(first), pictures)


def _kind(data: bytes) -> CardFileKind:
    """What kind of card file `data` is."""
    if _png.is_png(data):
        return "png"
    return "charx" if is_charx(data) else "json"


def _texts(original: bytes) -> list[tuple[str, dict[str, object]]]:
    """Every copy of the card's text a file holds, keyword-tagged."""
    if _png.is_png(original):
        return _png.read_cards(original)
    if is_charx(original):
        return [("charx", _charx.read_text(_charx.open_archive(original)))]
    return [("json", _json.read_text(original))]

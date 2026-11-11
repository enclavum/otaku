"""A card written as a file of a kind (`write_card`): the text of the file
it was imported from with otaku's fields and tagline laid over it where
they differ — every other word, key and extension as it came — or, with
no file, the text written whole as V2. A PNG carries the card in both
its V2 and its V3 chunk on the picture given, every other chunk kept; a
CHARX is its file with its text replaced while its pictures stand, else
built from the text and the pictures given, each listed with its type;
a JSON is the text alone, in its own spec, else V2's. The tagline rides
under `extensions.otaku`; the reminder is SillyTavern's depth prompt,
its depth the card's own until otaku's changes. A card nothing was
changed in, of the kind it came as, is its file as it came. Every file
is built in memory."""

import base64
import io
import json
import struct
import zipfile
from dataclasses import replace

from otaku.backend.formats.cards import (
    Asset,
    read_assets,
    read_card,
    read_main_picture,
    write_card,
)
from otaku.context.cards import CharacterProfile

_PICTURE = b"\x89PNG\r\n\x1a\n"  # completed by `_png`
_JOY = b"\x89PNG joy"
_INN = b"\x89PNG inn"
# A card's depth prompt, its depth SillyTavern's count of every message.
_DEPTH_PROMPT = {"prompt": "Be kind.", "depth": 5, "role": "user"}


class TestNothingChanged:
    def test_a_json_card_is_its_file_as_it_came(self) -> None:
        data = _json(_v3(name="Elara", description="  A warden.\r\n", creator="x"))
        profile, _ = read_card(data)
        assert _write("json", profile, original=data) == data

    def test_a_png_card_on_its_own_picture_is_its_file_as_it_came(self) -> None:
        data = _png(_text_chunk(b"chara", _v2(name="Elara")))
        profile, _ = read_card(data)
        assert _write("png", profile, original=data, pictures=[_icon(data)]) == data

    def test_a_charx_whose_pictures_stand_is_its_file_as_it_came(self) -> None:
        data = _charx(_v3(name="Elara"), {"assets/icon/main.png": _PICTURE})
        profile, _ = read_card(data)
        assert _write("charx", profile, original=data, keep_pictures=True) == data


class TestJson:
    def test_a_changed_field_is_written_and_every_other_keeps_its_exact_words(self) -> None:
        text = _v2(
            name="Elara",
            description="  A warden.\r\n",
            creator="someone",
            extensions={"custom": {"kept": True}},
        )
        profile, _ = read_card(_json(text))
        out = _read_json(
            _write("json", replace(profile, personality="Stern."), original=_json(text))
        )
        assert out["data"]["personality"] == "Stern."
        assert out["data"]["description"] == "  A warden.\r\n"
        assert out["data"]["creator"] == "someone"
        assert out["data"]["extensions"]["custom"] == {"kept": True}

    def test_a_card_keeps_its_own_spec(self) -> None:
        data = _json(_v3(name="Elara"))
        profile, _ = read_card(data)
        out = _read_json(_write("json", replace(profile, scenario="A chapel."), original=data))
        assert out["spec"] == "chara_card_v3"

    def test_a_card_from_another_kind_of_file_is_written_as_v2(self) -> None:
        data = _png(_text_chunk(b"ccv3", _v3(name="Elara")))
        profile, _ = read_card(data)
        assert _read_json(_write("json", profile, original=data))["spec"] == "chara_card_v2"

    def test_a_card_without_a_file_is_written_whole_as_v2_and_reads_back(self) -> None:
        profile = CharacterProfile(
            name="Mira",
            description="A thief.",
            greeting="Hands where I see them.",
            alternate_greetings=("Again?",),
            examples=("Mira: Mine.", "Mira: Yours, then."),
            reminder="Stay terse.",
            reminder_depth=3,
        )
        out = _write("json", profile, line="Steals.", original=None)
        assert _read_json(out)["spec"] == "chara_card_v2"
        read, _ = read_card(out)
        assert read == profile

    def test_the_tagline_rides_under_otakus_own_extension(self) -> None:
        data = _json(_v2(name="Elara"))
        profile, _ = read_card(data)
        out = _read_json(_write("json", profile, line="Keeps the forest.", original=data))
        assert out["data"]["extensions"]["otaku"] == {"tagline": "Keeps the forest."}

    def test_a_tagline_emptied_leaves_no_trace(self) -> None:
        data = _json(_v2(name="Elara", extensions={"otaku": {"tagline": "Old."}}))
        profile, _ = read_card(data)
        out = _read_json(_write("json", profile, line="", original=data))
        assert "otaku" not in out["data"]["extensions"]


class TestTheReminder:
    def test_a_changed_text_keeps_the_cards_own_depth_and_role(self) -> None:
        data = _json(_v2(name="Elara", extensions={"depth_prompt": _DEPTH_PROMPT}))
        profile, _ = read_card(data)
        out = _read_json(_write("json", replace(profile, reminder="Be kinder."), original=data))
        assert out["data"]["extensions"]["depth_prompt"] == {
            "prompt": "Be kinder.",
            "depth": 5,
            "role": "user",
        }

    def test_a_changed_depth_is_sillytaverns_count_of_every_message(self) -> None:
        data = _json(_v2(name="Elara", extensions={"depth_prompt": _DEPTH_PROMPT}))
        profile, _ = read_card(data)
        out = _read_json(_write("json", replace(profile, reminder_depth=2), original=data))
        prompt = out["data"]["extensions"]["depth_prompt"]
        assert (prompt["depth"], prompt["role"]) == (4, "user")


class TestPng:
    def test_a_card_rides_its_picture_in_both_its_v2_and_its_v3_chunk(self) -> None:
        picture = _png(_chunk(b"IDAT", b"pixels"))
        profile = CharacterProfile(name="Mira", description="A thief.")
        out = _write("png", profile, original=None, pictures=[_icon(picture)])
        cards = _cards_in(out)
        assert [(keyword, card["spec"]) for keyword, card in cards] == [
            ("chara", "chara_card_v2"),
            ("ccv3", "chara_card_v3"),
        ]
        # every chunk the picture had is kept, in its order
        assert _chunk(b"IDAT", b"pixels") in out
        assert read_card(out)[0] == profile

    def test_every_copy_the_file_holds_is_changed_and_the_missing_one_added(self) -> None:
        data = _png(_text_chunk(b"chara", _v2(name="Elara", personality="Warm.")))
        profile, _ = read_card(data)
        out = _write(
            "png", replace(profile, personality="Stern."), original=data, pictures=[_icon(data)]
        )
        assert [(keyword, card["data"]["personality"]) for keyword, card in _cards_in(out)] == [
            ("chara", "Stern."),
            ("ccv3", "Stern."),
        ]


class TestCharx:
    def test_a_charx_whose_pictures_stand_is_its_file_with_its_text_replaced(self) -> None:
        members = {"assets/icon/main.png": _PICTURE, "assets/other/song.mp3": b"ID3"}
        data = _charx(_v3(name="Elara", personality="Warm."), members)
        profile, _ = read_card(data)
        out = _write(
            "charx", replace(profile, personality="Stern."), original=data, keep_pictures=True
        )
        written = _members(out)
        assert set(written) == {"card.json", *members}
        assert all(written[path] == members[path] for path in members)
        card = json.loads(written["card.json"])
        assert (card["spec"], card["data"]["personality"]) == ("chara_card_v3", "Stern.")

    def test_otherwise_it_is_built_from_the_text_and_the_pictures_given(self) -> None:
        profile = CharacterProfile(name="Mira")
        pictures = [
            _icon(_PICTURE),
            Asset("emotion", "joy", _JOY),
            Asset("background", "inn", _INN),
        ]
        out = _write("charx", profile, original=None, pictures=pictures)
        listed = json.loads(_members(out)["card.json"])["data"]["assets"]
        assert [(asset["type"], asset["name"]) for asset in listed] == [
            ("icon", "main"),
            ("emotion", "joy"),
            ("background", "inn"),
        ]
        assert read_main_picture(out) == _PICTURE
        assert [(a.type, a.name, a.data) for a in read_assets(out)] == [
            ("emotion", "joy", _JOY),
            ("background", "inn", _INN),
        ]
        assert read_card(out)[0] == profile


def _write(
    written: str,
    profile: CharacterProfile,
    *,
    line: str = "",
    original: bytes | None,
    pictures: list[Asset] | None = None,
    keep_pictures: bool = False,
) -> bytes:
    return write_card(
        written,  # type: ignore[arg-type]
        profile,
        line,
        original=original,
        pictures=pictures or [],
        keep_pictures=keep_pictures,
    )


def _icon(data: bytes) -> Asset:
    return Asset("icon", "", data)


def _v2(**data: object) -> dict:
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}


def _v3(**data: object) -> dict:
    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": data}


def _json(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def _read_json(data: bytes) -> dict:
    found = json.loads(data)
    assert isinstance(found, dict)
    return found


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + b"\x00\x00\x00\x00"


def _text_chunk(keyword: bytes, payload: dict) -> bytes:
    return _chunk(b"tEXt", keyword + b"\x00" + base64.b64encode(json.dumps(payload).encode()))


def _png(*chunks: bytes) -> bytes:
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    return _PICTURE + ihdr + b"".join(chunks) + _chunk(b"IEND", b"")


def _cards_in(png: bytes) -> list[tuple[str, dict]]:
    """Every card a PNG's text chunks carry, keyword first, in file order."""
    found = []
    at = len(_PICTURE)
    while at + 8 <= len(png):
        (length,) = struct.unpack(">I", png[at : at + 4])
        ctype, body = png[at + 4 : at + 8], png[at + 8 : at + 8 + length]
        at += 12 + length
        if ctype == b"tEXt":
            keyword, _, text = body.partition(b"\x00")
            found.append((keyword.decode(), json.loads(base64.b64decode(text))))
    return found


def _charx(payload: dict, members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("card.json", _json(payload))
        for path, data in members.items():
            archive.writestr(path, data)
    return out.getvalue()


def _members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}

"""A card in a CHARX, the V3 spec's zip: its `card.json` read as any
card's text, its main icon the card's picture — the icon named `main`,
else the first the card lists, else the first in its icon folder — its
other pictures each with the type and name the card gives it, in the
card's order, and what is no picture said left behind; a JSON card
packed with its picture into one (`pack_charx`) reads back as both. A
member that would inflate past any card's size is refused before it is
read. Every archive is built in memory."""

import io
import json
import zipfile

import pytest

from otaku.backend.formats.cards import (
    CardError,
    is_charx,
    pack_charx,
    read_assets,
    read_card,
    read_main_picture,
)

_ICON = b"\x89PNG icon"
_OTHER = b"\x89PNG other"
_JOY = b"\x89PNG joy"
_SONG = {"type": "x-risu-asset", "name": "song", "uri": "embeded://s.mp3", "ext": "mp3"}


class TestPackCharx:
    def test_a_packed_card_reads_back_as_its_card_and_its_picture(self) -> None:
        packed = pack_charx(_json(_v3(name="Ann")), _ICON)
        assert is_charx(packed)
        card, notes = read_card(packed)
        assert (card.name, notes) == ("Ann", [])
        assert read_main_picture(packed) == _ICON


class TestTheMainIcon:
    def test_the_icon_named_main_is_the_picture(self) -> None:
        assets = [_asset("icon", "side", "a.png"), _asset("icon", "main", "b.png")]
        archive = _charx(_v3(name="Ann", assets=assets), {"a.png": _OTHER, "b.png": _ICON})
        assert read_main_picture(archive) == _ICON

    def test_else_the_first_icon_the_card_lists(self) -> None:
        assets = [_asset("icon", "one", "a.png"), _asset("icon", "two", "b.png")]
        archive = _charx(_v3(name="Ann", assets=assets), {"a.png": _ICON, "b.png": _OTHER})
        assert read_main_picture(archive) == _ICON

    def test_else_the_first_file_in_the_icon_folder(self) -> None:
        archive = _charx(_v3(name="Ann"), {"assets/icon/any.png": _ICON})
        assert read_main_picture(archive) == _ICON

    def test_a_charx_with_no_icon_has_no_picture(self) -> None:
        assert read_main_picture(_charx(_v3(name="Ann"), {})) is None


class TestTheOtherPictures:
    def test_each_beside_the_icon_comes_with_its_type_and_name_in_the_cards_order(self) -> None:
        assets = [
            _asset("icon", "main", "i.png"),
            _asset("background", "inn", "b.png"),
            _asset("emotion", "joy", "j.png"),
        ]
        members = {"i.png": _ICON, "b.png": _OTHER, "j.png": _JOY}
        archive = _charx(_v3(name="Ann", assets=assets), members)
        assert [(a.type, a.name, a.data) for a in read_assets(archive)] == [
            ("background", "inn", _OTHER),
            ("emotion", "joy", _JOY),
        ]

    def test_a_file_that_is_no_picture_is_none_of_them(self) -> None:
        archive = _charx(_v3(name="Ann", assets=[_SONG]), {"s.mp3": b"ID3"})
        assert read_assets(archive) == []

    def test_a_json_card_has_none(self) -> None:
        assert read_assets(_json(_v3(name="Ann"))) == []


class TestWhatIsLeftBehind:
    def test_a_file_beside_the_pictures_that_is_no_picture_is_said_out_loud(self) -> None:
        assets = [_asset("icon", "main", "i.png"), _asset("emotion", "joy", "j.png"), _SONG]
        members = {"i.png": _ICON, "j.png": _JOY, "s.mp3": b"ID3"}
        _, notes = read_card(_charx(_v3(name="Ann", assets=assets), members))
        assert len(notes) == 1

    def test_pictures_alone_leave_nothing_behind(self) -> None:
        assets = [_asset("icon", "main", "i.png"), _asset("emotion", "joy", "j.png")]
        members = {"i.png": _ICON, "j.png": _JOY}
        _, notes = read_card(_charx(_v3(name="Ann", assets=assets), members))
        assert notes == []


class TestRefusals:
    def test_a_zip_without_its_card_is_refused(self) -> None:
        with pytest.raises(CardError):
            read_card(_zip({"assets/icon/main.png": _ICON}))

    def test_a_zip_that_will_not_open_is_refused(self) -> None:
        with pytest.raises(CardError):
            read_card(b"PK\x03\x04 not a zip at all")

    def test_a_member_inflating_past_any_cards_size_is_refused(self) -> None:
        # deflated, the zeros take a few kilobytes; inflated, past the guard
        with pytest.raises(CardError):
            read_card(_zip({"card.json": b"\x00" * 65_000_000}))


def _v3(**data: object) -> dict:
    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": data}


def _json(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def _asset(kind: str, name: str, path: str) -> dict:
    return {"type": kind, "name": name, "uri": f"embeded://{path}", "ext": "png"}


def _charx(payload: dict, members: dict[str, bytes]) -> bytes:
    return _zip({"card.json": _json(payload), **members})


def _zip(members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in members.items():
            archive.writestr(path, data)
    return out.getvalue()

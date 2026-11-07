"""A card in a PNG beside the files RisuAI keeps there: a text chunk
each (`chara-ext-asset_:N`, the file in base64), which the card's text
names `__asset:N` — a V3 card's assets, else a V2 card's own RisuAI
listing. Those that are pictures are the card's other pictures, each with
its type and name, in the card's order, each once; the PNG's own picture
(`ccdefault:`) is the main one; the rest are said left behind. Every
file is built in memory."""

import base64
import json
import struct

from otaku.backend.formats.cards import read_assets, read_card

_JOY = b"\x89PNG joy"
_INN = b"\xff\xd8\xff\xe0 inn"
_SONG = b"ID3 a song"


class TestTheChunksAV3CardNames:
    def test_each_picture_comes_with_its_type_and_name_in_the_cards_order(self) -> None:
        assets = [
            _asset("icon", "main", "ccdefault:"),
            _asset("background", "inn", "__asset:2"),
            _asset("emotion", "joy", "__asset:1"),
        ]
        png = _png(_card(b"ccv3", _v3(name="Ann", assets=assets)), _file(1, _JOY), _file(2, _INN))
        assert [(a.type, a.name, a.data) for a in read_assets(png)] == [
            ("background", "inn", _INN),
            ("emotion", "joy", _JOY),
        ]

    def test_a_chunk_named_without_its_colon_is_read_too(self) -> None:
        assets = [_asset("emotion", "joy", "__asset:1")]
        legacy = _text(b"chara-ext-asset_1", _JOY)
        png = _png(_card(b"ccv3", _v3(name="Ann", assets=assets)), legacy)
        assert [a.name for a in read_assets(png)] == ["joy"]

    def test_a_file_named_twice_or_never_kept_comes_once_or_not_at_all(self) -> None:
        assets = [
            _asset("emotion", "joy", "__asset:1"),
            _asset("emotion", "again", "__asset:1"),
            _asset("emotion", "gone", "__asset:9"),
        ]
        png = _png(_card(b"ccv3", _v3(name="Ann", assets=assets)), _file(1, _JOY))
        assert [a.name for a in read_assets(png)] == ["joy"]


class TestTheChunksAV2CardLists:
    def test_risuais_own_listing_names_its_emotions_and_its_other_assets(self) -> None:
        risu = {
            "emotions": [["joy", "__asset:1"]],
            "additionalAssets": [["inn", "__asset:2", "inn.jpg"]],
        }
        card = _v2(name="Ann", extensions={"risuai": risu})
        png = _png(_card(b"chara", card), _file(1, _JOY), _file(2, _INN))
        assert [(a.type, a.name) for a in read_assets(png)] == [
            ("emotion", "joy"),
            ("x-risu-asset", "inn"),
        ]

    def test_a_card_without_a_listing_has_no_other_pictures(self) -> None:
        png = _png(_card(b"chara", _v2(name="Ann")), _file(1, _JOY))
        assert read_assets(png) == []


class TestWhatIsLeftBehind:
    def test_a_file_that_is_no_picture_is_none_of_them_and_said_out_loud(self) -> None:
        assets = [
            _asset("emotion", "joy", "__asset:1"),
            _asset("x-risu-asset", "song", "__asset:2"),
        ]
        png = _png(_card(b"ccv3", _v3(name="Ann", assets=assets)), _file(1, _JOY), _file(2, _SONG))
        assert [a.name for a in read_assets(png)] == ["joy"]
        _, notes = read_card(png)
        assert len(notes) == 1

    def test_pictures_alone_leave_nothing_behind(self) -> None:
        assets = [_asset("emotion", "joy", "__asset:1")]
        png = _png(_card(b"ccv3", _v3(name="Ann", assets=assets)), _file(1, _JOY))
        assert read_card(png)[1] == []


def _v2(**data: object) -> dict:
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}


def _v3(**data: object) -> dict:
    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": data}


def _asset(kind: str, name: str, uri: str) -> dict:
    return {"type": kind, "name": name, "uri": uri, "ext": "png"}


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + b"\x00\x00\x00\x00"


def _text(keyword: bytes, data: bytes) -> bytes:
    return _chunk(b"tEXt", keyword + b"\x00" + base64.b64encode(data))


def _card(keyword: bytes, payload: dict) -> bytes:
    return _text(keyword, json.dumps(payload).encode())


def _file(n: int, data: bytes) -> bytes:
    return _text(b"chara-ext-asset_:" + str(n).encode(), data)


def _png(*chunks: bytes) -> bytes:
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"".join(chunks) + _chunk(b"IEND", b"")

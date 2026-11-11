"""A card file prepared for the codex, nothing stored
(`prepare_card_file`): its card read, its main picture and a CHARX's
others passed through the one intake, each other with the asset it came
as; a picture the intake refuses left out — the main one with the
intake's sentence, the others counted; a picture alone, where one is
allowed, read as the blank card it makes; a file that is no card, nor
such a picture, refused. Every file is built in memory."""

import io
import json
import zipfile

import pytest
from PIL import Image

from otaku.backend.codex.cards.services import prepare_card_file
from otaku.backend.errors import Refused


class TestPrepareCardFile:
    def test_a_charx_brings_its_card_its_main_picture_and_its_others(self) -> None:
        assets = [
            _asset("icon", "main", "assets/icon/main.png"),
            _asset("emotion", "joy", "assets/emotion/joy.png"),
            _asset("background", "inn", "assets/bg/inn.png"),
        ]
        members = {
            "assets/icon/main.png": _png((200, 0, 0)),
            "assets/emotion/joy.png": _png((0, 200, 0)),
            "assets/bg/inn.png": _png((90, 90, 90)),
        }
        prepared = prepare_card_file(_charx(_v3(name="Ann", assets=assets), members), "ann.charx")
        assert prepared.profile is not None and prepared.profile.name == "Ann"
        assert prepared.main is not None and prepared.main.media_type == "image/png"
        assert [(asset.type, asset.name) for asset, _ in prepared.others] == [
            ("emotion", "joy"),
            ("background", "inn"),
        ]
        assert (prepared.refused, prepared.main_refused, prepared.bare) == (0, "", None)

    def test_a_picture_the_intake_refuses_is_left_out(self) -> None:
        assets = [
            _asset("icon", "main", "assets/icon/main.png"),
            _asset("emotion", "joy", "assets/emotion/joy.png"),
        ]
        broken = b"\x89PNG not a picture at all"
        members = {"assets/icon/main.png": broken, "assets/emotion/joy.png": broken}
        prepared = prepare_card_file(_charx(_v3(name="Ann", assets=assets), members), "ann.charx")
        assert prepared.main is None and prepared.main_refused
        assert (prepared.others, prepared.refused) == ((), 1)

    def test_a_picture_alone_is_the_blank_card_it_makes_where_one_is_allowed(self) -> None:
        prepared = prepare_card_file(_png((10, 20, 30)), "cat.png", allow_bare_picture=True)
        assert prepared.bare is not None and prepared.profile is None
        with pytest.raises(Refused):
            prepare_card_file(_png((10, 20, 30)), "cat.png")

    def test_a_file_that_is_no_card_is_refused(self) -> None:
        with pytest.raises(Refused):
            prepare_card_file(b"hello there", "notes.txt")


def _v3(**data: object) -> dict:
    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": data}


def _asset(kind: str, name: str, path: str) -> dict:
    return {"type": kind, "name": name, "uri": f"embeded://{path}", "ext": "png"}


def _charx(payload: dict, members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("card.json", json.dumps(payload))
        for path, data in members.items():
            archive.writestr(path, data)
    return out.getvalue()


def _png(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(out, "PNG")
    return out.getvalue()

"""A card in a CHARX: the V3 spec's zip of `card.json` and its assets.
On import, its main icon is the card's picture, its other pictures each
come with the type and name the card gives them, and what is no picture
is left behind; every member is read through one guard (`read_member`),
which refuses one that would inflate past any card's size before it is
read. On export, a CHARX is the one it came as with its text replaced
(`replace_text`), or a card's text and its pictures, each listed as an
asset of its type (`write_charx`); and, for an import, a JSON card
packed with its picture into one (`pack_charx`), so a card a site keeps
beside its picture reads as one file."""

import io
import json
import zipfile
import zlib
from dataclasses import dataclass

from otaku.backend.formats.cards import _json
from otaku.backend.formats.cards._json import CardError, list_assets

_MAGIC = b"PK\x03\x04"
# The card's file and the folder its icons stand in; an asset's uri names
# its file in the zip after the spec's own (misspelt) scheme.
_CARD = "card.json"
_ICONS = "assets/icon/"
_EMBEDDED = ("embeded://", "embedded://")
# A member inflated past this is no card's: what a zip bomb would be.
_MAX_MEMBER = 64_000_000
# A packed picture's extension, by its first bytes.
_PICTURE_EXTENSIONS = ((b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpeg"), (b"GIF8", "gif"))
# The extensions an asset declares that name a picture.
_PICTURES = {"png", "jpg", "jpeg", "webp", "gif", "avif", "heic", "heif"}


@dataclass(frozen=True)
class Asset:
    """One of a card's pictures beside its main icon: the type the card
    gives it (`icon`, `emotion`, `background`, … as the card wrote it),
    its name there ("" for none), and its bytes."""

    type: str
    name: str
    data: bytes


# ---------- import ----------


def is_charx(data: bytes) -> bool:
    """Whether a file is a CHARX — a zip, which only its `card.json`
    makes a card."""
    return data.startswith(_MAGIC)


def open_archive(data: bytes) -> zipfile.ZipFile:
    """The zip, refused where it will not open."""
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise CardError(f"not a readable CHARX ({e})") from e


def read_text(archive: zipfile.ZipFile) -> dict[str, object]:
    """The card's text, its `card.json`."""
    try:
        return _json.read_text(read_member(archive, _CARD))
    except CardError as e:
        raise CardError(f"the CHARX's {_CARD} is no card ({e})") from e


def read_member(archive: zipfile.ZipFile, path: str) -> bytes:
    """One file of the zip, refused past `_MAX_MEMBER` as its header
    declares it — the reader stops there whatever the stream holds."""
    try:
        info = archive.getinfo(path)
    except KeyError:
        raise CardError(f"the CHARX holds no {path}") from None
    if info.file_size > _MAX_MEMBER:
        raise CardError(f"the CHARX's {path} is too large")
    try:
        return archive.read(info)
    # An encrypted member raises RuntimeError, an unknown compression
    # NotImplementedError.
    except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError) as e:
        raise CardError(f"the CHARX's {path} will not read ({e})") from e


def icon_path(archive: zipfile.ZipFile, payload: dict[str, object]) -> str | None:
    """The main icon's file: the icon asset named `main`, else the first
    the card lists, else the first file in the icon folder; None for
    none."""
    icons = [
        asset
        for asset in list_assets(payload)
        if isinstance(asset, dict) and asset.get("type") == "icon"
    ]
    icons.sort(key=lambda asset: asset.get("name") != "main")  # stable: `main` first
    names = archive.namelist()
    listed = (path for asset in icons if (path := _asset_path(asset)) in names)
    folder = (name for name in names if name.startswith(_ICONS) and not name.endswith("/"))
    return next(listed, None) or next(folder, None)


def read_pictures(archive: zipfile.ZipFile, payload: dict[str, object]) -> list[Asset]:
    """Every picture the card lists in its zip but its main icon, in the
    card's order, each once: those whose extension names a picture, a
    member that will not read left out."""
    icon = icon_path(archive, payload)
    out: list[Asset] = []
    seen = {icon}
    for asset in list_assets(payload):
        path = _asset_path(asset)
        if path is None or path in seen or not _is_picture(asset):
            continue
        seen.add(path)
        try:
            data = read_member(archive, path)
        except CardError:
            continue
        kind, name = _field(asset, "type"), _field(asset, "name")
        out.append(Asset(kind, name, data))
    return out


def count_left_behind(archive: zipfile.ZipFile, payload: dict[str, object]) -> int:
    """How many of the files the card lists in its zip are no picture —
    what an import leaves behind."""
    return sum(
        1
        for asset in list_assets(payload)
        if _asset_path(asset) is not None and not _is_picture(asset)
    )


def _is_picture(asset: object) -> bool:
    """Whether an asset names a picture: by the extension it declares,
    else by its file's."""
    ext = _field(asset, "ext") or (_asset_path(asset) or "").rpartition(".")[2]
    return ext.lower().lstrip(".") in _PICTURES


def _field(asset: object, key: str) -> str:
    value = asset.get(key) if isinstance(asset, dict) else None
    return value if isinstance(value, str) else ""


def _asset_path(asset: object) -> str | None:
    """An asset's file in the zip, from its uri; None for one kept
    elsewhere."""
    uri = asset.get("uri") if isinstance(asset, dict) else None
    if not isinstance(uri, str):
        return None
    return next((uri[len(s) :] for s in _EMBEDDED if uri.startswith(s)), None)


# ---------- export ----------


def pack_charx(card: bytes, picture: bytes) -> bytes:
    """A JSON card and its picture as one CHARX: the card as it came, its
    `card.json`, and the picture in the icon folder — where `icon_path`
    finds it when the card lists no icon."""
    extension = _extension(picture)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_CARD, card)
        archive.writestr(f"{_ICONS}main.{extension}", picture)
    return out.getvalue()


def replace_text(data: bytes, payload: dict[str, object]) -> bytes:
    """The CHARX `data` with `payload` as its `card.json`, every other
    member kept as it was."""
    archive = open_archive(data)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as written:
        written.writestr(_CARD, json.dumps(payload))
        for info in archive.infolist():
            if info.filename != _CARD and not info.is_dir():
                written.writestr(info.filename, read_member(archive, info.filename))
    return out.getvalue()


def write_charx(payload: dict[str, object], carried: list[Asset]) -> bytes:
    """A CHARX of a card's text and its pictures, in their order: each in
    the folder of its type, listed in the text's `assets` with its type
    and name — the first `icon` the main one, named so."""
    data = payload.get("data")
    card = dict(data) if isinstance(data, dict) else {}
    listed: list[dict[str, str]] = []
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as written:
        for at, picture in enumerate(carried):
            ext = _extension(picture.data)
            name = picture.name or ("main" if not listed and picture.type == "icon" else str(at))
            path = f"assets/{picture.type}/{at}.{ext}"
            written.writestr(path, picture.data)
            listed.append(
                {"type": picture.type, "uri": f"embeded://{path}", "name": name, "ext": ext}
            )
        card["assets"] = listed
        written.writestr(_CARD, json.dumps({**payload, "data": card}))
    return out.getvalue()


def _extension(picture: bytes) -> str:
    return next(
        (ext for magic, ext in _PICTURE_EXTENSIONS if picture.startswith(magic)),
        "webp" if picture[8:12] == b"WEBP" else "bin",
    )

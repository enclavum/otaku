"""A card in a CHARX: the V3 spec's zip of `card.json` and its assets,
its main icon the card's picture and its other assets left behind; and a
JSON card packed with its picture into one (`pack_charx`), so a card a
site keeps beside its picture reads as one file. Every member is read
through one guard (`member`), which refuses one that would inflate past
any card's size before it is read."""

import io
import zipfile
import zlib

from otaku.backend.formats.cards.spec import CardError, assets, json_payload

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


def card_payload(archive: zipfile.ZipFile) -> dict[str, object]:
    """The card's text, its `card.json`."""
    try:
        return json_payload(member(archive, _CARD))
    except CardError as e:
        raise CardError(f"the CHARX's {_CARD} is no card ({e})") from e


def member(archive: zipfile.ZipFile, path: str) -> bytes:
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
        for asset in assets(payload)
        if isinstance(asset, dict) and asset.get("type") == "icon"
    ]
    icons.sort(key=lambda asset: asset.get("name") != "main")  # stable: `main` first
    names = archive.namelist()
    listed = (path for asset in icons if (path := _asset_path(asset)) in names)
    folder = (name for name in names if name.startswith(_ICONS) and not name.endswith("/"))
    return next(listed, None) or next(folder, None)


def other_assets(archive: zipfile.ZipFile, payload: dict[str, object]) -> int:
    """How many of the assets the card lists are not its main icon — what
    an import leaves behind."""
    icon = icon_path(archive, payload)
    return sum(1 for asset in assets(payload) if _asset_path(asset) != icon)


def pack_charx(card: bytes, picture: bytes) -> bytes:
    """A JSON card and its picture as one CHARX: the card as it came, its
    `card.json`, and the picture in the icon folder — where `icon_path`
    finds it when the card lists no icon."""
    extension = next(
        (ext for magic, ext in _PICTURE_EXTENSIONS if picture.startswith(magic)),
        "webp" if picture[8:12] == b"WEBP" else "bin",
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_CARD, card)
        archive.writestr(f"{_ICONS}main.{extension}", picture)
    return out.getvalue()


def _asset_path(asset: object) -> str | None:
    """An asset's file in the zip, from its uri; None for one kept
    elsewhere."""
    uri = asset.get("uri") if isinstance(asset, dict) else None
    if not isinstance(uri, str):
        return None
    return next((uri[len(s) :] for s in _EMBEDDED if uri.startswith(s)), None)

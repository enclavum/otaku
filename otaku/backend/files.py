"""Pictures coming in: read once, below both frontends, so what the
model is shown is the same whichever medium sent it.

A picture arrives as the reader's bytes and leaves as the wire copy: the
type sniffed from the bytes (never the name), decoded, the camera's
orientation applied, downsized to `WIRE_EDGE` and never upsized, every
metadata block dropped (EXIF with its GPS, XMP, comments — the colour
profile stays, it is not private and keeps the colours honest), and
re-encoded: PNG stays PNG, so a screenshot keeps its text sharp;
everything else becomes JPEG, the one format every engine takes. A
thumbnail of `THUMB_EDGE` is cut from the same decoded image for the
page's transcript. `read_picture` is that pipeline over bytes; `save`
stores what it made under its name and returns the row's side
(`store.schema.Attachment`), apart so a turn reads every picture before
it saves any.

What is refused, each with its own sentence (`Refused`): bytes over
`MAX_BYTES`, a type not taken, a picture over `MAX_PIXELS` (judged on
the header, before a pixel is decoded, which is what stops a
decompression bomb), a file that will not decode, and a HEIC where the
opener is absent (Pyodide has none). `MAX_PICTURES` is the per-turn
limit `play.submit` holds a line to, and the two sentences beside it are
the refusals that are not about one file: a model that cannot see, too
many pictures on one turn.
"""

import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from otaku.backend.session import Refused
from otaku.formatting import format_megabytes
from otaku.store.files import FileStore
from otaku.store.schema import Attachment

try:  # HEIC needs libheif, which pillow-heif bundles — except on Pyodide.
    from pillow_heif.as_plugin import register_heif_opener
except ImportError:  # pragma: no cover — the demo's case
    _HEIF = False
else:
    register_heif_opener()
    _HEIF = True

WIRE_EDGE = 1568  # px, the long edge on the wire; never upsized to it
THUMB_EDGE = 512  # px, the thumbnail's long edge
MAX_PICTURES = 8  # on one turn
MAX_BYTES = 10_000_000  # of one file as it arrives; decimal, as a phone shows it
MAX_PIXELS = 100_000_000  # decoded, judged from the header; a 48 MP phone is well under

CANNOT_SEE = "This model cannot see pictures."
TOO_MANY = f"At most {MAX_PICTURES} pictures on one turn."

_JPEG_QUALITY = 85
_THUMB_QUALITY = 80
# Pillow's names for what is taken. MPO is a JPEG with more pictures
# inside it — what an iPhone writes for an HDR gain map or portrait
# depth — and the first of them is the photograph.
_TAKEN = {"JPEG", "MPO", "PNG", "WEBP", "HEIF"}
_TAKEN_SENTENCE = "only JPEG, PNG, WebP or HEIC"
# The `ftyp` brands a HEIC or HEIF file opens with, for the refusal that
# names the format where its opener is absent.
_HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1")


@dataclass(frozen=True)
class RawFile:
    """A file as a frontend hands it over: the bytes, and what the
    reader called it — for the refusal sentences alone."""

    data: bytes
    name: str = ""


@dataclass(frozen=True)
class Picture:
    """A picture as the wire gets it, before it is stored: the bytes and
    their facts, and the thumbnail cut from the same image."""

    data: bytes
    media_type: str
    width: int
    height: int
    thumb: bytes


def save(files: FileStore, picture: Picture, story_id: int) -> Attachment:
    """A picture into the folder under the story's number, and the row
    that names it. Apart from `read_picture` so a turn can read every
    picture first and save none until all of them are: a refusal then
    leaves the folder as it was."""
    name = files.add(story_id, picture.data, picture.media_type, thumb=picture.thumb)
    return Attachment(
        file=name,
        width=picture.width,
        height=picture.height,
        size=len(picture.data),
    )


def read_picture(file: RawFile) -> Picture:
    """The module docstring's pipeline over a file in memory: the wire
    copy and the thumbnail, or a `Refused` naming what was wrong with
    it — by the name the reader gave it, which is used for the sentences
    alone. Pure — the unit suite's door."""
    data, name = file.data, file.name
    if len(data) > MAX_BYTES:
        raise Refused(
            f"Too big: {_label(name)} is {format_megabytes(len(data))} and the limit is "
            f"{format_megabytes(MAX_BYTES)}."
        )
    try:
        # Pillow's own bomb warning is quieted: `MAX_PIXELS` below is the
        # one guard, and it refuses where Pillow would only warn.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            opened = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        raise _picture_refused_resolution(name) from None
    except UnidentifiedImageError:
        if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS and not _HEIF:
            raise Refused(f"Cannot read HEIC here: {_label(name)}.") from None
        raise _picture_refused(name) from None
    if opened.format not in _TAKEN:
        raise _picture_refused(name)
    width, height = opened.size
    if width * height > MAX_PIXELS:
        raise _picture_refused_resolution(name, f"{width}×{height}")  # noqa: RUF001 — the sign, as a phone prints it
    png = opened.format == "PNG"
    icc = opened.info.get("icc_profile")
    try:
        # A copy either way, its orientation tag consumed; `thumbnail`
        # keeps the aspect and never enlarges.
        image: Image.Image = ImageOps.exif_transpose(opened) or opened
        image.thumbnail((WIRE_EDGE, WIRE_EDGE), Image.Resampling.LANCZOS)
    except OSError:  # truncated or corrupt past the header
        raise _picture_refused(name) from None
    thumb = image.copy()
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
    return Picture(
        data=_encode(image, png=png, icc=icc, quality=_JPEG_QUALITY),
        media_type="image/png" if png else "image/jpeg",
        width=image.width,
        height=image.height,
        thumb=_encode(thumb, png=False, icc=icc, quality=_THUMB_QUALITY),
    )


def _encode(image: Image.Image, *, png: bool, icc: bytes | None, quality: int) -> bytes:
    """The bytes of `image` in the one format chosen, carrying nothing
    from the source but the colour profile: `info` is emptied before the
    save, so no EXIF, XMP or comment rides along by default."""
    if png:
        # A palette's transparency lives in `info`, which is about to go:
        # it and every other mode PNG cannot hold plainly become RGBA.
        if image.mode not in ("RGB", "RGBA", "L", "LA", "1"):
            image = image.convert("RGBA")
    elif image.mode in ("RGBA", "LA", "P", "PA"):
        image = _on_white(image)
    elif image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.info.clear()
    out = io.BytesIO()
    if png:
        image.save(out, "PNG", optimize=True, icc_profile=icc)
    else:
        image.save(out, "JPEG", quality=quality, optimize=True, icc_profile=icc)
    return out.getvalue()


def _on_white(image: Image.Image) -> Image.Image:
    """Transparency flattened onto white, the way a page would show it —
    JPEG has no alpha, and a black backdrop is what a plain convert
    gives."""
    rgba = image.convert("RGBA")
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(white, rgba).convert("RGB")


def _label(name: str) -> str:
    return name or "the picture"


def _picture_refused(name: str) -> Refused:
    return Refused(f"App cannot accept this picture ({_TAKEN_SENTENCE}): {_label(name)}.")


def _picture_refused_resolution(name: str, dimensions: str = "") -> Refused:
    measure = f" is {dimensions} and" if dimensions else " is"
    return Refused(
        f"Too many pixels: {_label(name)}{measure} the limit is "
        f"{MAX_PIXELS // 1_000_000} megapixels."
    )

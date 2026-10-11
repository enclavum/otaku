"""The files folder beside the database: what a turn's pictures and a
codex card's are on disk. A picture is stored as the model sees it,
sealed with the session cipher like every column that holds the
reader's content, under a name a person can read in a listing:

    pic-0024-20260918-a3f9c1e2.jpg          a turn's picture, by its story's number
    pic-0024-20260918-a3f9c1e2-thumb.jpg    its thumbnail
    crd-0012-20261011-0b7e44d1.png          a codex card's picture, by the card's

Whose it is (`Owner`), its number, the day it was added, and a four-byte
BLAKE2 digest of the bytes as eight hex characters, then the picture's
own extension — so the folder sorts into owners and then into days,
and the same picture added twice for one owner on one day is one
file. A fork's rows keep the origin's names, so a fork shares the files
by reference, and a card row sent with a card's portrait shares the
card's (`schema.Attachment` is the row's side). The NAME, extension
included, is what a row carries and what every reader asks by; the
thumbnail's follows from it and is always JPEG. The digest is unkeyed:
under encryption, a holder of the folder who already has a picture can
tell whether it is in there, which is judged not worth a keyed hash's
machinery.

Nothing here knows what a picture is: bytes and a media type in, bytes
out. The folder is made on the first write, so a store without pictures
never has one. Deletion is a `sweep` against what the rows still
reference — `Store.sweep_files` runs it after a story or a card is
deleted and the launch runs it once more, so a crash between the rows
and the files heals on the next run. `Store` opens the folder beside the database it
opens, under the same cipher; the nucleus knows nothing of it.
"""

import hashlib
import os
import secrets
from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from pathlib import Path

from cryptography.exceptions import InvalidTag

from otaku.encryption import Cipher

_DECRYPT_ERRORS = (InvalidTag, ValueError)
_THUMB = "-thumb.jpg"
_DIGEST_BYTES = 4  # eight hex characters: git's habit for a short id, and enough for a story's day
# The extension by media type where the subtype is not it, and the media
# type by extension — what the folder holds is JPEG or PNG, the two
# every engine takes.
_EXTENSIONS = {"image/jpeg": "jpg"}
_MEDIA_TYPES = {".jpg": "image/jpeg", ".png": "image/png"}


class Owner(Enum):
    """Whose picture a file is, the first word of its name: a turn's,
    numbered by its story, or a codex card's, numbered by the card."""

    STORY = "pic"
    CARD = "crd"


class FileStore:
    def __init__(self, directory: Path, cipher: Cipher) -> None:
        self._dir = directory
        self._cipher = cipher

    def add(
        self, owner: Owner, number: int, data: bytes, media_type: str, thumb: bytes | None = None
    ) -> str:
        """Store `data` under the name the module docstring draws, for
        the owner's `number` and today, and return it. A file already
        there is the same bytes and is left as it is. The thumbnail is
        stored beside it when given."""
        day = datetime.now().astimezone().strftime("%Y%m%d")
        digest = hashlib.blake2b(data, digest_size=_DIGEST_BYTES).hexdigest()
        subtype = media_type.partition("/")[2] or media_type
        extension = _EXTENSIONS.get(media_type, subtype)
        name = f"{owner.value}-{number:04d}-{day}-{digest}.{extension}"
        if not (self._dir / name).exists():
            self._write(self._dir / name, data)
        if thumb is not None:
            thumb_path = self._dir / f"{_stem(name)}{_THUMB}"
            if not thumb_path.exists():
                self._write(thumb_path, thumb)
        return name

    def get(self, name: str) -> tuple[bytes, str] | None:
        """The picture's plain bytes and its media type, which its
        extension says. None when absent, or when the cipher cannot open
        it (a folder from another key) — served as absent, the way a
        sealed column that will not open reads as a sentinel rather than
        fail its caller."""
        media_type = _MEDIA_TYPES.get(Path(name).suffix.lower())
        if media_type is None:
            return None
        data = self._read(self._dir / name)
        return (data, media_type) if data is not None else None

    def get_thumb(self, name: str) -> bytes | None:
        """The thumbnail's plain bytes, under `get`'s rule."""
        return self._read(self._dir / f"{_stem(name)}{_THUMB}")

    def sweep(self, referenced: Iterable[str]) -> int:
        """Delete every file whose stem is not that of a name in
        `referenced`; how many went. A half-written temporary (a crash
        mid-write) goes with them: its name never matches."""
        if not self._dir.is_dir():
            return 0
        keep = {_stem(name) for name in referenced}
        gone = 0
        for path in self._dir.iterdir():
            if path.is_file() and _stem(path.name) not in keep:
                path.unlink()
                gone += 1
        return gone

    def _read(self, path: Path) -> bytes | None:
        try:
            sealed = path.read_bytes()
        except OSError:
            return None
        try:
            return self._cipher.unseal(sealed)
        except _DECRYPT_ERRORS:
            return None

    def _write(self, path: Path, data: bytes) -> None:
        """Sealed, then landed whole: written to a temporary beside the
        target and renamed over it, so a crash leaves no half file under
        a real name."""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._dir / f".{secrets.token_hex(8)}.tmp"
        tmp.write_bytes(self._cipher.seal(data))
        os.replace(tmp, path)


def _stem(name: str) -> str:
    """The stem a file's name carries: the extension off, and the
    thumbnail's mark with it."""
    return name.removesuffix(_THUMB).rsplit(".", 1)[0]

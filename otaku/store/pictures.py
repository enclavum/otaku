"""The pictures a row carries, as its JSON column records them: a
turn's (`Attachment`, `messages.attachments`) and a codex card's, each of
its kind (`CardPicture`, `cards.pictures`). Both are a file in the folder
beside the database (`store.files`) and its measure, which they share
(`_Picture`); neither stands for the other."""

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Self


@dataclass(frozen=True)
class _Picture(ABC):
    """What every kind of stored picture shares, and none is without — a
    file in the folder beside the database, as the app's facts about it,
    none of the reader's words: which is why the columns that record
    pictures are plain where the files are sealed. `file` is the
    file's name in the folder, extension included (`store.files` draws
    it; the extension says the type), the rest its measure as the model
    sees it — recorded so that no reader has to open a sealed file to
    describe a picture. The reader's own file name is not kept: nothing
    needs it once the picture is in."""

    file: str
    width: int
    height: int
    size: int

    @classmethod
    def of(cls, picture: "_Picture") -> Self:
        """Another picture's file and measure as this kind of picture —
        a card's portrait as the picture its story's row carries."""
        return cls(picture.file, picture.width, picture.height, picture.size)

    @classmethod
    @abstractmethod
    def from_json(cls, text: str | None) -> tuple[Self, ...]:
        """The column that records this kind of picture, read back."""

    @classmethod
    @abstractmethod
    def to_json(cls, pictures: Sequence[Self]) -> str | None:
        """The column's text for these pictures."""


@dataclass(frozen=True)
class Attachment(_Picture):
    """One picture on a turn, as the `attachments` column records it."""

    @classmethod
    def from_json(cls, text: str | None) -> tuple[Self, ...]:
        """The column read back; NULL → no pictures, which is also what a
        row written before the column existed reads as."""
        if not text:
            return ()
        return tuple(
            cls(
                file=str(item["file"]),
                width=int(item["width"]),
                height=int(item["height"]),
                size=int(item["size"]),
            )
            for item in json.loads(text)
        )

    @classmethod
    def to_json(cls, pictures: Sequence[Self]) -> str | None:
        """The column's text for these pictures: a JSON list, or NULL (None)
        for a turn without any — the column never holds an empty list
        pretending to be absent. The keys are a contract with the SQL that
        reads the column back (`$.file` is what the sweep extracts)."""
        if not pictures:
            return None
        return json.dumps(
            [
                {
                    "file": a.file,
                    "width": a.width,
                    "height": a.height,
                    "size": a.size,
                }
                for a in pictures
            ]
        )


class CardPictureKind(Enum):
    """What a card's picture shows, as card files name their assets: the
    character itself, one of its expressions, a background, or anything
    else a card carries."""

    PORTRAIT = "portrait"
    EXPRESSION = "expression"
    BACKGROUND = "background"
    OTHER = "other"


@dataclass(frozen=True)
class CardPicture(_Picture):
    """One of a codex card's pictures, as the `cards.pictures` column
    records it: a picture, what it shows, and the name its card file gave
    it ("" for none — a picture added by hand, a card file's one
    picture). A picture recorded before kinds were reads as a portrait,
    which every one was."""

    kind: CardPictureKind = CardPictureKind.PORTRAIT
    name: str = ""

    @classmethod
    def from_json(cls, text: str | None) -> tuple[Self, ...]:
        if not text:
            return ()
        kinds = {kind.value: kind for kind in CardPictureKind}
        return tuple(
            cls(
                file=str(item["file"]),
                width=int(item["width"]),
                height=int(item["height"]),
                size=int(item["size"]),
                # none recorded: a portrait, as every picture before was;
                # one this version does not know: another kind of picture
                kind=(
                    kinds.get(str(item["kind"]), CardPictureKind.OTHER)
                    if "kind" in item
                    else CardPictureKind.PORTRAIT
                ),
                name=str(item.get("name", "")),
            )
            for item in json.loads(text)
        )

    @classmethod
    def to_json(cls, pictures: Sequence[Self]) -> str | None:
        if not pictures:
            return None
        return json.dumps(
            [
                {
                    "file": a.file,
                    "width": a.width,
                    "height": a.height,
                    "size": a.size,
                    "kind": a.kind.value,
                    "name": a.name,
                }
                for a in pictures
            ]
        )

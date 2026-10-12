"""The sample stories a first launch lands, shipped in this folder as
the package's own data: each document, its pictures beside it and the
cards its characters become (`cards.toml`), seeded through the app's own
import, intake and codex — no pass runs and no model is called. The
launch calls `seed` on a database created from scratch."""

import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

from otaku.backend.api import Api
from otaku.backend.domain.app import App
from otaku.backend.domain.app.pictures import RawFile, read_picture, save
from otaku.backend.domain.story.settings import StorySettingQuestions
from otaku.backend.errors import NotFound, Refused
from otaku.store.files import Owner
from otaku.store.schema import Attachment

# What the launch says once a sample landed (`LaunchFacts.hint`).
HINT = (
    "Sample stories were imported so you can look around — type to play on, "
    "/stories switches between them, or see every command with /help · "
    "/model chooses a model · /new starts your own play."
)
# A sample's pictures ship beside its document, named for the message
# they belong to: `river.15.1.jpg` is the first picture of river.md's
# 15th message. The document itself carries no picture — the export
# format never does — so the first launch puts them on the message
# through the app's own intake, as a reader's attachment would land.
_SAMPLE_PICTURE = re.compile(r"^(?P<stem>.+)\.(?P<message>\d+)\.(?P<order>\d+)\.[A-Za-z0-9]+$")
# The river sample ends on a question, and a tool that is off has its
# calls neither drawn nor sent: it lands with the questions tool on.
_ASKING_SAMPLE = "river.md"
# Which of the samples' characters are kept in All characters, beside them.
_SAMPLE_CARDS = "cards.toml"


def seed(app: App) -> list[str]:
    """A database created from scratch is seeded with the shipped sample
    stories, through the import operation's own machinery — native
    imports, so no pass runs and no model is called — and remembered, so
    the user lands (and stays) in the middle of a playable story. Every
    samples/*.md lands, in name order, its pictures and its characters'
    cards with it, and the app settles in the FIRST of them: the short
    story is the landing, the rest wait in /stories. Returns what went
    wrong, as notices."""
    api = Api(app)
    notices: list[str] = []
    landed: list[int] = []
    for file in sorted(Path(__file__).parent.glob("*.md")):
        try:
            imported = api.imports.create(
                text=file.read_text(encoding="utf-8"), file_name=file.name
            ).value
        except (Refused, OSError) as e:
            # Best effort, per file: a damaged sample says so and the
            # others still land. A first launch is the worst possible
            # place for a traceback.
            notices.append(f"The sample story {file.name} could not be imported ({e}).")
        else:
            landed.append(imported.story_id)
            notices += _seed_pictures(app, imported.story_id, file)
            notices += _seed_cards(api, imported.story_id, file)
            if file.name == _ASKING_SAMPLE:
                api.stories.settings.update(
                    imported.story_id, StorySettingQuestions.name, enabled=True
                )
    if landed:
        api.settings.update(last_story=landed[0])
    return notices


def _seed_pictures(app: App, story_id: int, document: Path) -> list[str]:
    """The sample's pictures, shipped beside its document
    (`_sample_pictures`), into the story the way a reader's land: read
    through the one intake — sniffed, oriented, downsized, thumbnailed
    — saved under the story's number, and put on the message each
    belongs to. Best effort per picture: one that cannot be read, or
    names a message the document lacks, says so — the notices returned —
    and the rest land."""
    notices: list[str] = []
    names = sorted(p.name for p in document.parent.iterdir()) if document.parent.is_dir() else []
    ids = app._store.stories.get_messages_ids(story_id)
    by_message: dict[int, list[Attachment]] = {}
    for number, name in _sample_pictures(names, document.stem):
        path = document.parent / name
        try:
            if not 1 <= number <= len(ids):
                raise NotFound(f"{document.name} has no message {number}")
            picture = read_picture(RawFile(path.read_bytes(), name))
        except (Refused, OSError) as e:
            notices.append(f"The sample picture {name} could not be added ({e}).")
            continue
        by_message.setdefault(number, []).append(
            save(app._store.files, picture, Owner.STORY, story_id)
        )
    for number, attachments in by_message.items():
        app._store.messages.set_attachments(ids[number - 1], attachments)
    return notices


def _seed_cards(api: Api, story_id: int, document: Path) -> list[str]:
    """The sample's characters into All characters, as the samples' own
    manifest names them (`_SAMPLE_CARDS`): each made from its story's
    character and linked to it, its tagline set, and its portrait — a
    picture shipped beside the samples — added where one is named. Best
    effort per card: one that cannot be made says so — the notices
    returned — and the rest land."""
    manifest = document.parent / _SAMPLE_CARDS
    try:
        rows = tomllib.loads(manifest.read_text(encoding="utf-8")).get("card", [])
    except (OSError, tomllib.TOMLDecodeError):
        return []  # no manifest, no cards: the stories stand on their own
    notices: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("story") != document.stem:
            continue
        name = str(row.get("name", ""))
        try:
            found = api.stories.characters.list(story_id, name=name)
            if not found:
                raise NotFound(f"{document.name} has no character {name}")
            card = api.cards.create(story_id=story_id, character_id=found[0].id).value
            if row.get("tagline"):
                api.cards.update(card.id, tagline=str(row["tagline"]))
            if row.get("picture"):
                path = document.parent / str(row["picture"])
                api.cards.pictures.create(card.id, file=RawFile(path.read_bytes(), path.name))
        except (Refused, OSError) as e:
            notices.append(f"The sample card {name} could not be added ({e}).")
    return notices


def _sample_pictures(names: Iterable[str], stem: str) -> list[tuple[int, str]]:
    """The pictures shipped for the sample `stem` among `names`, each
    with the number of the message it belongs to, in message and then
    picture order (`_SAMPLE_PICTURE`): the shipped layout's one rule."""
    found: list[tuple[int, int, str]] = []
    for name in names:
        match = _SAMPLE_PICTURE.match(name)
        if match is not None and match.group("stem") == stem:
            found.append((int(match.group("message")), int(match.group("order")), name))
    return [(message, name) for message, _order, name in sorted(found)]

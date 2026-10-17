"""A story's memory as the terminal reaches it — the twin of
`backend.api.story.lore`: everything it answers, re-exported, and what
`/merge SOURCE into TARGET` means — the two names resolved against the
cast — with its sentence."""

from otaku.backend.api import Api, Invalid, NotFound
from otaku.backend.api.story.lore import Character as Character
from otaku.backend.api.story.lore import Extraction as Extraction
from otaku.backend.api.story.lore import Journal as Journal
from otaku.backend.api.story.lore import Lore as Lore
from otaku.backend.api.story.lore import ProfileFields as ProfileFields
from otaku.backend.api.story.lore import Scene as Scene


def merge(api: Api, story_id: int, raw: str) -> str:
    """`/merge SOURCE into TARGET`: names, multi-word ones surviving,
    resolved against the story's cast, then one folded into the other
    (`api.stories.characters.merges`)."""
    src_raw, sep, dst_raw = raw.partition(" into ")
    if not sep or not src_raw.strip() or not dst_raw.strip():
        raise Invalid("Usage: /merge SOURCE into TARGET")
    source = api.stories.characters.list(story_id, name=src_raw)
    target = api.stories.characters.list(story_id, name=dst_raw)
    if not source or not target:
        missing = src_raw if not source else dst_raw
        raise NotFound(f"No character named '{missing.strip()}' in this story (see /cast).")
    return api.stories.characters.merges.create(story_id, source[0].id, into=target[0].id).notice

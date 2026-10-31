"""The stories as the terminal reaches them — the twin of
`backend.story.stories`: everything it answers, re-exported — the
three refusals the terminal says itself where it has no story to ask
among them — and what `/title`, `/fork`, `/new` and a bare `/system`
mean, with the sentence each is answered with."""

from collections.abc import Callable
from contextlib import AbstractContextManager

from otaku.backend.api import Api, Changed, Conflict
from otaku.backend.story.stories.schemas import NOTHING_TO_REGENERATE as NOTHING_TO_REGENERATE
from otaku.backend.story.stories.schemas import NOTHING_TO_REVERT as NOTHING_TO_REVERT
from otaku.backend.story.stories.schemas import NOTHING_TO_UNDO as NOTHING_TO_UNDO
from otaku.backend.story.stories.schemas import Question as Question
from otaku.backend.story.stories.schemas import Story as Story
from otaku.backend.story.stories.schemas import StorySummary as StorySummary


def title(
    api: Api, story_id: int | None, raw: str, *, make: Callable[[], AbstractContextManager[int]]
) -> str:
    """`/title [TITLE]`: bare reports the story's title, a title is given
    to it — `make` making the story first when there is none yet, as a
    first turn would, and taking it back where the title is refused."""
    text = raw.strip()
    if text:
        with make() as made:
            return api.stories.update(made, title=text).notice
    current = api.stories.get(story_id).title if story_id is not None else ""
    return f'Title: "{current}"' if current else "Usage: /title NEW-TITLE"


def fork(api: Api, story_id: int | None, raw: str) -> Changed[Story]:
    """`/fork [TITLE]`: the story continued in a copy from its head, the
    argument the copy's title."""
    if story_id is None:
        # COPIED, as there is no story to ask: the home is
        # `backend.story.stories.services.fork_story`.
        raise Conflict("Nothing to fork yet — send a message first.")
    return api.stories.forks.create(story_id, title=raw)


def new(api: Api, raw: str) -> Changed[Story]:
    """`/new [TITLE]`: a new story, made at once, the argument its title."""
    return api.stories.create(title=raw)


def report_system(premise: str) -> str:
    """Bare `/system`: the premise, or that there is none. A report, so
    nothing changes."""
    return f'System: "{premise}"' if premise else "System: (none)"

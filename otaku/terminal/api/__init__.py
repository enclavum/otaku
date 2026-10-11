"""THE TERMINAL'S DOOR TO THE BACKEND — the one package of the terminal
that imports `otaku.backend.api`, as `web.api` is the page's. It is laid
out as the interface is, in its two packages of twins: a module for each
interface module, re-exporting everything that module answers and
holding what the terminal's own input means to it — a typed argument and
the sentence it is answered with (`app.model`, `story.stories.title`), a
setting's prompt (`story.prompts`), a provider's field
(`app.providers`). A plain call is made on the facade the `Chat` holds —
`Api`, re-exported here with the rest of the interface's root
vocabulary; the door adds a function only where it translates, and a
module of it is imported as `api_<name>`.

Imports nothing of the terminal: every other part of it stands above.
"""

from otaku.backend.api import (
    NO_MODEL_HINT,
    NO_STORY_HINT,
    NO_SUCH_STORY,
    UNCHANGED,
    Actor,
    Api,
    App,
    Attachment,
    Changed,
    CompletionType,
    Conflict,
    HeadMoved,
    InjectionPosition,
    Invalid,
    JoinType,
    KeySource,
    Layout,
    Locality,
    ModelRef,
    ModelState,
    NotFound,
    Refused,
    StoryMode,
    Unavailable,
    Unchanged,
)

__all__ = [
    "NO_MODEL_HINT",
    "NO_STORY_HINT",
    "NO_SUCH_STORY",
    "UNCHANGED",
    "Actor",
    "Api",
    "App",
    "Attachment",
    "Changed",
    "CompletionType",
    "Conflict",
    "HeadMoved",
    "InjectionPosition",
    "Invalid",
    "JoinType",
    "KeySource",
    "Layout",
    "Locality",
    "ModelRef",
    "ModelState",
    "NotFound",
    "Refused",
    "StoryMode",
    "Unavailable",
    "Unchanged",
]

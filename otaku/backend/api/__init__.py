"""THE INTERFACE — what both frontends call, transport-free and
REST-shaped, and the only part of the backend a frontend imports.

A frontend builds one `Api` over the App it is handed and calls its
resources: one per top-level path, a sub-resource an attribute of its
parent, so a call spells its URL — `api.stories.messages.create(5,
line=…)` is POST /stories/5/messages. A RESOURCE CLASS (`<Noun>Resource`)
holds the App and nothing else, no story and no cache, and its methods
are the five verbs — `list`, `get`, `create`, `update`, `delete` — the
ids first, in path order, the fields by keyword; an update changes the
fields given (`UNCHANGED` the default, so None can clear); a write
answers `Changed`, the thing as it now stands and the sentence; a stream
is a `create` whose answer arrives as events. What a resource answers is
an ENTITY CLASS, frozen data defined in its resource's module after the
resource classes, every derived fact a field — so a client calls the
resources and nothing else. The one exception is `language`, the story's
language as a client applies it to text not stored yet. A resource is
thin: the domain call (`backend.domain`), then its entity built — never
the store, never an `app._` member.

Re-exported here: `Api`, a write's answer, the refusals, and the
vocabulary several resources share. A resource's own entity classes are
imported from its module.
"""

from otaku.backend.api._writes import UNCHANGED, Changed, Unchanged
from otaku.backend.api.app import AppResource
from otaku.backend.api.app.history import HistoryResource
from otaku.backend.api.app.integrations import IntegrationsResource
from otaku.backend.api.app.pictures import PicturesResource
from otaku.backend.api.app.providers import ProvidersResource
from otaku.backend.api.app.reports import BalanceResource, InfoResource, UsageResource
from otaku.backend.api.app.settings import SettingsResource
from otaku.backend.api.codex.cards import CardsResource
from otaku.backend.api.story.language import LanguageResource
from otaku.backend.api.story.prompts import PromptsResource, SharedReminderResource, ToolsResource
from otaku.backend.api.story.stories import StoriesResource
from otaku.backend.api.story.transfer import ImportsResource
from otaku.backend.domain.app import App
from otaku.backend.domain.story.messages import Actor
from otaku.backend.errors import (
    NO_MODEL_HINT,
    NO_STORY_HINT,
    NO_SUCH_STORY,
    Conflict,
    HeadMoved,
    Invalid,
    NotFound,
    Refused,
    Unavailable,
)
from otaku.context.assembler import JoinType
from otaku.providers import KeySource, Locality, ModelState
from otaku.settings.state import ModelRef
from otaku.store.schema import (
    Attachment,
    CompletionType,
    InjectionPosition,
    Layout,
    StoryMode,
)


class Api:
    """The interface over one opened app: a resource per top-level path,
    each built over the same App — what a frontend holds and calls. It
    holds the app and nothing else, so one may serve any number of
    clients."""

    def __init__(self, app: App) -> None:
        self.app = AppResource(app)
        self.settings = SettingsResource(app)
        self.tools = ToolsResource(app)
        self.prompts = PromptsResource(app)
        self.shared_reminder = SharedReminderResource(app)
        self.providers = ProvidersResource(app)
        self.cards = CardsResource(app)
        self.stories = StoriesResource(app)
        self.imports = ImportsResource(app)
        self.usage = UsageResource(app)
        self.info = InfoResource(app)
        self.balance = BalanceResource(app)
        self.pictures = PicturesResource(app)
        self.history = HistoryResource(app)
        self.integrations = IntegrationsResource(app)
        self.language = LanguageResource(app)


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

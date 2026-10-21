"""THE TERMINAL'S DOOR TO THE BACKEND — the one package of the terminal
that imports the backend, as `web.api` is the page's. This root
re-exports what the terminal takes from it as it is: the interface's root
vocabulary (`Api` among it, the facade the `Chat` holds and every plain
call is made on), the schemas the terminal draws and passes on (a message
and the play's events, a story setting, the web search's key), and the
story's language as a module (`language`).

A module of the door exists only where the terminal's own input means
something to a resource — a typed argument and the sentence it is
answered with (`app.model`, `stories.title`), a setting's prompt
(`prompts`), a provider's field (`providers`) — one flat module named
after the resource it translates for (`app` for the app's own),
re-exporting what that resource answers, and imported as `api_<name>`.

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
from otaku.backend.app.integrations.schemas import WebSearch
from otaku.backend.story.language import schemas as language
from otaku.backend.story.messages.schemas import (
    AssistantMessage,
    AssistantReasoning,
    AssistantText,
    AssistantToolCall,
    Declined,
    Done,
    Failed,
    Message,
    PlayEvent,
    RawFile,
    ToolMessage,
    UserMessage,
)
from otaku.backend.story.settings.schemas import DEFAULT_LAYOUT, LAYOUT_SETTING, StorySetting
from otaku.backend.story.transfer.schemas import EXPORT_SUFFIX, NOTHING_TO_EXPORT

__all__ = [
    "DEFAULT_LAYOUT",
    "EXPORT_SUFFIX",
    "LAYOUT_SETTING",
    "NOTHING_TO_EXPORT",
    "NO_MODEL_HINT",
    "NO_STORY_HINT",
    "NO_SUCH_STORY",
    "UNCHANGED",
    "Actor",
    "Api",
    "App",
    "AssistantMessage",
    "AssistantReasoning",
    "AssistantText",
    "AssistantToolCall",
    "Attachment",
    "Changed",
    "CompletionType",
    "Conflict",
    "Declined",
    "Done",
    "Failed",
    "HeadMoved",
    "InjectionPosition",
    "Invalid",
    "JoinType",
    "KeySource",
    "Layout",
    "Locality",
    "Message",
    "ModelRef",
    "ModelState",
    "NotFound",
    "PlayEvent",
    "RawFile",
    "Refused",
    "StoryMode",
    "StorySetting",
    "ToolMessage",
    "Unavailable",
    "Unchanged",
    "UserMessage",
    "WebSearch",
    "language",
]

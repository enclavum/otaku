"""The model's language: the tools it may use by writing a call in its
reply — one class each, and the one registry.

A class declares a tool: its name, and who acts on its call. An
instance is one call of it, read at construction — what the play stream
makes of a reply. WHAT a call is — a fenced block marked `otk-NAME`,
whatever the name — is `context.tool_calls`' business, which knows no
tool: a body keeps its fences for good, and a call must stay one in a
build that never heard of its tool. Whether a story lets the model use
a tool, the prompt that tells the model how, and where it rides, are
the story's setting's (`backend.story`).

A tool otaku RUNS (`Actor.SYSTEM`) ends the reply as a question does,
and its `run` yields the RESULT otaku records as the TOOL turn the
model reads next — the reply stream's loop (`api.play._reply_events`)
hands it the session, records the result, and asks the model again.

A new tool is a subclass and its name in `TOOLS`.
"""

import enum
import re
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from otaku.integrations import Astronomy, IntegrationError, SearchResult, WebSearch

if TYPE_CHECKING:
    # the session imports the stories' settings, which import these tools
    from otaku.backend.session import Session


class Actor(enum.Enum):
    """Who acts on a tool's call."""

    NOBODY = "nobody"  # an aside: the reply goes on after the call
    USER = "user"  # the reply ends there, the user's next line answers
    SYSTEM = "system"  # the reply ends there, otaku runs the tool, the model goes on


class Tool:
    """One tool, declared by its class; an instance is one call of it,
    read at construction."""

    name: ClassVar[str] = ""  # its name in the namespace: a fenced block marked otk-NAME
    label: ClassVar[str] = ""  # what a frontend calls it, where one names it
    actor: ClassVar[Actor] = Actor.NOBODY

    text: str  # the call's inside, as written

    def __init__(self, text: str) -> None:
        self.text = text

    def to_json(self) -> dict[str, object]:
        """The call as a frontend draws it — the tool, its label and who
        acts on it, its text, and whatever the tool read out of it. Plain
        data; the page keys on it."""
        return {
            "tool": self.name,
            "label": self.label,
            "actor": self.actor.value,
            "text": self.text,
        }

    def run(self, session: "Session") -> str:
        """The RESULT of a call of this tool — the TOOL turn's text, the
        model's to read next — read off the session, which a tool reads
        and never writes. A tool the SYSTEM acts on has a result; every
        other has none to give."""
        raise NotImplementedError(f"the {self.name} tool is not otaku's to run")

    @classmethod
    def label_of(cls, result: str) -> str:
        """What a frontend calls a TOOL turn of this tool holding `result`
        — the tool's label, unless the result says it is something else."""
        return cls.label


class ToolQuestion(Tool):
    """A block marked `otk-question` — ONE question to the user, closing the reply:
    the `question`, then the `options` to pick from — none when the
    question is free-form, `_MAX_OPTIONS` at most. Whatever follows the
    options that is not one is dropped: a picked option is sent as the
    user's line, and a closing remark must not ride it."""

    name = "question"
    label = "Questions"
    actor = Actor.USER
    # An answer line: `1.` `1)` `a.` `a)` at the head of a line, then the
    # answer itself.
    _OPTION: ClassVar[re.Pattern[str]] = re.compile(r"\s*(?:\d{1,2}|[A-Za-z])[.)]\s+(\S.*)")
    _MAX_OPTIONS: ClassVar[int] = 9

    question: str
    options: tuple[str, ...]

    def __init__(self, text: str) -> None:
        super().__init__(text)
        question: list[str] = []
        options: list[str] = []
        for line in text.splitlines():
            found = self._OPTION.fullmatch(line)
            if found is not None:
                options.append(found.group(1).rstrip())
            elif not options:
                question.append(line)
        self.question = "\n".join(question).strip()
        self.options = tuple(options[: self._MAX_OPTIONS])

    def to_json(self) -> dict[str, object]:
        return {**super().to_json(), "question": self.question, "options": list(self.options)}

    @classmethod
    def is_option(cls, line: str) -> bool:
        """Whether `line` is an answer to pick from — the head every
        option has, then the answer — for a reader of the call as it
        streams, which must part the question from its options by the
        same rule the parse does."""
        return cls._OPTION.fullmatch(line) is not None


class ToolAssistantNote(Tool):
    """A block marked `otk-note` — the model's private aside before its reply:
    what the visible scene cannot show, kept for its own later turns."""

    name = "note"
    label = "Notes"


class ToolSystemInfo(Tool):
    """A block marked `otk-system-info`, empty — the model asks the hour and
    the sky, and which model it is: the local time and weekday, the
    moon's phase, the nearest solstice or equinox, the model's name.
    Nothing that tells where the machine is — the time carries no zone —
    or what it runs. A fixture for the tools otaku runs, and the one
    result that needs no key."""

    name = "system-info"
    label = "System info"
    actor = Actor.SYSTEM

    def run(self, session: "Session") -> str:
        now = datetime.now().astimezone()
        sky = session.integrations.get(Astronomy)
        return "\n".join(
            (
                f"Time: {now:%Y-%m-%d %H:%M (%A)}",
                f"Moon: {sky.moon_phase(now)}",
                f"Sun: {sky.turning_point(now)}",
                f"Model: {session.model}",
            )
        )


class ToolWebSearch(Tool):
    """A block marked `otk-web-search` — the model looks something up: the
    inside is the query, and the result is the top results' snippets as
    "title: snippet" paragraphs, cut to a character budget so the
    result's cost is bounded whatever comes back — or the sentence for
    why there are none. Asked of the web search integration, whose key
    says which engine answers (`integrations.WebSearch`)."""

    name = "web-search"
    label = "Web search"
    actor = Actor.SYSTEM
    MAX_RESULTS: ClassVar[int] = 4
    CHAR_BUDGET: ClassVar[int] = 1500  # characters of snippets at most
    # What a failure's sentence opens with, and what its turn is called.
    _FAILED: ClassVar[str] = "Web search failed"

    def run(self, session: "Session") -> str:
        query = " ".join(self.text.split())
        if not query:
            return "No query was given."
        try:
            results = session.integrations.get(WebSearch).search(
                query, max_results=self.MAX_RESULTS
            )
        except IntegrationError as e:
            return f"{self._FAILED}: {e}."
        return self._render(results)

    def _render(self, results: list[SearchResult]) -> str:
        parts: list[str] = []
        used = 0
        for result in results:
            snippet = result.snippet.strip()
            if not snippet:
                continue
            remaining = self.CHAR_BUDGET - used
            if remaining <= 0:
                break
            snippet = snippet[:remaining]
            parts.append(f"{result.title}: {snippet}")
            used += len(snippet)
        return "\n\n".join(parts) if parts else "No results found."

    @classmethod
    def label_of(cls, result: str) -> str:
        # a search that failed is called so, in both frontends
        return cls._FAILED if result.startswith(f"{cls._FAILED}:") else cls.label


# Every tool, keyed by its name — the one registry.
TOOLS: dict[str, type[Tool]] = {
    cls.name: cls for cls in (ToolQuestion, ToolAssistantNote, ToolSystemInfo, ToolWebSearch)
}


def read_call(name: str, text: str) -> Tool:
    """The call, read as the tool its name says, in any case. Raises
    KeyError for a name no tool owns."""
    return TOOLS[name.lower()](text)


def get_tool_label(name: str | None, result: str) -> str:
    """What a frontend calls a tool turn: the label of the tool its
    `provider` names, for the `result` it holds (`Tool.label_of` — "Web
    search failed" for a search that failed); the name itself for a tool
    this build does not have, "" for none."""
    if not name:
        return ""
    found = TOOLS.get(name)
    return found.label_of(result) if found is not None else name

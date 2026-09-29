"""The model's language: the tools it may use by writing a call in its
reply — one class each, and the one registry.

A class declares a tool: its name, who acts on its call, and the key of
its prompt in prompts.toml. An instance is one call of it, read at
construction — what the play stream makes of a reply. WHAT a call is —
a fenced block marked `otk-NAME`, whatever the name — is
`context.tool_calls`' business, which knows no tool: a body keeps its
fences for good, and a call must stay one in a build that never heard
of its tool. Whether a
story lets the model use a tool, and where its prompt rides, is the
story's setting (`backend.story`).

A tool otaku RUNS (`Actor.SYSTEM`) ends the reply as a question does,
and its `run` yields the RESULT otaku records as the TOOL turn the
model reads next — the reply stream's loop (`api.play._reply_events`)
hands it the integrations as bound, records the result, and asks the
model again.

A new tool is a subclass and its name in `TOOLS`.
"""

import enum
import os
import re
from datetime import datetime
from typing import ClassVar

from otaku.integrations import IntegrationError, Integrations, SearchResult


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
    # The prompts.toml key of its prompt — what tells the model how the
    # tool is used. The story's setting reads the text under it.
    prompt_name: ClassVar[str] = ""

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

    def run(self, integrations: Integrations) -> str:
        """The RESULT of a call of this tool — the TOOL turn's text, the
        model's to read next — given the integrations as bound, of which
        a tool takes the one it runs on, or none. A tool the SYSTEM acts
        on has a result; every other has none to give."""
        raise NotImplementedError(f"the {self.name} tool is not otaku's to run")

    @classmethod
    def to_prose(cls, text: str) -> str:
        """What a call of this tool reads as on the wire while the tool is
        OFF — story text, or nothing. Nothing by default: a call the
        model is no longer told about teaches it nothing."""
        return ""


class ToolQuestions(Tool):
    """A block marked `otk-question` — ONE question to the user, closing the reply:
    the `question`, then the `options` to pick from — none when the
    question is free-form, `_MAX_OPTIONS` at most. Whatever follows the
    options that is not one is dropped: a picked option is sent as the
    user's line, and a closing remark must not ride it."""

    name = "question"
    actor = Actor.USER
    prompt_name = "tool_questions_prompt"
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

    @classmethod
    def to_prose(cls, text: str) -> str:
        # The question alone, as the narrator's own: the reader's answer
        # that follows still has something to answer. The options were
        # the menu's, and a menu is what the model must not learn here.
        return cls(text).question


class ToolAssistantNotes(Tool):
    """A block marked `otk-note` — the model's private aside before its reply:
    what the visible scene cannot show, kept for its own later turns."""

    name = "note"
    label = "Notes"
    prompt_name = "tool_assistant_notes_prompt"


class ToolSystem(Tool):
    """A block marked `otk-system`, empty — the model asks the machine:
    the time now, and the load it is under. A fixture for the tools
    otaku runs, and the one result that needs no key."""

    name = "system"
    label = "System"
    actor = Actor.SYSTEM
    prompt_name = "tool_system_prompt"

    def run(self, integrations: Integrations) -> str:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            one, five, fifteen = os.getloadavg()
            load = f"{one:.2f} {five:.2f} {fifteen:.2f}"
        except (AttributeError, OSError):
            load = "unknown"
        return f"Time: {now}\nLoad average (1, 5, 15 min): {load}"


class ToolWebSearch(Tool):
    """A block marked `otk-search` — the model looks something up: the
    inside is the query, and the result is the top results' snippets as
    "title: snippet" paragraphs, cut to a character budget so the
    result's cost is bounded whatever comes back — or the sentence for
    why there are none. The engine asked is the one the web search key
    chose (`Integrations.web_search_engine`)."""

    name = "search"
    label = "Web search"
    actor = Actor.SYSTEM
    prompt_name = "tool_web_search_prompt"
    MAX_RESULTS: ClassVar[int] = 4
    CHAR_BUDGET: ClassVar[int] = 1500  # characters of snippets at most

    def run(self, integrations: Integrations) -> str:
        query = " ".join(self.text.split())
        if not query:
            return "No query was given."
        engine = integrations.web_search_engine
        if engine is None or not engine.api_key:
            return "Web search is not set up: no API key."
        try:
            results = engine.search(query, max_results=self.MAX_RESULTS)
        except IntegrationError as e:
            return f"Web search failed: {engine.label} — {e}."
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


# Every tool, keyed by its name — the one registry.
TOOLS: dict[str, type[Tool]] = {
    cls.name: cls for cls in (ToolQuestions, ToolAssistantNotes, ToolSystem, ToolWebSearch)
}


def read_call(name: str, text: str) -> Tool:
    """The call, read as the tool its name says, in any case. Raises
    KeyError for a name no tool owns."""
    return TOOLS[name.lower()](text)


def get_tool_label(name: str | None) -> str:
    """What a frontend calls the tool a tool turn's `provider` names —
    the registry's label; the name itself for one this build does not
    have, "" for none."""
    if not name:
        return ""
    found = TOOLS.get(name)
    return found.label if found is not None else name

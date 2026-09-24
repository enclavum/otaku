"""The model's language: the tools it may use by writing a call in its
reply — one class each, and the one registry.

A class declares a tool: its name, who answers its call, and the key of
its prompt in prompts.toml. An instance is one call of it, read at
construction — what the play stream makes of a reply. WHAT a call is —
`<otk-NAME>…</otk-NAME>`, whatever the name — is `context.tool_calls`'
business, which knows no tool: a body keeps its tags for good, and a
call must stay one in a build that never heard of its tool. Whether a
story lets the model use a tool, and where its prompt rides, is the
story's setting (`backend.story`).

A new tool is a subclass and its name in `TOOLS`.
"""

import enum
import re
from typing import ClassVar


class Actor(enum.Enum):
    """Who answers a tool's call."""

    NOBODY = "nobody"  # an aside: the reply goes on after the call
    USER = "user"  # the reply ends there, the user's next line answers
    SYSTEM = "system"  # otaku answers by itself — not supported yet


class Tool:
    """One tool, declared by its class; an instance is one call of it,
    read at construction."""

    name: ClassVar[str] = ""  # its name in the namespace: <otk-NAME>…</otk-NAME>
    actor: ClassVar[Actor] = Actor.NOBODY
    # The prompts.toml key of its prompt — what tells the model how the
    # tool is used. The story's setting reads the text under it.
    prompt_name: ClassVar[str] = ""

    text: str  # the call's inside, as written

    def __init__(self, text: str) -> None:
        self.text = text

    def to_json(self) -> dict[str, object]:
        """The call as a frontend draws it — the tool and its text, and
        whatever the tool read out of it. Plain data; the page keys on it."""
        return {"tool": self.name, "text": self.text}

    @classmethod
    def to_prose(cls, text: str) -> str:
        """What a call of this tool reads as on the wire while the tool is
        OFF — story text, or nothing. Nothing by default: a call the
        model is no longer told about teaches it nothing."""
        return ""


class ToolAssistantNotes(Tool):
    """`<otk-note>…</otk-note>` — the model's private aside before its reply:
    what the visible scene cannot show, kept for its own later turns."""

    name = "note"
    prompt_name = "tool_assistant_notes_prompt"


class ToolQuestions(Tool):
    """`<otk-question>…</otk-question>` — ONE question to the user, closing the reply:
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


# Every tool, keyed by its name — the one registry.
TOOLS: dict[str, type[Tool]] = {cls.name: cls for cls in (ToolAssistantNotes, ToolQuestions)}


def read(name: str, text: str) -> Tool:
    """The call, read as the tool its name says, in any case. Raises
    KeyError for a name no tool owns."""
    return TOOLS[name.lower()](text)

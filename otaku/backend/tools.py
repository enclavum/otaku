"""The model's language: the tools it may use by writing a tagged block
in its reply — one class each, and the one registry.

A class declares a tool: its tag, who answers its block, and the name of
its instruction in prompts.toml. An instance is one block of it, read at
construction — what the play stream makes of a reply. WHAT a block is —
`<otk-NAME>…</otk-NAME>`, whatever the name — is `context.blocks`'
business, which knows no tool: a body keeps its tags for good, and a
block must stay one in a build that never heard of its tool. Whether a
story lets the model use a tool, and where its instruction rides, is the
story's setting (`backend.story`).

A new tool is a subclass and its name in `TOOLS`.
"""

import enum
import re
from typing import ClassVar


class Actor(enum.Enum):
    """Who answers a tool's block."""

    NOBODY = "nobody"  # an aside: the reply goes on after the block
    USER = "user"  # a call: the reply ends there, the user's next line answers
    SYSTEM = "system"  # a call otaku answers by itself — not supported yet


class Tool:
    """One tool, declared by its class; an instance is one block of it,
    read at construction."""

    tag: ClassVar[str] = ""  # its name in the namespace: <otk-TAG>…</otk-TAG>
    actor: ClassVar[Actor] = Actor.NOBODY
    # The prompts.toml field of its instruction — what tells the model how
    # the tool is used. The story's setting reads the text under it.
    prompt_name: ClassVar[str] = ""

    text: str  # the block's inside, as written

    def __init__(self, text: str) -> None:
        self.text = text


class ToolAssistantNotes(Tool):
    """`<otk-notes>…</otk-notes>` — the model's private aside before its reply:
    what the visible scene cannot show, kept for its own later turns."""

    tag = "notes"
    prompt_name = "notes_instruction"


class ToolQuestions(Tool):
    """`<otk-ask>…</otk-ask>` — ONE question to the user, closing the reply:
    the `question`, then the `options` to pick from — none when the
    question is free-form, `_MAX_OPTIONS` at most. Whatever follows the
    options that is not one is dropped: a picked option is sent as the
    user's line, and a closing remark must not ride it."""

    tag = "ask"
    actor = Actor.USER
    prompt_name = "ask_instruction"
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


# Every tool, keyed by its tag — the one registry.
TOOLS: dict[str, type[Tool]] = {cls.tag: cls for cls in (ToolAssistantNotes, ToolQuestions)}


def read(tag: str, text: str) -> Tool:
    """The block, read as the tool its tag names, in any case. Raises
    KeyError for a tag no tool owns."""
    return TOOLS[tag.lower()](text)

"""Slash-command completion for the REPL.

A small custom Completer that walks the command tree by whitespace-split
tokens, matching the literal current token against the keys at the current
node. The tree derives from `commands.COMMANDS`, and each row
carries its /help line as the menu's meta column. A `PATH_LEAF` node
hands the argument to `chat.pathcomplete` — this module owns only WHEN
that fires (behind an explicit `@`, immediately and while typing) and
the raw-line slicing that lets spaces survive in paths.

It fires ONLY on lines that start with `/`: the REPL completes while
typing (the menu opens the moment `/` is pressed and filters with every
keystroke), so on prose the completer must stay silent or the menu would
pop mid-sentence.
"""

import re
from collections.abc import Iterator
from typing import Any, Self

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from otaku.chat import pathcomplete
from otaku.chat.commands import (
    PATH_LEAF,
    CompletionTree,
    completion_tree,
    describe_command,
)


class SlashCompleter(Completer):
    def __init__(self, tree: CompletionTree) -> None:
        self.tree = tree

    @classmethod
    def build(cls) -> Self:
        """A completer over the current command table."""
        return cls(completion_tree())

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        text = document.text_before_cursor
        if not text.lstrip().startswith("/"):
            return  # prose — never open the menu mid-sentence
        # Mid-token: the partial is the last whitespace-separated word;
        # after a trailing space, offer the children at the current node.
        tokens = list(re.finditer(r"\S+", text))
        if text.endswith((" ", "\t")):
            path = [m.group(0) for m in tokens]
            walked = tokens
            partial = ""
        else:
            path = [m.group(0) for m in tokens[:-1]]
            walked = tokens[:-1]
            partial = tokens[-1].group(0) if tokens else ""

        node: Any = self.tree
        arg_start: int | None = None
        for match in walked:
            if node == PATH_LEAF:
                break  # further tokens are path text (spaces in filenames)
            token = match.group(0)
            if not isinstance(node, dict) or token not in node:
                return
            node = node[token]
            if node == PATH_LEAF:
                # The argument begins at the first non-space char after this
                # token — sliced from the raw line, so spaces survive.
                rest = text[match.end() :]
                arg_start = match.end() + (len(rest) - len(rest.lstrip()))
            if node is None:
                return

        if node == PATH_LEAF:
            # Paths complete behind an explicit `@`: the menu pops the
            # moment it is typed and filters while typing. Never on a bare
            # path — a directory listing under every keystroke of ordinary
            # text would be noise. The handlers strip the `@`; it is a
            # trigger, not part of the name.
            if arg_start is not None and text[arg_start:].startswith("@"):
                yield from pathcomplete.completions(text[arg_start + 1 :])
            return

        if not isinstance(node, dict):
            return

        # Fixed column widths: pad every row to the widths of the WHOLE
        # node, not the filtered subset, so the menu never resizes as you
        # type.
        metas = {key: describe_command((*path, key)) for key in node}
        key_width = max((len(key) for key in node), default=0)
        meta_width = max((len(meta) for meta in metas.values()), default=0)
        for key in node:
            if key.startswith(partial):
                yield Completion(
                    key,
                    start_position=-len(partial),
                    display=key.ljust(key_width),
                    display_meta=metas[key].ljust(meta_width) if meta_width else None,
                )

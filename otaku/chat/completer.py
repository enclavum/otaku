"""Tab completion for the otaku REPL.

A small custom Completer that walks a nested dict tree by whitespace-split
tokens. Unlike prompt_toolkit's NestedCompleter (which uses a WordCompleter
at the root and splits on '/' as a word boundary, so '/c<Tab>' matches
nothing) this matches the literal current token against the keys at the
current tree node.

The tree itself is derived from `commands.COMMANDS` so adding a new
slash command in one place automatically wires up its completions.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from otaku.chat.commands import CompletionTree, completion_tree


class SlashCompleter(Completer):
    def __init__(self, tree: CompletionTree) -> None:
        self.tree = tree

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        text = document.text_before_cursor
        # If user is in the middle of typing a token, the partial is the last
        # whitespace-separated word; otherwise (trailing space) we offer the
        # children at the current node.
        if text.endswith((" ", "\t")):
            path = text.split()
            partial = ""
        else:
            parts = text.split()
            path = parts[:-1]
            partial = parts[-1] if parts else ""

        node: Any = self.tree
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return
            node = node[p]
            if node is None:
                return

        if not isinstance(node, dict):
            return

        for key in node:
            if key.startswith(partial):
                yield Completion(key, start_position=-len(partial))


def build_completer() -> Completer:
    return SlashCompleter(completion_tree())

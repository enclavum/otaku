"""Slash-command completion for the REPL.

A small custom Completer that walks the command tree by whitespace-split
tokens, matching the literal current token against the keys at the current
node. The tree derives from `commands.registry.COMMANDS`, and each row
carries its /help line as the menu's meta column.

It fires ONLY on lines that start with `/`: the REPL completes while
typing (the menu opens the moment `/` is pressed and filters with every
keystroke), so on prose the completer must stay silent or the menu would
pop mid-sentence.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Self

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from otaku.chat.commands.registry import (
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
            # Filenames complete on Tab only — auto-popping a directory
            # listing under every keystroke of a path would be noise.
            if arg_start is not None and complete_event.completion_requested:
                yield from _path_completions(text[arg_start:])
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


def _path_completions(prefix: str) -> Iterator[Completion]:
    """Filesystem completion for the last SEGMENT of `prefix` (the text
    after the final `/`), so `~/` and earlier directories stay exactly as
    typed — and spaces anywhere in the path just work, because the prefix
    is sliced from the raw line, never whitespace-tokenized."""
    sep = prefix.rfind("/")
    base, frag = (prefix[: sep + 1], prefix[sep + 1 :]) if sep >= 0 else ("", prefix)
    directory = Path(base).expanduser() if base else Path(".")
    try:
        entries = sorted(directory.iterdir(), key=lambda e: e.name.casefold())
    except OSError:
        return
    names = [
        entry.name + ("/" if entry.is_dir() else "")
        for entry in entries
        if entry.name.startswith(frag) and (frag.startswith(".") or not entry.name.startswith("."))
    ]
    width = max((len(name) for name in names), default=0)
    for name in names:
        yield Completion(name, start_position=-len(frag), display=name.ljust(width))

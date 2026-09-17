"""Completion at the prompt — the whole of it: the two slash menus
(commands at a line's start, inliners mid-line), and filesystem paths
behind an explicit `@` (only the last segment completes, hidden entries
only when asked, directories with a trailing `/`; `split` and `matches`
are the pure core).

A line that STARTS with a slash walks the command tree (specs and
descriptions from `backend.commands`, the terminal's completion shapes —
path arguments behind `@`, cast names per command — bound here); a slash
token typed INSIDE a played line offers the inliners (their rows come
from `backend.commands` like every other's). On ordinary prose both stay
silent — the two surfaces are mutually exclusive by construction:
`applies` asks the same question from opposite sides, so the menu never
pops mid-sentence. The cast menu reads `api.lore.cast` through the
callable the prompt passes — cheap and empty-safe, fit for a
per-keystroke call.
"""

import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Self

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from otaku.backend.commands import COMMANDS, CommandSpec
from otaku.backend.session import PARAMETERS, THINK_MENU
from otaku.terminal.tty import latin_key

# The story's characters for the menus: (name, one-line description).
Cast = Sequence[tuple[str, str]]

# A completion-tree leaf may be PATH_LEAF: "complete a filesystem path
# here" (behind an `@`). Paths may contain spaces — handlers read them
# from the raw text, never from split tokens, and strip the leading `@`.
PATH_LEAF = "<path>"

# A leaf may instead be NAME_LEAF: "a character name goes here" — the
# completer offers the story's cast, shaped per command (`_cast_rows`).
# Names may contain spaces, so like a path the argument is read raw.
NAME_LEAF = "<name>"

# A leaf may be THINK_LEAF: "a thinking level goes here" — the completer
# offers what the model in use takes, looked up live
# (`api.settings.think_levels`), in the shared ladder's order.
THINK_LEAF = "<level>"

# And PARAMETER_LEAF: "a /set parameter goes here" — the ones the
# provider in use reads, looked up live (`api.settings.parameter_names`),
# each followed by its `reset`.
PARAMETER_LEAF = "<parameter>"

CompletionTree = dict[str, "CompletionTree | str | None"]

# The terminal's completion SHAPES: which command's argument is a path,
# which a character name, and the value menus of the /set family — bound
# here because they are completion affordances, not command semantics.
_PATH_COMMANDS = {"/system", "/card", "/import", "/export"}
_NAME_COMMANDS = {"/merge"}


class MenuRow(Completion):
    """A menu row that also says whether anything can follow the token it
    inserts. The prompt adds a space when accepting one, so the rest can
    be typed straight on — required or optional alike, since a row that
    takes a parameter is chosen to be given one. Only a command that
    takes nothing at all is ready to send the moment it is chosen."""

    def __init__(self, *args: Any, takes_argument: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.takes_argument = takes_argument


class _Surface(Completer):
    """One of the two menus. Beyond the completer protocol each answers
    two questions about the line alone: whether it is the surface in
    play, and which token is being completed on it — empty when a menu
    belongs here with nothing typed into it yet."""

    @staticmethod
    def applies(text_before_cursor: str) -> bool:
        raise NotImplementedError

    @staticmethod
    def partial(text_before_cursor: str) -> str:
        raise NotImplementedError


class CommandCompleter(_Surface):
    """The line-start menu: the command tree, walked by whitespace-split
    tokens, each row carrying its table description as the meta column. A
    `PATH_LEAF` node hands the argument to the path rows — this owns only
    WHEN that fires (behind an explicit `@`, immediately and while
    typing) and the raw-line slicing that lets spaces survive in paths."""

    def __init__(
        self,
        tree: CompletionTree,
        cast: Callable[[], Cast] | None = None,
        levels: Callable[[], Sequence[str]] | None = None,
        parameters: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        self.tree = tree
        # The story's cast, looked up live: (name, one-line description)
        # rows for the commands whose argument is a character.
        self.cast = cast or (lambda: ())
        # What /set think and /set parameter take on the model in use,
        # looked up live — the whole vocabulary with no session to ask.
        self.levels = levels or (lambda: THINK_MENU)
        self.parameters = parameters or (lambda: tuple(PARAMETERS))
        self.shortcuts: dict[str, str] = {}

    @staticmethod
    def applies(text_before_cursor: str) -> bool:
        return _opens_the_submission(text_before_cursor)

    @staticmethod
    def partial(text_before_cursor: str) -> str:
        """After a trailing space nothing is typed yet and the whole node
        is offered; otherwise the last whitespace-separated word filters
        it."""
        if text_before_cursor.endswith((" ", "\t")):
            return ""
        tokens = text_before_cursor.split()
        return tokens[-1] if tokens else ""

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        text = document.text_before_cursor
        tokens = list(re.finditer(r"\S+", text))
        walked = tokens if text.endswith((" ", "\t")) else tokens[:-1]
        path = [match.group(0) for match in walked]

        node: Any = self.tree
        arg_start: int | None = None
        for match in walked:
            if node in (PATH_LEAF, NAME_LEAF):
                break  # further tokens are argument text (spaces survive)
            token = match.group(0)
            if not isinstance(node, dict) or token not in node:
                return
            node = self._expanded(node[token])
            if node in (PATH_LEAF, NAME_LEAF):
                # The argument begins at the first non-space char after this
                # token — sliced from the raw line, so spaces survive.
                rest = text[match.end() :]
                arg_start = match.end() + (len(rest) - len(rest.lstrip()))
            if node is None:
                return

        if node == NAME_LEAF:
            if arg_start is not None:
                yield from _cast_rows(path[0], text[arg_start:], self.cast())
            return

        if node == PATH_LEAF:
            # Paths complete behind an explicit `@`: the menu pops the
            # moment it is typed and filters while typing. Never on a bare
            # path — a directory listing under every keystroke of ordinary
            # text would be noise. The handlers strip the `@`; it is a
            # trigger, not part of the name.
            if arg_start is not None and text[arg_start:].startswith("@"):
                yield from completions(text[arg_start + 1 :])
            return

        if not isinstance(node, dict):
            return

        menu = {key: _describe((*path, key)) for key in node}
        yield from _rows(menu, self.partial(text), tuple(path), self.shortcuts)

    def _expanded(self, node: Any) -> Any:
        """A leaf that asks the session, expanded to the menu it stands
        for the moment the walk steps onto it — so what follows (a
        parameter's `reset`) is walked like any node."""
        if node == THINK_LEAF:
            return {level: None for level in self.levels()}
        if node == PARAMETER_LEAF:
            return {name: {"reset": None} for name in self.parameters()}
        return node


class InlinerCompleter(_Surface):
    """The mid-line menu: the inliners a played line may close with.

    It applies while a slash token is being typed inside prose, and that
    token must open with a slash following whitespace — the same rule
    the syntax runs the inliner by. Ordinary prose therefore stays
    silent: in `and/or`, `he/she`, `24/08/2026` and `https://x.co/` the
    slash follows a non-space. Past a space the token is finished rather
    than being typed, and a line that opens with a slash is the other
    surface's."""

    def __init__(self, menu: dict[str, str]) -> None:
        self.menu = menu

    @staticmethod
    def applies(text_before_cursor: str) -> bool:
        return _inliner_token(text_before_cursor) is not None

    @staticmethod
    def partial(text_before_cursor: str) -> str:
        return _inliner_token(text_before_cursor) or ""

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        yield from _rows(self.menu, self.partial(document.text_before_cursor), ("…",), {})


class SlashCompleter(Completer):
    """The one completer the prompt registers; prompt_toolkit takes
    exactly one, so the choice between the two menus is made here rather
    than by either of them."""

    def __init__(self, surfaces: tuple[_Surface, ...], prefix: Callable[[], str]) -> None:
        self.surfaces = surfaces
        self.prefix = prefix

    @classmethod
    def build(
        cls,
        prefix: Callable[[], str] = lambda: "",
        cast: Callable[[], Cast] | None = None,
        shortcuts: dict[str, str] | None = None,
        levels: Callable[[], Sequence[str]] | None = None,
        parameters: Callable[[], Sequence[str]] | None = None,
    ) -> Self:
        """A completer over the shared command table. `prefix` supplies an
        open block's collected text — the line being typed is read in the
        context of the message it belongs to, or every continuation line
        would look like the start of one. `cast` answers with the story's
        characters, looked up live; `levels` and `parameters` with what
        /set think and /set parameter take on the model in use, likewise.
        `shortcuts` (token → caption, from `chat.bindings.SHORTCUTS` as
        data) fills the menu's key column."""
        command = CommandCompleter(_completion_tree(), cast, levels, parameters)
        command.shortcuts = shortcuts or {}
        return cls((command, InlinerCompleter(_inliner_menu())), prefix)

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        whole = Document(self.prefix() + document.text_before_cursor)
        for surface in self.surfaces:
            if surface.applies(whole.text_before_cursor):
                yield from surface.get_completions(whole, complete_event)
                return

    def partial(self, text_before_cursor: str) -> str | None:
        """The token being completed at the cursor, on whichever surface
        owns it; None when neither does and no menu belongs here. Empty
        means a menu belongs with nothing typed into it yet — the prompt
        anchors on the length, so empty anchors at the cursor."""
        whole = self.prefix() + text_before_cursor
        for surface in _SURFACES:
            if surface.applies(whole):
                return surface.partial(whole)
        return None


# The surfaces as line-level questions, which need no tree — asked in
# this order, and at most one answers.
_SURFACES: tuple[type[_Surface], ...] = (CommandCompleter, InlinerCompleter)


# ---------- the menus off the shared table ----------


def _completion_tree() -> CompletionTree:
    """The slash-completion tree over `backend.commands.COMMANDS`: one
    top-level key per line-opening row (the SYNTAX directions included —
    typed like commands, discoverable like them), the /set family nested
    by its second word, and this module's own shapes at the leaves."""
    tree: CompletionTree = {}
    for spec in COMMANDS:
        words = spec.token.split()
        if words[0] == "…":
            continue  # inliners: the other surface's menu
        if len(words) == 2:
            family = tree.setdefault(words[0], {})
            assert isinstance(family, dict)
            family[words[1]] = _subcommand_leaf(words[1])
            continue
        tree.setdefault(words[0], _command_leaf(spec))
    return tree


def _command_leaf(spec: CommandSpec) -> "CompletionTree | str | None":
    if spec.token in _PATH_COMMANDS:
        return PATH_LEAF
    if spec.token in _NAME_COMMANDS or spec.args.startswith("NAME"):
        return NAME_LEAF
    if spec.token == "/usage":
        return {"all": None}
    return None


def _subcommand_leaf(name: str) -> "CompletionTree | str | None":
    """The /set family's value menus."""
    if name == "think":
        return THINK_LEAF
    if name == "parameter":
        return PARAMETER_LEAF
    if name in ("verbose", "autocorrect", "notification"):
        return {"on": None, "off": None}
    return None


def _inliner_menu() -> dict[str, str]:
    """The mid-line menu: every inliner row of the shared table, keyed by
    its bare token — a table row is written behind a `…`, so `/ooc`
    inside a line and `/ooc` opening one keep their separate meanings
    without colliding."""
    return {
        spec.token.split()[1]: spec.description for spec in COMMANDS if spec.token.startswith("… ")
    }


def _spec_for(path: tuple[str, ...]) -> CommandSpec | None:
    """The table row a menu path names, matched on the token+args words'
    leading tokens — ("/set", "think") finds the "/set think" row,
    ("/set", "parameter", "temperature") still answers from it."""
    for spec in COMMANDS:
        words = f"{spec.token} {spec.args}".split()
        if len(words) >= len(path) and tuple(words[: len(path)]) == path:
            return spec
    return None


def _describe(path: tuple[str, ...]) -> str:
    spec = _spec_for(path)
    return spec.description if spec else ""


def _arguments(path: tuple[str, ...]) -> str:
    """What follows the path, spelled as the table spells it — ("/me",)
    → "NAME: PROMPT", ("/set",) → "think <level>". Shown beside the
    command, so the shape of the line is visible while it is being typed."""
    spec = _spec_for(path)
    if spec is None:
        return ""
    words = f"{spec.token} {spec.args}".split()
    return " ".join(words[len(path) :])


def _takes_argument(path: tuple[str, ...]) -> bool:
    """Whether anything can follow the row at `path` — a parameter or a
    subcommand, REQUIRED OR OPTIONAL. A bracketed follower counts: the
    row that takes it was chosen over the bare line, so the space is
    what the user is after, and Enter on the emptied line still sends."""
    spec = _spec_for(path)
    if spec is None:
        return False
    words = f"{spec.token} {spec.args}".split()
    return len(words) > len(path)


def _cast_rows(command: str, segment: str, cast: Cast) -> Iterator[Completion]:
    """The cast offered where a command takes a character, shaped for the
    command: `/me` inserts `Name:` and keeps going (the prompt follows),
    `/you` inserts the bare name (the line is then complete — the hint is
    the rarer option, a colon away), and `/merge` completes both sides of
    `A into B`. `segment` is the raw argument text, so names with spaces
    filter whole; past a `:` the argument is content, and no menu belongs
    there."""
    if command == "/merge":
        _, sep, rest = segment.partition(" into ")
        if sep:
            yield from _name_rows(cast, rest, suffix="", required=False)
        else:
            yield from _name_rows(cast, segment, suffix=" into", required=True)
        return
    if ":" in segment:
        return
    if command == "/me":
        yield from _name_rows(cast, segment, suffix=":", required=True)
    else:
        yield from _name_rows(cast, segment, suffix="", required=False)


def _name_rows(cast: Cast, typed: str, *, suffix: str, required: bool) -> Iterator[Completion]:
    """One row per matching cast member: the name shown plain with the
    description as the meta column, the inserted text carrying whatever
    the command's shape needs after it (`:`, ` into`)."""
    wanted = typed.casefold()
    width = max((len(name) for name, _ in cast), default=0)
    for name, about in cast:
        if name.casefold().startswith(wanted):
            yield MenuRow(
                name + suffix,
                start_position=-len(typed),
                display=name.ljust(width),
                display_meta=about or None,
                takes_argument=required,
            )


def _rows(
    menu: dict[str, str], partial: str, path: tuple[str, ...], shortcuts: dict[str, str]
) -> Iterator[Completion]:
    """Menu rows, filtered by what is typed. Each row reads as the line
    it starts: the command, then what it takes — dimmed, because only the
    command is inserted — and then the key that runs it without typing at
    all, in a column of its own so the keys read down the menu as a list.
    Every width is measured over the WHOLE menu, not the filtered subset,
    so no column moves as the filter narrows it."""
    labels = {key: f"{key} {_arguments((*path, key))}".rstrip() for key in menu}
    keys = {key: shortcuts.get(" ".join((*path, key)), "") for key in menu}
    key_width = max((len(label) for label in labels.values()), default=0)
    shortcut_width = max((len(text) for text in keys.values()), default=0)
    meta_width = max((len(meta) for meta in menu.values()), default=0)
    for key, meta in menu.items():
        if key.startswith(latin_key(partial)):
            yield MenuRow(
                key,
                start_position=-len(partial),
                display=[
                    ("", key),
                    ("dim", labels[key][len(key) :]),
                    ("", " " * (key_width - len(labels[key]))),
                    ("dim", f"  {keys[key].ljust(shortcut_width)}" if shortcut_width else ""),
                ],
                display_meta=meta.ljust(meta_width) if meta_width else None,
                takes_argument=_takes_argument((*path, key)),
            )


def _opens_the_submission(text_before_cursor: str) -> bool:
    """Whether the cursor sits on a slash that opens the whole submission —
    the only place a command can be typed. A newline before it means the
    line is a continuation inside a `\"\"\"` block, where the message has
    already begun and only an inliner can follow."""
    return "\n" not in text_before_cursor and text_before_cursor.lstrip().startswith("/")


def _inliner_token(text_before_cursor: str) -> str | None:
    """The inliner token being typed at the cursor, its `/` included:
    `she looks up /c` → `"/c"`. None when the cursor is not in one."""
    if text_before_cursor.endswith((" ", "\t")):
        return None
    if _opens_the_submission(text_before_cursor):
        return None
    tokens = list(re.finditer(r"\S+", text_before_cursor))
    if not tokens or not tokens[-1].group(0).startswith("/"):
        return None
    return tokens[-1].group(0)


# ---------- filesystem paths behind `@` ----------


def completions(prefix: str) -> Iterator[Completion]:
    """The menu rows for a partly typed path: `matches` over the entries
    of the directory `prefix` sits in (`~` expanded for the listing only;
    an unreadable directory offers nothing)."""
    base, fragment = split(prefix)
    directory = Path(base).expanduser() if base else Path(".")
    try:
        entries = [(entry.name, entry.is_dir()) for entry in directory.iterdir()]
    except OSError:
        return
    names = matches(entries, fragment)
    width = max((len(name) for name in names), default=0)
    for name in names:
        yield Completion(name, start_position=-len(fragment), display=name.ljust(width))


def split(prefix: str) -> tuple[str, str]:
    """(directory part, fragment) of a partly typed path: everything
    through the final `/` — kept exactly as typed, `~` unexpanded, spaces
    intact — and the segment being completed after it. No `/` yet:
    ("", prefix)."""
    at = prefix.rfind("/")
    if at < 0:
        return "", prefix
    return prefix[: at + 1], prefix[at + 1 :]


def matches(entries: Iterable[tuple[str, bool]], fragment: str) -> list[str]:
    """Display names among `entries` — (name, is_dir) pairs — completing
    `fragment`: prefix-matched, hidden entries only when the fragment
    itself starts with a dot, directories with a trailing `/`, ordered by
    casefolded name."""
    hidden_wanted = fragment.startswith(".")
    return [
        name + ("/" if is_dir else "")
        for name, is_dir in sorted(entries, key=lambda entry: entry[0].casefold())
        if name.startswith(fragment) and (hidden_wanted or not name.startswith("."))
    ]

"""What one submitted line is assembled from: `Carry`, what a shortcut
hands across the prompt's exit, and `LineAssembler`, the `\"\"\"`
multiline convention — a line starting with `\"\"\"` opens a block that
spans lines until a closing `\"\"\"` ends one, the text between the
delimiters (newlines preserved) sent as a single message."""

from otaku.terminal.tty.render import command_tokens

_TRIPLE = '"""'


class Carry:
    """What a shortcut keybinding hands the run loop across the prompt's
    exit: the fact that a shortcut submitted the line, and the in-progress
    text to restore at the next prompt (still readable after `take_text`
    consumed it as the default — the shortcut branch erases exactly what
    was shown)."""

    def __init__(self) -> None:
        self.text = ""
        self.shortcut = False
        # PROTOTYPE: the canvas's keys are in force — Ctrl+Enter (or Ctrl+J)
        # a paragraph, Enter the send as everywhere (`keys.make_bindings`)
        self.canvas_keys = False
        # PROTOTYPE: the prompt stands in the story's text; `/` at the start
        # of an empty paragraph there leaves it for the COMMAND PARAGRAPH,
        # a prompt of its own under the text (`to_command`, what stood in
        # the text in `shown`, the story typed so far kept as `draft`);
        # Esc or Backspace on the bare slash leaves that for the text again
        # (`from_command`). A canvas prompt outside the text — an empty
        # story's, one under a command's output — leaves for it the same
        # way, its own row erased
        self.in_text = False
        self.command_paragraph = False
        self.to_command = False
        self.from_command = False
        self.shown = ""
        self.draft = ""
        # PROTOTYPE: the canvas's walk through the input history — ONE walk
        # over both places, each entry shown where it would have been typed
        # (a command on the command paragraph, a story line in the text),
        # the prompt handing over to the other place as the walk crosses
        # from one kind to the other: `walk` the entry shown (-1 the
        # reader's own draft), `walk_draft` that draft and
        # `walk_draft_command` the place it was taken from, `recall` the
        # entry a hand-over gives the next prompt
        self.walk = -1
        self.walk_draft = ""
        self.walk_draft_command = False
        self.recall: str | None = None
        # The line stands as the history gave it, nothing typed on it
        # since: the menu does not open on its own there, so no rows are
        # kept free under the line for it (`session.build_prompt`)
        self.recalled = False

    def end_walk(self) -> None:
        """A line was sent, or dropped: the next walk starts from the
        newest entry again."""
        self.walk = -1
        self.walk_draft = ""
        self.walk_draft_command = False
        self.recall = None
        self.recalled = False

    def take_text(self) -> str:
        text, self.text = self.text, ""
        return text

    def take_shortcut(self) -> bool:
        taken, self.shortcut = self.shortcut, False
        return taken


class LineAssembler:
    """Assembles triple-quoted multiline input, one line at a time.

    A block is a way to press Enter without submitting, and nothing more:
    what it collects is an ordinary prompt, read for its framing syntax
    like any other and told apart from a typed line by nothing downstream.

    Feed each input line via `feed()`. It returns None while a `\"\"\"` block
    is still open (the caller keeps prompting with the continuation
    prompt), otherwise the message. The one difference between the two
    cases is settled HERE, where it is known: a typed line is stripped, and
    a block keeps the whitespace at its edges — someone who opened a block
    to lay text out meant the layout. `verbatim` keeps a typed line as it
    is, both edges: the canvas's, where the whitespace it opens and ends
    with are its seams (`backend.story.play.submit_line`)."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self.in_block = False

    def feed(self, line: str, *, verbatim: bool = False) -> str | None:
        if self.in_block:
            before, closed = _cut_suffix(line, _TRIPLE)
            self._lines.append(before)
            if not closed:
                return None  # closing delimiter not seen yet — keep collecting
            text = "\n".join(self._lines)
            self.reset()
            return text
        head, opened = _cut_opener(line)
        if opened:
            rest, closed = _cut_suffix(line[len(head) + len(_TRIPLE) :], _TRIPLE)
            if closed:
                return head + rest  # a whole block on one line
            self._lines = [head + rest]
            self.in_block = True
            return None
        return line if verbatim else line.strip()

    @property
    def prefix(self) -> str:
        """What an open block has collected, with the newline the next line
        will follow — so the completer can read the line being typed in the
        context of the message it belongs to. Empty when no block is open.

        Without it every continuation line looks like the start of a
        submission, and a `/` typed on one would open the command menu
        where only an inliner can go."""
        if not self.in_block:
            return ""
        return "\n".join(self._lines) + "\n"

    def reset(self) -> None:
        """Drop any partial block (used on Ctrl+C)."""
        self._lines = []
        self.in_block = False


def _cut_opener(line: str) -> tuple[str, bool]:
    """The text a `\"\"\"` opener follows, and whether the line opens a block
    at all. Usually nothing precedes it — but a COMMAND may (`/system
    \"\"\"`), because a multiline argument is the obvious reason to want a
    block, and the delimiters would otherwise land in that argument as
    text. The command is kept and the quotes dropped, so the block reads
    as the one long line the writer meant to type.

    Only a real command opens one, and only with the quotes right after
    it: `/ooc the docstring is \"\"\"x\"\"\"` is prose about quotes, and its
    line has to survive as typed."""
    if line.startswith(_TRIPLE):
        return "", True
    head, quotes, _ = line.partition(_TRIPLE)
    if quotes and head.rstrip(" ") in command_tokens():
        return head, True
    return line, False


def _cut_suffix(text: str, suffix: str) -> tuple[str, bool]:
    if text.endswith(suffix):
        return text[: -len(suffix)], True
    return text, False

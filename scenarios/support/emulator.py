"""The real binary in a pty with its screen EMULATED (pyte): the rows a
terminal would show, frame by frame — for the stories whose subject is the
screen itself, where `Terminal.expect` over the stripped transcript cannot
tell a row erased from a row left behind.

The model is the test model's (`scripts/test_model.py`): a line's leading
digits choose the reply's blocks — thinking, a note, a question, the two
tools, a long reply — so every block can be played; `-1` fails every time
here, where the test model fails half the time. Two readings judge a
screen: `mismatch`, the live screen against what a FRESH LAUNCH echoes of
the same story (`echo`), and `jumps`, the rows above the line being
written that changed while a reply streamed.
"""

import functools
import importlib.util
import itertools
import os
import select
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pyte

from otaku.backend import Layout
from otaku.backend import launch as backend_launch
from otaku.backend.api import play, providers, settings, stories
from scenarios.support.harness import set_config_provider
from scenarios.support.server import ModelServer
from scenarios.support.terminal import Terminal

ROWS, COLS = 60, 80
ENTER = b"\r"
CTRL_C = b"\x03"
CTRL_D = b"\x04"
CTRL_J = b"\n"
CTRL_R = b"\x12"
CTRL_U = b"\x15"
DOWN = b"\x1b[B"
RIGHT = b"\x1b[C"
# Ctrl+Enter as a terminal honouring modifyOtherKeys reports it
CTRL_ENTER = b"\x1b[27;5;13~"

_CPR = b"\x1b[6n"
_ROOT = Path(__file__).resolve().parents[2]


def load_test_model() -> ModuleType:
    """`scripts/test_model.py`, loaded by path: a script, not a package."""
    spec = importlib.util.spec_from_file_location("test_model", _ROOT / "scripts" / "test_model.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TM = load_test_model()


class XtermScreen(pyte.Screen):
    """pyte, restoring a saved cursor as xterm does: clamped to the margins
    in origin mode alone. pyte clamps it always, which puts the cursor a
    row high once the status row's scroll region is set from the last row
    (`statusline.StatusLine.open`) — a row the real terminal never loses."""

    def restore_cursor(self) -> None:
        if not self.savepoints:
            super().restore_cursor()
            return
        saved = self.savepoints.pop()
        self.g0_charset, self.g1_charset, self.charset = (
            saved.g0_charset,
            saved.g1_charset,
            saved.charset,
        )
        if saved.origin:
            self.set_mode(pyte.modes.DECOM)
        if saved.wrap:
            self.set_mode(pyte.modes.DECAWM)
        self.cursor = saved.cursor
        self.ensure_hbounds()
        self.ensure_vbounds(use_margins=pyte.modes.DECOM in self.mode)


# the rows, the cursor's row, when it was drawn, and where the cursor shows
# (row, column) or None while hidden
Frame = tuple[tuple[str, ...], int, float, tuple[int, int] | None]


class Emulated(Terminal):
    """`Terminal` with its output fed through an emulated screen, which
    answers the app's cursor-position queries from where its cursor is.
    `recording` keeps every distinct frame the screen passed through."""

    def __init__(self, root: Path) -> None:
        super().__init__(str(root), rows=ROWS, cols=COLS)
        self.root = root
        self.screen = XtermScreen(COLS, ROWS)
        self._stream = pyte.ByteStream(self.screen)
        self._frames: list[Frame] | None = None
        self._last_byte = time.time()

    def _drain(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self._master], [], [], 0.03)
            if not ready:
                continue
            try:
                chunk = os.read(self._master, 65536)
            except OSError:
                return
            self._last_byte = time.time()
            self._raw += chunk
            while _CPR in chunk:
                head, _, chunk = chunk.partition(_CPR)
                self._feed(head)
                y, x = self.screen.cursor.y, self.screen.cursor.x
                os.write(self._master, b"\x1b[%d;%dR" % (y + 1, x + 1))
            self._feed(chunk)

    def _feed(self, data: bytes) -> None:
        self._stream.feed(data)
        if self._frames is not None:
            cursor = self.screen.cursor
            rows, shown = (
                tuple(self.screen.display),
                None if cursor.hidden else (cursor.y, cursor.x),
            )
            last = self._frames[-1] if self._frames else None
            if last is None or (last[0], last[1], last[3]) != (rows, cursor.y, shown):
                self._frames.append((rows, cursor.y, time.monotonic(), shown))

    def quiet(self, still: float = 1.2, timeout: float = 60.0) -> None:
        """Wait until nothing has been drawn for `still` seconds."""
        start = time.time()
        self._drain(0.3)
        while time.time() - start < timeout:
            self._drain(0.2)
            if time.time() - self._last_byte > still:
                return

    @contextmanager
    def recording(self) -> Iterator[list[Frame]]:
        """Every distinct frame drawn inside the block."""
        frames: list[Frame] = []
        self._frames = frames
        try:
            yield frames
        finally:
            self._frames = None

    def lines(self) -> list[str]:
        return [line.rstrip() for line in self.screen.display]

    @property
    def cursor(self) -> tuple[int, int]:
        return self.screen.cursor.y, self.screen.cursor.x

    def kill(self) -> None:
        """End a process already on its way out (Ctrl+D sent)."""
        self._proc.kill()
        os.close(self._master)


def model_server() -> ModelServer:
    """The test model on the scripted server: both models, their windows
    known, its replies, `-1` refused."""
    server = ModelServer(models=(TM.CHAT_MODEL, TM.TEXT_MODEL))
    server.router = True
    server.loaded = {TM.CHAT_MODEL, TM.TEXT_MODEL}
    server.window = TM.WINDOW
    server.contexts.update({TM.CHAT_MODEL: TM.WINDOW, TM.TEXT_MODEL: TM.WINDOW})
    server.template_reads = set()
    server.chunk_size = 6
    server.chunk_delay = 0.01
    server.script = TM.reply
    server.refuse = _refuse
    return server


def _refuse(body: dict[str, Any]) -> int | None:
    """500 for a line whose number is -1 — every time."""
    messages = body.get("messages") or []
    last = messages[-1] if messages else {}
    line = TM.content_text(last) if last.get("role") == "user" else ""
    return 500 if line.rstrip().rsplit("\n\n", 1)[-1].lstrip().startswith("-1") else None


def story(
    root: Path,
    server: ModelServer,
    layout: Layout,
    lines: tuple[str, ...] = ("The ferry left at dawn.",),
    *,
    verbose: bool = False,
) -> None:
    """A story seeded through the backend: the chat model chosen, every
    tool on and the notes shown, `lines` played in `layout`."""
    set_config_provider(root, server, name="llamacpp")
    session = backend_launch.open_session(root)
    try:
        providers.set_model(session, "llamacpp", TM.CHAT_MODEL)
        stories.update_setting(session, "layout", layout=layout)
        for line in lines[:1]:
            for _ in play.submit(session, line):
                pass
        # the story exists once its first line is played: its own settings now
        for name in ("allow_questions", "allow_system_info", "allow_web_search"):
            stories.update_setting(session, name, enabled=True)
        stories.update_setting(session, "allow_assistant_notes", enabled=True, display_notes=True)
        for line in lines[1:]:
            for _ in play.submit(session, line):
                pass
        if verbose:
            settings.set_verbose(session, "on")
    finally:
        session.close()


def fresh_root() -> Path:
    return Path(tempfile.mkdtemp()) / "state"


def echo(root: Path) -> tuple[list[str], tuple[int, int]]:
    """What a fresh launch shows of the story: its rows and its cursor."""
    other = Emulated(root)
    try:
        other.quiet(still=1.5)
        return other.lines(), other.cursor
    finally:
        other.quit()


def mismatch(live: Emulated, *, story_mode: bool) -> list[str]:
    """Where the live screen and a fresh launch's echo of the same story
    disagree, read back from the cursor through the echoed turns: row by
    row in chat mode, the rows the store never holds left out of the live
    screen; word by word in story mode, whose one text wraps wherever its
    echo began."""
    rows, (ey, ex) = echo(live.root)
    start = next((i for i, row in enumerate(rows) if "Resumed at message" in row), -1) + 2
    lines, (ly, lx) = live.lines(), live.cursor
    if story_mode:
        theirs = _words([*rows[start:ey], rows[ey][:ex]])
        ours = _words([*lines[:ly], lines[ly][:lx]])
        if ours[-len(theirs) :] == theirs:
            return []
        k = 1
        while k <= min(len(theirs), len(ours)) and ours[-k] == theirs[-k]:
            k += 1
        return [
            f"word {k} from the end: live {' '.join(ours[-k - 6 : len(ours) - k + 3])!r}"
            f" | echo {' '.join(theirs[-k - 6 : len(theirs) - k + 3])!r}"
        ]
    kept, ly = _stored_rows(lines, ly)
    out = []
    if kept[ly][:lx] != rows[ey][:ex]:
        out.append(f"cursor row: live {kept[ly][:lx]!r} | echo {rows[ey][:ex]!r}")
    k = 1
    while ey - k >= start and ly - k >= 0:
        if kept[ly - k] != rows[ey - k]:
            out.append(f"row -{k}: live {kept[ly - k]!r} | echo {rows[ey - k]!r}")
        k += 1
    return out


def _stored_rows(lines: list[str], cursor_row: int) -> tuple[list[str], int]:
    """`lines` without what the store never holds — the thinking's rows,
    an error's line, the verbose stats line — and the blank a block of
    them stood on; the cursor's row moved up with them."""
    kept: list[str] = []
    cut = 0
    dropping = False
    for i, line in enumerate(lines):
        if i >= cursor_row:
            kept.append(line)
            continue
        thinking = line.startswith("│") and (
            dropping or (len(line) > 6 and any(line[2:32] in t for t in TM.THOUGHTS))
        )
        unstored = thinking or line.startswith(("[ error:", "[ total"))
        if unstored or (dropping and not line.strip()):
            dropping = thinking or line.startswith("[ error:")
            cut += 1
            continue
        dropping = False
        kept.append(line)
    return kept, cursor_row - cut


def _words(lines: list[str]) -> list[str]:
    """The text the rows hold: a full row read as wrapped onto the next,
    a blank row as a paragraph mark."""
    text = ""
    for line in lines:
        text += " ¶ " if not line.strip() else line + ("" if len(line) >= COLS else " ")
    return text.split()


def jumps(frames: list[Frame]) -> list[str]:
    """Rows of finished text — above the line being written in both
    frames — that changed from one frame to the next other than by the
    screen scrolling."""
    found = []
    for (a, ay, _, _), (b, by, _, _) in itertools.pairwise(frames):
        shift = max(range(12), key=functools.partial(_kept, a, b))
        for i in range(max(0, min(ay - shift, by) - 1)):
            if a[i + shift].strip() and a[i + shift] != b[i]:
                found.append(f"row {i}: {a[i + shift].rstrip()!r} became {b[i].rstrip()!r}")
    return found


def _kept(a: tuple[str, ...], b: tuple[str, ...], shift: int) -> int:
    """How many rows of text `a` holds that `b` holds `shift` rows up."""
    return sum(1 for i in range(ROWS - shift) if a[i + shift].strip() and a[i + shift] == b[i])


def vanished(frames: list[Frame], text: str, *, after: int = 0) -> list[int]:
    """The frames from `after` on that showed no row holding `text`, and
    stood long enough to be seen — a write the pty hands over in pieces
    shows a frame for microseconds, which no terminal paints."""
    shown = 0.005
    return [
        i
        for i, (rows, _, at, _) in enumerate(frames)
        if i >= after
        and not any(text in row for row in rows)
        and (frames[i + 1][2] if i + 1 < len(frames) else time.monotonic()) - at >= shown
    ]

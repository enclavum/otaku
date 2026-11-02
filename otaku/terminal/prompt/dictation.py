"""Dictation at the prompt: Ctrl+G records what the reader says, Enter or
Esc stops it, and the model saved for dictation writes it down
(`api.app.dictation`), the text going in at the cursor. While the text
is on its way the line cannot be sent, so it never leaves without it.
While it records, the line says so — faint at the cursor, or in place
of an empty line's placeholder (`ListeningHint`, `placeholder`) — and
the status row how to stop; while the text is on its way, the status
row says that (`note`). A prompt that
ends first — Ctrl+C, a shortcut, Ctrl+D — releases the microphone and
drops what was still on its way."""

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import AnyFormattedText, FormattedText
from prompt_toolkit.formatted_text.utils import fragment_list_len
from prompt_toolkit.layout.processors import Processor, Transformation, TransformationInput
from prompt_toolkit.layout.utils import explode_text_fragments

from otaku.terminal.api import Api, Refused
from otaku.terminal.tty.microphone import BAND_COUNT, MicrophoneError, Recording
from otaku.terminal.tty.spinner import FRAMES

# What the line says while the reader speaks: at the cursor of a line
# with text, and in place of an empty line's placeholder.
_LISTENING = "listening… "
_LISTENING_PLACEHOLDER = FormattedText([("class:placeholder", "Listening…")])
# The status row's words while the reader speaks, and while the text is on
# its way: the transient fragments a worker updates in place.
_RECORDING_NOTE = "enter or esc to stop"
_WRITING_NOTE = "transcribing…"
# The row moves while it works: an equalizer — how loud each band of pitch
# is now, low to high, a bar falling back by `_FALL` a tick — and the
# time spoken while the reader speaks, the spinner while the text is on
# its way.
_BARS = "▁▂▃▄▅▆▇█"
_FALL = 0.2
_TICK = 0.1  # seconds
# How long a refusal stands in the row.
_SAID_SECONDS = 4.0


class Dictation:
    """One prompt's dictation, between the keys that drive it (`start`,
    `stop`) and what shows it (`ListeningHint`, `placeholder`, `note`).
    Built once and kept across prompts; each recording belongs to the
    prompt it began in."""

    def __init__(self, api: Api) -> None:
        self._api = api
        self._recording: Recording | None = None
        self._is_writing = False
        self._said = ""
        self._said_timer: threading.Timer | None = None
        # Bumped whenever a prompt's dictation is dropped, so text that
        # lands after it knows it is too late.
        self._turn = 0
        self._levels = [0.0] * BAND_COUNT
        self._frame = 0
        self._started = 0.0  # when the recording began, on the monotonic clock

    @property
    def is_recording(self) -> bool:
        return self._recording is not None

    @property
    def is_busy(self) -> bool:
        """Recording, or the text still on its way: the line is not sent."""
        return self._recording is not None or self._is_writing

    @property
    def note(self) -> str:
        """What the status row says of it: "" for nothing."""
        if self._recording is not None:
            top = len(_BARS) - 1
            meter = "".join(_BARS[min(top, int(level * len(_BARS)))] for level in self._levels)
            minutes, seconds = divmod(int(time.monotonic() - self._started), 60)
            return f"{meter} {minutes}:{seconds:02d} · {_RECORDING_NOTE}"
        if self._is_writing:
            return f"{FRAMES[self._frame % len(FRAMES)]} {_WRITING_NOTE}"
        return self._said

    def placeholder(self, standing: AnyFormattedText) -> Callable[[], AnyFormattedText]:
        """The empty line's placeholder: "Listening…" while a recording
        runs, `standing` otherwise."""
        return lambda: _LISTENING_PLACEHOLDER if self._recording is not None else standing

    def start(self, app: Application[Any]) -> None:
        """Start recording — once the backend says a model will write it
        down, and the microphone opened; a refusal stands in the row
        instead. The prompt's end releases it (`_drop`)."""
        if self.is_busy or app.future is None:
            return
        try:
            self._api.app.dictation.create(validate_only=True)
            self._recording = Recording()
        except (Refused, MicrophoneError) as e:
            self._say(app, str(e))
            return
        self._said = ""
        turn = self._turn
        app.future.add_done_callback(lambda _future: self._drop(turn))
        self._animate(app, turn)

    def stop(self, app: Application[Any]) -> None:
        """Stop recording and have it written down on a thread of its
        own, the text landing at the cursor on the prompt's loop."""
        recording, loop = self._recording, app.loop
        if recording is None or loop is None:
            return
        self._recording = None
        audio = recording.stop()
        self._is_writing = True
        turn = self._turn

        def write_down() -> None:
            text, said = "", ""
            try:
                text = self._api.app.dictation.create(audio=audio, audio_format="wav").value.text
            except Refused as e:
                said = str(e)

            def land() -> None:
                if turn != self._turn:
                    return  # the prompt it was said in has ended
                self._is_writing = False
                if said:
                    self._say(app, said)
                elif text:
                    _insert(app.current_buffer, text)
                app.invalidate()

            with contextlib.suppress(RuntimeError):  # the loop closed with its prompt
                loop.call_soon_threadsafe(land)

        threading.Thread(target=write_down, daemon=True).start()

    def _animate(self, app: Application[Any], turn: int) -> None:
        """The row redrawn every tick for as long as this dictation works:
        the equalizer takes the microphone's bands, the spinner its next
        frame. On a thread of its own; the redraw is the prompt's."""
        self._levels = [0.0] * BAND_COUNT
        self._frame = 0
        self._started = time.monotonic()

        def tick() -> None:
            while turn == self._turn and self.is_busy:
                time.sleep(_TICK)
                recording = self._recording
                heard = recording.take_bands() if recording is not None else [0.0] * BAND_COUNT
                self._levels = [
                    max(now, before - _FALL)
                    for now, before in zip(heard, self._levels, strict=True)
                ]
                self._frame += 1
                app.invalidate()

        threading.Thread(target=tick, daemon=True).start()

    def _drop(self, turn: int) -> None:
        """The prompt `turn` began in has ended: the microphone released,
        and what is on its way dropped."""
        if turn != self._turn:
            return
        self._turn += 1
        if self._recording is not None:
            self._recording.discard()
            self._recording = None
        self._is_writing = False

    def _say(self, app: Application[Any], sentence: str) -> None:
        """`sentence` in the row for `_SAID_SECONDS`."""
        if self._said_timer is not None:
            self._said_timer.cancel()
        self._said = sentence

        def clear() -> None:
            if self._said == sentence:
                self._said = ""
                app.invalidate()

        self._said_timer = threading.Timer(_SAID_SECONDS, clear)
        self._said_timer.daemon = True
        self._said_timer.start()
        app.invalidate()


class ListeningHint(Processor):
    """ "listening… " drawn faint after the cursor of a line with text
    while a recording runs — where the text will land, set off from a
    word as the text will be (`_insert`) — and never part of the
    line."""

    def __init__(self, dictation: Dictation) -> None:
        self._dictation = dictation

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        document = ti.document
        if (
            not self._dictation.is_recording
            or not document.text
            or ti.lineno != document.cursor_position_row
        ):
            return Transformation(ti.fragments)
        fragments = explode_text_fragments(ti.fragments)
        at = min(ti.source_to_display(document.cursor_position_col), fragment_list_len(fragments))
        before = document.char_before_cursor
        hint = f" {_LISTENING}" if before and not before.isspace() else _LISTENING
        width = len(hint)
        return Transformation(
            [*fragments[:at], ("class:placeholder", hint), *fragments[at:]],
            source_to_display=lambda i: i if i <= at else i + width,
            display_to_source=lambda i: i if i <= at else max(at, i - width),
        )


def _insert(buffer: Buffer, text: str) -> None:
    """`text` at the cursor, a space between it and a word it would
    otherwise run into."""
    before = buffer.document.char_before_cursor
    buffer.insert_text(f" {text}" if before and not before.isspace() else text)

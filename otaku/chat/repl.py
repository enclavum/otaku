"""The chat loop: prompt → submission → reply, and the session chrome.

`run` owns the loop and the spacing around submissions; `submit` owns
what one submitted line means (a command dispatches, anything else plays
as a turn) and contains its crashes. The prompt itself — prompt_toolkit,
keybindings, the command menu, multiline assembly — lives in
chat/prompt.py; what the exchanges occupy on screen and how /undo and
/regen take them back lives in chat/screen.py.

Every submission ends with one blank line before the next prompt — the
gap lives HERE, once, not at each print site. The typed line counts as
output (it stays on screen); a shortcut's erased prompt line does not,
so a picker cancelled without a word leaves the screen exactly as it
was. After an erased /undo (or a /you nothing answered) the standing
blank is already on screen, and the ledger suppresses the loop's own.
"""

import sys
from typing import Any, TextIO, cast

from prompt_toolkit.formatted_text import FormattedText

from otaku import __version__
from otaku.chat.commands import dispatch
from otaku.chat.commands.lore import build_job
from otaku.chat.inference import run_inference
from otaku.chat.prompt import (
    CLOUD_PLACEHOLDER,
    PLACEHOLDER,
    Carry,
    LineAssembler,
    build_prompt,
)
from otaku.chat.session import RESUME_TURNS, Session
from otaku.formatting import flatten, pretty_path, truncate
from otaku.logs.errors import ErrorLog
from otaku.store import Store
from otaku.store.schema import Message
from otaku.terminal import (
    BOLD,
    CURSOR_BLINK_ON,
    PROMPT_CONTINUATION,
    PROMPT_PREFIX,
    RESET,
    banner,
)
from otaku.terminal.cursor import measure, terminal_width
from otaku.terminal.statusline import StatusLine


class _OutputTracker:
    """A sys.stdout stand-in that remembers whether anything was written.
    The full-screen surfaces don't register: prompt_toolkit writes through
    its own Output object, bound to the real stream before any tracker
    exists — exactly the split the prompt-gap rule needs."""

    def __init__(self, wrapped: TextIO) -> None:
        self.wrapped = wrapped
        self.wrote = False

    def write(self, text: str) -> int:
        if text:
            self.wrote = True
        return self.wrapped.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)


def run(session: Session, store: Store) -> None:
    """The chat loop: banner, then prompt → command or model turn, until
    /bye, Ctrl+D, or EOF."""
    # Some terminals (Ghostty) need the explicit blink opt-in on top of the
    # DECSCUSR shape escape prompt_toolkit emits.
    sys.stdout.write(CURSOR_BLINK_ON)
    sys.stdout.flush()

    if session.config.show_banner:
        print(_banner(session, store))
    _show_resumed(session, store)
    if session.notice:
        # The resume echo above always precedes the notice and ends with
        # its own blank line. The notice sits below the echoed turns, so
        # they are no longer erasable.
        print(f"{BOLD}{session.notice}{RESET}")
        print()
        session.notice = ""
        session.screen.invalidate()

    carry = Carry()
    prompt_session = build_prompt(session, store, carry)
    worker = session.worker
    # One status callback, two surfaces: the prompt's toolbar while the
    # prompt is up, the pinned bottom row while a reply streams. Each is a
    # no-op when it isn't the live one; `invalidate()` is prompt_toolkit's
    # thread-safe repaint trigger.
    session.status_line = StatusLine(worker.get_status)

    def repaint() -> None:
        if session.status_line is not None:
            session.status_line.refresh()
        if prompt_session.app.is_running:
            prompt_session.app.invalidate()

    worker.on_status = repaint
    # Typing is activity: every buffer change pushes a pending pass back a
    # full idle window, so it starts on REAL idle, not mid-composition.
    prompt_session.default_buffer.on_text_changed += lambda _buf: worker.touch()
    worker.start()

    assembler = LineAssembler()

    # Terminal rows the CURRENT submission's input occupies on screen —
    # accumulated across a """ block's prompts — so a played turn can be
    # erased and re-echoed as the grey submitted-turn block.
    input_rows = 0

    while not session.should_quit:
        prefix = PROMPT_CONTINUATION if assembler.in_block else PROMPT_PREFIX
        placeholder = None if assembler.in_block else _placeholder(session)
        try:
            line = prompt_session.prompt(prefix, placeholder=placeholder, default=carry.take_text())
        except EOFError:
            session.should_quit = True
            break
        except KeyboardInterrupt:
            # ^C clears the line; inside a """ block it also drops the
            # buffer. The aborted prompt line stays on screen as a row the
            # ledger cannot measure, so erasing is off until the next play.
            assembler.reset()
            input_rows = 0
            session.screen.invalidate()
            continue

        if carry.take_shortcut():
            # A shortcut key exited the prompt with its command as the
            # result; it is always a command — even mid-"""-block, where
            # feeding it to the assembler would paste "/regen" into the
            # user's text. The shown line is erased (its text returns at
            # the next prompt), so the submission occupies no screen rows;
            # an open block's collected lines above it are composition the
            # ledger cannot see past.
            message, is_raw, shown = line, False, False
            session.screen.typed_rows = 0
            if assembler.in_block:
                session.screen.invalidate()
            sys.stdout.write(f"\x1b[{_rows_on_screen(carry.text, prefix)}A\r\x1b[J")
        else:
            input_rows += _rows_on_screen(line, prefix)
            result = assembler.feed(line)
            if result is None:
                continue  # inside an open """ block — keep collecting lines
            text, is_raw = result
            message = text if is_raw else text.strip()
            if not message:
                # A bare Enter leaves its prompt row on screen; its rows
                # stay in input_rows so the next submission's erase takes
                # it too.
                continue
            shown = True
            session.screen.typed_rows, input_rows = input_rows, 0

        tracker = _OutputTracker(sys.stdout)
        sys.stdout = cast(TextIO, tracker)
        try:
            submit(message, session, store, raw=is_raw)
        except KeyboardInterrupt:
            # ^C during streaming or a picker: return to the prompt
            # cleanly. What it left mid-row is not the ledger's to count.
            print()
            session.screen.invalidate()
        finally:
            sys.stdout = tracker.wrapped
        if (shown or tracker.wrote) and not session.screen.take_suppressed_gap():
            print()  # the systematic gap before the prompt (see module doc)

    worker.shutdown()  # non-blocking: exit is immediate


# ---------- session chrome ----------


def submit(line: str, session: Session, store: Store, *, raw: bool = False) -> None:
    """One submitted line, whatever surface it came from: the user is
    active again, so queued background work is dropped; a slash command
    dispatches — never for a `raw` block, which is always a literal
    prompt — and anything else echoes as the grey played-turn block, is
    recorded as the user's turn, and the model answers. A new model turn
    arms the idle-debounced lore pass: it fires while the user reads and
    dies the moment they type.

    A crash is contained here, at the line boundary: the traceback goes
    to the error log, one short line says so, and the session lives on —
    every store write is transactional, so nothing is half-done. The
    error line prints past the screen ledger, so it invalidates it."""
    session.worker.defer()
    last_before = session.messages[-1] if session.messages else None
    try:
        if raw or not dispatch(line, session, store):
            session.screen.echo_block(line)
            session.record_turn(store, Message(role="user", body=line))
            run_inference(session, store)
        _maybe_schedule(session, last_before)
    except KeyboardInterrupt:
        raise  # ^C is the user speaking, not a crash — the loop handles it
    except Exception as e:
        if session.screen.take_lead_blank():
            print()  # the crash report is the command's first output
        session.screen.invalidate()
        path = ErrorLog(session.paths).record(f"command {line.split(' ', 1)[0]!r}", e)
        print(f"command failed ({type(e).__name__}) — recorded in {pretty_path(path)}")


def _maybe_schedule(session: Session, last_before: Message | None) -> None:
    """Arm an idle-debounced pass when a NEW model turn just landed — plain
    messages, /regen, /ooc, /you, and /me all produce them. Identity (not
    length) detects it: regenerate swaps the last message without changing
    the count. [lore_extraction].enabled gates THIS — the automatic
    scheduling — and nothing else; /extract still works."""
    if not session.config.lore_enabled or session.provider is None:
        return
    if session.story_id is None or not session.messages:
        return
    last = session.messages[-1]
    if last is not last_before and last.role == "assistant":
        session.worker.schedule(build_job(session))


def _rows_on_screen(line: str, prefix: str) -> int:
    """Terminal rows one prompt read occupied: the prompt prefix plus the
    line — wrapping at the terminal's width and embedded newlines (a
    recalled multiline entry) included."""
    return measure(prefix + line + "\n", terminal_width())


def _placeholder(session: Session) -> FormattedText:
    """The idle prompt's hint — the cloud wording when the story is
    played against a hosted catalog, so every turn quietly says the text
    leaves the machine."""
    if session.provider is not None:
        client = session.providers.get_client(session.provider.name)
        if not client.local:
            return CLOUD_PLACEHOLDER
    return PLACEHOLDER


def _banner(session: Session, store: Store) -> str:
    """The session header. Best-effort: a provider that can't report its
    context window just leaves that fact out — the banner never blocks or
    fails a launch. A cloud catalog is never asked at all: its answer
    lives across the internet, and a launch does not wait for that."""
    context = None
    backend = ""
    if session.provider is not None:
        client = session.providers.get_client(session.provider.name)
        backend = client.kind
        if client.local:
            try:
                context = client.get_context_size(session.model)
            except Exception:
                context = None
    story = flatten(truncate(session.story_label(store), 40))
    return banner.render(
        __version__,
        session.model or "(no model)",
        backend=backend,
        context=context,
        story=story,
    )


def _show_resumed(session: Session, store: Store) -> None:
    """A resumed story starts mid-scene: name what was resumed and show its
    last turns, so the scene is on screen before the prompt — and hand
    them to the screen ledger, so /undo and /regen can take them back."""
    if not session.messages:
        return
    label = flatten(truncate(session.story_label(store), 40))
    if label:
        print(f"Story: {label}. Resumed at message {len(session.messages)}.")
    else:
        print(f"Resumed at message {len(session.messages)}.")
    print()
    print(session.render_last_turns(RESUME_TURNS))
    print()
    session.restore_screen_tail(RESUME_TURNS)

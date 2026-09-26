"""The chat loop. `run` owns the launch chrome (theme, notices, banner,
resume echo, worker start with the repaint hook) and the prompt →
submission loop with its spacing contract; `submit` routes one line (a
command dispatches through `chat.bindings`, story syntax plays through
the stream renderer, Ctrl+R loops regenerate in place) and contains its
crashes at the line boundary via `session.record_crash`.

Every submission ends with one blank line before the next prompt — the
gap lives in the ledger, once, not at each print site. The typed line
counts as output (it stays on screen); a shortcut's erased prompt line
does not, so a picker cancelled without a word leaves the screen exactly
as it was.
"""

import functools
import sys

from otaku.backend.api import play as api_play
from otaku.backend.api import stories as api_stories
from otaku.backend.session import Refused, Session
from otaku.console import banner
from otaku.terminal.chat import bindings, stream
from otaku.terminal.chat.chat import RESUME_TURNS, Chat
from otaku.terminal.prompt import PLACEHOLDER, Carry, LineAssembler, build_prompt, pictures
from otaku.terminal.screens import models as screen_models
from otaku.terminal.tty import (
    BOLD,
    CLOUD_PROMPT_PREFIX,
    CURSOR_BLINK_ON,
    PROMPT_CONTINUATION,
    PROMPT_PREFIX,
    RESET,
    error_line,
    theme,
)
from otaku.terminal.tty.cursor import measure, terminal_width
from otaku.terminal.tty.render import last_turns


def run(session: Session) -> None:
    """The whole terminal life over an open session, until /bye or EOF:
    settle the theme (before anything draws — the background ask reads
    stdin, which belongs to prompt_toolkit from the first prompt on),
    print the launch reports, then the banner, open the model picker
    when the session arrived model-less (nothing remembered — the launch-time
    pick is THIS loop's, not the backend's), echo the resumed scene into
    the ledger, start the worker with the repaint hook, then prompt →
    submit. `session.close()` stays the caller's (cli's)."""
    theme.use(session.terminal)
    # Some terminals (Ghostty) need the explicit blink opt-in on top of
    # the DECSCUSR shape escape prompt_toolkit emits.
    sys.stdout.write(CURSOR_BLINK_ON)
    sys.stdout.flush()

    chat = Chat(session)
    # The launch's reports — created files, stale settings, keys that
    # would not open — before anything else draws, one blank line after
    # the lot.
    for report in session.notices:
        print(report)
    if session.notices:
        print()
    session.notices.clear()
    # The launch-time pick, before the banner names the model: the picker
    # runs only when nothing is remembered — Esc is not a cancel, the
    # session simply stays model-less (the same state /model's Esc
    # leaves behind).
    if not session.model:
        screen_models.pick(session)
    if session.terminal.show_banner:
        # Each field a public read; the no-model fallback is this
        # frontend's own wording.
        print(
            banner.render_terminal(
                banner.SessionFacts(
                    model=session.model or "(no model)",
                    provider=session.provider,
                    max_context=session.max_context(),
                )
            )
        )
    carry = Carry()
    assembler = LineAssembler()
    prompt_session, answers = build_prompt(session, carry, assembler, shortcuts=bindings.SHORTCUTS)

    if session.notice:
        # The one bold hint, above the resumed story: a story that ends on
        # a question runs straight on into its answers, with nothing
        # between the two.
        print(f"{BOLD}{session.notice}{RESET}")
        print()
        session.notice = ""
    if session.messages:
        # A resumed story starts mid-scene: name what was resumed and
        # show its last turns, so the scene is on screen before the
        # prompt — and hand them to the ledger, so /undo and /regen can
        # take them back. The blank before the prompt is the prompt's own
        # when it opens on a question's answers (`AnswerMenu.top_row`).
        print(api_stories.landed_line(session))
        print()
        print(last_turns(list(session.messages), RESUME_TURNS, notes=chat.notes_displayed))
        answers.top_row = answers.posed
        if not answers.top_row:
            print()
        chat.restore_tail(RESUME_TURNS)

    # One status callback, two surfaces: the prompt's toolbar while the
    # prompt is up, the pinned bottom row while a reply streams. Each is
    # a no-op when it isn't the live one; `invalidate()` is
    # prompt_toolkit's thread-safe repaint trigger.
    def repaint() -> None:
        chat.status_line.refresh()
        if prompt_session.app.is_running:
            prompt_session.app.invalidate()

    session.set_on_status(repaint)
    # The launch batch is printed above; from here a notice is said where
    # it happens, not collected for a banner that has already been drawn.
    session.set_on_notice(chat.say)
    # Typing is activity: every buffer change pushes a pending pass back
    # a full idle window, so it starts on REAL idle, not mid-composition.
    prompt_session.default_buffer.on_text_changed += lambda _buf: session.touch()
    session.start_worker()

    while not chat.quit:
        # The marker carries the cloud, not the hint: the hint goes at
        # the first keystroke, which is exactly when the text is being
        # written to leave the machine.
        if assembler.in_block:
            prefix = PROMPT_CONTINUATION
        elif session.on_cloud:
            prefix = CLOUD_PROMPT_PREFIX
        else:
            prefix = PROMPT_PREFIX
        placeholder = None if assembler.in_block else PLACEHOLDER
        try:
            # the answers to a pending question, as rows over the prefix
            message = functools.partial(answers.message, prefix)
            line = prompt_session.prompt(
                message, placeholder=placeholder, default=carry.take_text(), pre_run=answers.open
            )
        except EOFError:
            break
        except KeyboardInterrupt:
            # ^C clears the line; inside a """ block it also drops the
            # buffer. The aborted prompt line stays on screen as a row
            # the ledger cannot measure, so erasing is off until the
            # next play — except while the story stands on a question,
            # where the abort erased the prompt, answers and all
            # (`prompt._answer_abort`), so the next prompt draws them in
            # place and the ledger has nothing new to count; a block's
            # collected lines above still stand. The prompt's own blank
            # (`top_row`) stays owed after an erased abort — the erase
            # took it too — and is on screen otherwise.
            erased = prompt_session.app.erase_when_done
            prompt_session.app.erase_when_done = False
            if assembler.in_block or not erased:
                chat.ledger.invalidate()
                answers.top_row = False
            assembler.reset()
            chat.ledger.typed_gone()
            continue
        # The prompt drew its blank, if it owed one; whatever follows
        # stands under the line it read.
        answers.top_row = False

        if carry.take_shortcut():
            # A shortcut key exited the prompt with its command as the
            # result; it is always a command — even mid-"""-block, where
            # feeding it to the assembler would paste "/regen" into the
            # user's text. The shown line is erased (its text returns at
            # the next prompt), so the submission occupies no screen
            # rows; an open block's collected lines above it are
            # composition the ledger cannot see past.
            submission = line
            chat.ledger.typed_gone()
            if assembler.in_block:
                chat.ledger.invalidate()
            # The rows the aborted prompt read occupies — the prefix plus
            # the in-progress line, wrapping and embedded newlines
            # included — go back up and away.
            shown = measure(prefix + carry.text + "\n", terminal_width())
            sys.stdout.write(f"\x1b[{shown}A\r\x1b[J")
        else:
            chat.ledger.typed(prefix + line + "\n")
            result = assembler.feed(line)
            if result is None:
                continue  # inside an open """ block — keep collecting lines
            submission = result
            if not submission:
                # A bare Enter leaves its prompt row on screen; its rows
                # stay counted so the next submission's erase takes it
                # too.
                continue

        try:
            submit(chat, submission)
        except KeyboardInterrupt:
            # ^C during a picker or a wait: return to the prompt cleanly.
            # What it left mid-row is not the ledger's to count.
            print()
            chat.ledger.invalidate()
        # The systematic blank before the next prompt — the prompt's own
        # to draw when it opens on a question's answers, so the question's
        # block runs on into them.
        answers.top_row = chat.ledger.gap(deferred=answers.posed)


def submit(chat: Chat, line: str) -> None:
    """One submitted line, whatever surface it came from: the user is
    active again, so queued background work is dropped; a slash command
    dispatches and anything else plays as story — the typed name
    settled, the turn echoed as the grey block, the reply streamed (a
    Ctrl+R loops regenerate in place).

    A crash is contained here, at the line boundary: the traceback goes
    to the error log, one short line says so, and the session lives on —
    every store write is transactional, so nothing is half-done. The
    error line prints past the screen ledger, so it invalidates it."""
    session = chat.session
    session.defer()
    try:
        if bindings.dispatch(chat, line):
            return
        # The terminal's own affordance, resolved on its side: an `@path`
        # naming a picture leaves the line and rides the turn as bytes.
        # Whether the model can see is the backend's to refuse.
        line, files = pictures.extract_pictures(line)
        try:
            events = api_play.submit(session, line, files)
        except Refused as e:
            # Checked before it plays: invalid syntax leaves the story
            # untouched rather than half-playing a line nobody can read.
            chat.ledger.invalidate()
            chat.say(str(e))
            return
        while stream.show(chat, events):
            events = api_play.regenerate(session)
    except KeyboardInterrupt:
        raise  # ^C is the user speaking, not a crash — the loop handles it
    except Exception as e:
        chat.ledger.invalidate()
        path = session.record_crash(f"command {line.split(' ', 1)[0]!r}", e)
        where = f" — recorded in {path}" if path else ""
        chat.say(error_line(f"Command failed ({type(e).__name__}){where}"))

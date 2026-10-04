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
import re
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples, to_formatted_text

from otaku.backend import StoryMode, commands
from otaku.backend.api import play as api_play
from otaku.backend.api import reports as api_reports
from otaku.backend.api import stories as api_stories
from otaku.backend.session import Refused, Session
from otaku.console import banner
from otaku.terminal.chat import bindings, stream
from otaku.terminal.chat.chat import RESUME_TURNS, Chat
from otaku.terminal.prompt import (
    CANVAS_PLACEHOLDER,
    CHAT_PLACEHOLDER,
    START_PLACEHOLDER,
    AnswerMenu,
    Carry,
    LineAssembler,
    build_prompt,
    pictures,
)
from otaku.terminal.screens import models as screen_models
from otaku.terminal.tty import (
    BOLD,
    CLOUD_PROMPT_PREFIX,
    CURSOR_BLINK_ON,
    MODIFY_OTHER_KEYS_OFF,
    MODIFY_OTHER_KEYS_ON,
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
    prompt_session, answers = build_prompt(
        session,
        carry,
        assembler,
        shortcuts=bindings.SHORTCUTS,
        aside=lambda: chat.context_use,
        note=lambda: chat.status_note,
    )

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
        echoed = last_turns(
            list(session.messages),
            RESUME_TURNS,
            shown=chat.shown_tools,
            is_canvas=session.layout.mode is StoryMode.STORY,
        )
        print(echoed)
        chat.ledger.note_printed(echoed + "\n")  # printed past the ledger: the flow's tail
        answers.top_row = answers.posed
        if not answers.top_row:
            print()
            chat.ledger.note_printed("\n")  # the rows under the text's end, counted
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
        # How full the next request is, read again before every prompt —
        # so after a turn, a story switch, an undo, a switched model —
        # and drawn on the status row's right until the next one.
        use = api_reports.context_use(session)
        chat.context_use = use.text() if use is not None else ""
        # The marker carries the cloud, not the hint: the hint goes at
        # the first keystroke, which is exactly when the text is being
        # written to leave the machine.
        is_canvas = session.layout.mode is StoryMode.STORY
        if assembler.in_block:
            prefix = PROMPT_CONTINUATION
        elif is_canvas and not carry.command_paragraph:
            prefix = ""  # PROTOTYPE: the canvas is one text, no prompt marker in it
        elif session.on_cloud:
            prefix = CLOUD_PROMPT_PREFIX
        else:
            prefix = PROMPT_PREFIX
        # the hint is the layout's: a story on the canvas is continued
        if assembler.in_block:
            placeholder = None
        elif is_canvas:
            placeholder = CANVAS_PLACEHOLDER if session.messages else START_PLACEHOLDER
        else:
            placeholder = CHAT_PLACEHOLDER
        # the answers to a pending question, as rows over the prefix
        message = functools.partial(answers.message, prefix)
        # PROTOTYPE: in the canvas the prompt stands in the story's own text
        # — its last line drawn again as the prompt's message, the join's
        # space after it — wherever the text is the last thing on screen
        in_text = (
            is_canvas
            and not assembler.in_block
            and not answers.posed
            and not carry.command_paragraph
            and chat.ledger.can_type_in_text()
        )
        carry.in_text = in_text
        # PROTOTYPE: the canvas's keys in every canvas prompt but a block's
        carry.canvas_keys = is_canvas and not assembler.in_block
        seam = ""
        if in_text:
            tail = chat.ledger.tail
            seam = "" if _plain(tail)[-1:].isspace() else " "
            chat.ledger.lift_to_tail()
            message = functools.partial(_in_text_message, tail, seam)
        # PROTOTYPE: a prompt in the text erases all it drew as it ends — its
        # message, the text's last line, too — and the ledger writes that line
        # again (`resume_tail`): no row is counted after it
        prompt_session.app.erase_when_done = in_text
        if carry.canvas_keys:
            # PROTOTYPE: Ctrl+Enter, the send, told from Enter where it can be
            sys.stdout.write(MODIFY_OTHER_KEYS_ON)
            sys.stdout.flush()
        try:
            line = prompt_session.prompt(
                message,
                placeholder=placeholder,
                default=carry.take_text(),
                pre_run=functools.partial(
                    _opening, answers, prompt_session, command=carry.command_paragraph
                ),
            )
        except EOFError:
            break
        except KeyboardInterrupt:
            if in_text:
                # PROTOTYPE: the line typed in the text goes; the text and
                # the layout under it stay, for the next prompt to stand in
                chat.ledger.resume_tail()
                chat.ledger.below_text()
                assembler.reset()
                chat.ledger.typed_gone()
                continue
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
            carry.end_walk()
            chat.ledger.typed_gone()
            continue
        finally:
            if in_text:
                prompt_session.app.erase_when_done = False
            if carry.canvas_keys:
                sys.stdout.write(MODIFY_OTHER_KEYS_OFF)
                sys.stdout.flush()
        # The prompt drew its blank, if it owed one; whatever follows
        # stands under the line it read.
        answers.top_row = False

        # PROTOTYPE: the command paragraph — opened from the text by `/`, it
        # is a prompt of its own under the text, the slash typed; left by
        # Esc or Backspace, its row goes and the text's prompt comes back,
        # the story typed so far with it
        if carry.to_command:
            carry.to_command = False
            if in_text:
                chat.ledger.resume_tail()
                chat.ledger.below_text()
            else:
                # the prompt erased its own row: nothing to put back
                prompt_session.app.erase_when_done = False
            carry.command_paragraph = True
            # the slash typed, or the command a history walk recalled
            carry.text = carry.recall if carry.recall is not None else "/"
            carry.recall = None
            continue
        was_command = carry.command_paragraph
        carry.command_paragraph = False
        if carry.from_command:
            # the prompt erased what it drew (`erase_when_done`): the caret
            # stands where the command paragraph began, under the text
            carry.from_command = False
            prompt_session.app.erase_when_done = False
            if carry.recall is not None:
                # a history walk crossed back into the story's text
                carry.text, carry.recall = carry.recall, None
                carry.draft = ""
            else:
                carry.text, carry.draft = carry.draft, ""
            continue
        if was_command and carry.draft:
            # the story typed before the command waits at the next prompt
            carry.text, carry.draft = carry.draft, ""

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
            if in_text:
                # PROTOTYPE: back to the text's end, the layout under it put back
                chat.ledger.resume_tail()
                chat.ledger.below_text()
            else:
                # The rows the aborted prompt read occupies — the prefix plus
                # the in-progress line, wrapping and embedded newlines
                # included — go back up and away.
                shown = measure(prefix + carry.text + "\n", terminal_width())
                sys.stdout.write(f"\x1b[{shown}A\r\x1b[J")
        else:
            if in_text:
                chat.ledger.resume_tail()
            else:
                chat.ledger.typed(prefix + line + "\n")
            # in the canvas the line plays as typed, the breaks at its edges
            # its paragraphs (`api.play.submit`); every other line is trimmed
            result = assembler.feed(line, verbatim=is_canvas)
            if result is None:
                if in_text:
                    chat.ledger.below_text(prefix + line)
                continue  # inside an open """ block — keep collecting lines
            submission = result
            if in_text and commands.is_command(submission):
                # PROTOTYPE: a command shows under the text as a prompt there
                # would have
                chat.ledger.below_text(prefix + submission)
            if not submission.strip() and not is_canvas:
                # A bare Enter leaves its prompt row on screen; its rows
                # stay counted so the next submission's erase takes it
                # too. In the canvas it is the continue: it plays.
                continue

        # the continue: the canvas's empty line, which the stream places
        # where the text ended rather than echoing (`stream.show`)
        carry.end_walk()
        chat.continuing = is_canvas and not submission.strip()
        chat.replied = False
        chat.status_note = ""  # the last reply's, shown until a line is sent
        try:
            submit(chat, submission)
        except KeyboardInterrupt:
            # ^C during a picker or a wait: return to the prompt cleanly.
            # What it left mid-row is not the ledger's to count.
            print()
            chat.ledger.invalidate()
        finally:
            chat.continuing = False
        # The answers are offered after a reply alone: a command's output
        # stands between the question and where the rows would be.
        answers.offered = chat.replied
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
        # in story mode the question this line answers leaves the text
        # before the stream takes the terminal
        stream.drop_past_question(chat)
        while stream.show(chat, events):
            events = api_play.regenerate(session)
    except KeyboardInterrupt:
        raise  # ^C is the user speaking, not a crash — the loop handles it
    except Exception as e:
        chat.ledger.invalidate()
        path = session.record_crash(f"command {line.split(' ', 1)[0]!r}", e)
        where = f" — recorded in {path}" if path else ""
        chat.say(error_line(f"Command failed ({type(e).__name__}){where}"))


def _in_text_message(tail: str, seam: str) -> StyleAndTextTuples:
    """PROTOTYPE: the prompt's message in the story's text — its last line
    as printed, escapes and all, and the join's space after it."""
    return [*to_formatted_text(ANSI(tail)), ("", seam)]


_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _plain(text: str) -> str:
    return _ESCAPE.sub("", text)


def _opening(answers: AnswerMenu, prompt_session: PromptSession[str], *, command: bool) -> None:
    """As each prompt opens: the question's answers read; and the command
    paragraph's menu shown at once, its slash typed (PROTOTYPE)."""
    answers.open()
    if command:
        prompt_session.default_buffer.start_completion(select_first=False)

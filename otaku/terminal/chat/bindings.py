"""The ONE binding module: the shared command table wired to the
terminal.

OPERATION-kind rows are wired generically: `OPERATIONS` maps their
tokens onto typed adapters `(session, raw) -> str` over the backend
calls — the sentence goes through `chat.say`, Refused is caught the same
way, and the dict's type holds the contract. INTERACTIVE rows get a
handler each: thin sequences of prompts, screens, waits, and ledger
choreography around backend calls, taking `chat` alone (the session
rides on it). The shortcut table carries both spellings of each key —
the binding form and the caption — so no seam translates by hand.
"""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import click

from otaku import web as web_frontend
from otaku.backend import commands
from otaku.backend.api import cards as api_cards
from otaku.backend.api import lore as api_lore
from otaku.backend.api import play as api_play
from otaku.backend.api import providers as api_providers
from otaku.backend.api import reports as api_reports
from otaku.backend.api import settings as api_settings
from otaku.backend.api import stories as api_stories
from otaku.backend.api import transfer as api_transfer
from otaku.backend.api.lore import WorkerRun
from otaku.backend.session import NO_STORY_HINT, Refused, Session
from otaku.terminal.chat import help as help_page
from otaku.terminal.chat import stream
from otaku.terminal.chat.chat import RESUME_TURNS, Chat
from otaku.terminal.screens import models as screen_models
from otaku.terminal.screens import stories as screen_stories
from otaku.terminal.screens import story as screen_story
from otaku.terminal.tty import BOLD, DIM, ERASE_LINE, RESET, YES_ANSWERS, error_line, latin_key
from otaku.terminal.tty.render import command_tokens, last_turns, message
from otaku.terminal.tty.typography import highlight_commands

# One OPERATION command: the raw argument text in, the sentence out.
Operation = Callable[[Session, str], str]

# token → backend call, for every OPERATION row of the shared table;
# built in one place so a new operation command is a table row + an
# entry here. `/new` is the one OPERATION with a handler instead (below):
# it swaps the story under the screen, and that break must be DRAWN —
# presentation the generic wiring has no business knowing.
OPERATIONS: dict[str, Operation] = {
    "/fork": api_stories.fork,
    "/title": api_stories.set_title,
    "/merge": api_lore.merge_raw,
    "/usage": lambda session, raw: api_reports.usage(session, raw).text(),
    "/balance": lambda session, raw: api_reports.balances(session).text(),
    "/info": lambda session, raw: api_reports.info(session).text(),
    "/set think": api_settings.set_think,
    "/set parameter": api_settings.set_parameter,
    "/set verbose": api_settings.set_verbose,
    "/set autocorrect": api_settings.set_autocorrect,
    "/set notification": api_settings.set_notification,
    "/set max_context": api_settings.set_max_context,
}


class Shortcut(NamedTuple):
    """One key in both spellings: what prompt_toolkit binds and what the
    menus print."""

    key: str  # prompt_toolkit's name ("c-r")
    caption: str  # the human's ("Ctrl+R")


# The terminal's shortcut keys, by the token they run.
SHORTCUTS: dict[str, Shortcut] = {
    "/regen": Shortcut("c-r", "Ctrl+R"),
    "/undo": Shortcut("c-u", "Ctrl+U"),
    "/stories": Shortcut("c-t", "Ctrl+T"),
    "/lore": Shortcut("c-l", "Ctrl+L"),
    "/model": Shortcut("c-o", "Ctrl+O"),  # Ctrl+M is unusable — the terminal sends it as Enter
    "/bye": Shortcut("c-d", "Ctrl+D"),
}

# The commands that manage the screen ledger themselves (they take an
# exchange back, or echo their own block). Every other command's output
# lands below the last exchange and invalidates on its first say — the
# dispatch window watches for the write rather than assuming one, so a
# picker left without a choice costs the exchanges above it nothing.
_PLAYING = {"/undo", "/regen", "/card"}


def dispatch(chat: Chat, line: str) -> bool:
    """True when the line was a command (handled); False when it should
    play as story. Unknown /words are reported here; the argument text
    passes raw — semantic parsing is the backend's."""
    if not commands.is_command(line):
        return False
    spec = commands.find(line)
    token = spec.token if spec else line.split()[0]
    with chat.ledger.command_output(manages_screen=token in _PLAYING):
        try:
            if spec is None:
                chat.say(commands.unknown_notice(line))
            elif spec.token in _INTERACTIVE:
                _INTERACTIVE[spec.token](chat, commands.raw_argument(line, spec.token))
            else:
                chat.say(
                    OPERATIONS[spec.token](chat.session, commands.raw_argument(line, spec.token))
                )
        except Refused as e:
            chat.say(str(e))
    return True


# ---------- the interactive handlers (one per INTERACTIVE row) ----------


def _undo(chat: Chat, raw: str) -> None:
    """Erase the exchange in place when the ledger proves it; report the
    new ending otherwise. The re-echoed turns below a report are turns —
    the next /undo or /regen works them — and taking them takes the
    report too, a fresh one printing in its place: the screen always
    shows one, current, report."""
    popped = api_play.undo(chat.session)
    if not popped:
        chat.ledger.invalidate()
        chat.say("Nothing to undo.")
        return
    refreshing = chat.ledger.top_is_report()
    if chat.ledger.erase_exchange():
        if refreshing:
            _report_undo_ending(chat)
        return  # otherwise the ending is still on screen — say nothing
    chat.ledger.invalidate()
    _report_undo_ending(chat)


def _report_undo_ending(chat: Chat) -> None:
    """The story's new ending, reported and re-echoed — and handed back
    to the ledger, the report line included, so the next /undo or /regen
    works the re-echoed turns."""
    chat.ledger.rule()
    if not chat.session.messages:
        chat.say("Undone. The story is now empty (its turns stay in the tree).")
        return
    report = f"{DIM}[ undone. the story now ends with: ]{RESET}"
    chat.say(f"{report}\n\n{last_turns(list(chat.session.messages), 2)}")
    chat.restore_tail(2, above=report)


def _regen(chat: Chat, raw: str) -> None:
    """Re-run the last prompt: the current reply becomes a sibling and a
    fresh one streams — in the old one's place when the screen allows;
    when it is beyond reach, the marker announces and the prompt being
    re-run echoes under it, so the new take reads as an exchange and
    /undo and /regen keep working it."""
    session = chat.session
    try:
        events = api_play.regenerate(session)
    except Refused as e:
        chat.ledger.invalidate()
        chat.say(str(e))
        return
    # The prompt the take re-runs, read BEFORE the stream starts (the
    # generator drops the standing reply on its first pull): the last
    # user row once the trailing reply goes — absent when the story is a
    # promptless reply, which regenerates on its own.
    msgs = list(session.messages)
    if msgs and msgs[-1].role == "assistant":
        msgs.pop()
    prompt = msgs[-1] if msgs else None
    if not chat.ledger.erase_reply():
        chat.ledger.invalidate()
        chat.ledger.rule()
        marker = f"{DIM}[ regenerating ]{RESET}"
        # The blank under the marker: the ledger's lead already counts
        # it (`echo_block`'s `above` math), so the screen must print it.
        chat.say(marker + "\n")
        # The typed line stays above the marker — nothing of it to erase.
        chat.ledger.typed_gone()
        chat.ledger.echo_block(message(prompt.body, "user") if prompt else "", above=marker)
    while stream.show(chat, events):
        events = api_play.regenerate(session)


def _last(chat: Chat, raw: str) -> None:
    """`/last [N]` — show the last N turns again (default 5), the way a
    relaunch shows the scene: a clean view after undos, regens, etc."""
    argument = raw.strip()
    if argument and not (argument.isdigit() and int(argument) > 0):
        chat.say("Usage: /last [N]")
        return
    count = int(argument) if argument else _LAST_TURNS_DEFAULT
    session = chat.session
    if not session.messages:
        chat.say("No turns yet.")
        return
    rows = count * 2  # a turn is an exchange: the prompt and its reply
    chat.ledger.rule()
    # What the echo actually holds, never what was asked for: a short
    # story shows everything it has, and the report must not over-claim.
    shown = (len(list(session.messages)[-rows:]) + 1) // 2
    report = f"The last {shown} turns of this story:"
    chat.say(f"{report}\n\n{last_turns(list(session.messages), rows)}")
    chat.restore_tail(rows, above=report)


# Turns /last shows when called bare — a turn being an exchange, the
# prompt and its reply (two message rows).
_LAST_TURNS_DEFAULT = 5


def _clear(chat: Chat, raw: str) -> None:
    """Wipe the screen; the story is untouched and /last brings the
    scene back. The next prompt opens at the top."""
    chat.ledger.clear()


def _system(chat: Chat, raw: str) -> None:
    """The terminal's grammar over the one backend setter: bare reports,
    `-` clears, a readable file (a leading `@` stripped) becomes the
    prompt's text; then ONE backend call — api.stories.set_system."""
    text = raw.strip().removeprefix("@")
    if not text:
        chat.say(api_stories.report_system(chat.session))
        return
    if text == "-":
        chat.say(api_stories.set_system(chat.session, ""))
        return
    path = _existing_file(text)
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as e:
            chat.say(error_line(f"Could not read {path}: {e}"))
            return
        if not text:
            chat.say(f"{path} is empty — system prompt unchanged.")
            return
    chat.say(api_stories.set_system(chat.session, text))


def _stories(chat: Chat, raw: str) -> None:
    """Browse every story, drill into any one's dossier, resume
    anywhere — the screen executes what its selection settled and
    returns the landing line; landing echoes the scene under the break
    rule."""
    if not api_stories.listing(chat.session):
        chat.say("No saved stories yet.")
        return
    _landed(chat, screen_stories.pick(chat.session))


def _landed(chat: Chat, landed: str | None) -> None:
    """Echo a screen's landing under the break rule — or nothing, when
    the reader only left (the screen restored itself; nothing moved)."""
    if landed is None:
        return
    chat.ledger.rule()
    turns = last_turns(list(chat.session.messages), RESUME_TURNS)
    # A story with nothing played echoes no turns, and no blank for them.
    chat.say(f"{landed}\n\n{turns}" if turns else landed)
    if turns:
        chat.restore_tail(RESUME_TURNS)


def _new(chat: Chat, raw: str) -> None:
    """The one OPERATION with a handler (see `OPERATIONS`): the story
    swaps under the screen, so the break rule draws over the notice."""
    chat.ledger.rule()
    chat.say(api_stories.new(chat.session, raw))


def _lore(chat: Chat, raw: str) -> None:
    _dossier(chat, "scenes")


def _cast(chat: Chat, raw: str) -> None:
    _dossier(chat, "cast")


def _dossier(chat: Chat, tab: screen_story.Tab) -> None:
    """The open story's dossier, on the asked-for tab — an empty memory
    opens too (its tab says so itself; premise and messages are a Tab
    away). A landing from its messages tab echoes like the browser's."""
    if chat.session.story_id is None:
        chat.say(NO_STORY_HINT)
        return
    _landed(chat, screen_story.browse(chat.session, tab))


def _extract(chat: Chat, raw: str) -> None:
    """Schedule the forced pass, echo the worker's status on one updating
    line, Ctrl+C cancels; the report prints when the WorkerRun returns."""
    run = api_lore.extract(chat.session)
    report = _wait_run(chat, run, opening="Closing a scene…")
    if report is not None:
        chat.say(error_line(report) if run.failed else report)


def _wait_run(chat: Chat, run: WorkerRun, *, opening: str) -> str | None:
    """Wait on a forced pass in the foreground, the worker's progress on
    one updating (transient, self-erasing) line; the report when the pass
    returned, None when Ctrl+C cancelled it (the cancel notice said)."""
    shown = ""

    def transient(line: str) -> None:
        sys.stdout.write(f"\r{ERASE_LINE}{DIM}{line}{RESET}")
        sys.stdout.flush()

    sys.stdout.write(chat.ledger.said())
    transient(opening)
    try:
        while not run.settled(0.1):
            line = chat.session.status()
            if line and line != shown:
                shown = line
                transient(line)
    except KeyboardInterrupt:
        transient(run.cancel())
        sys.stdout.write("\n")
        sys.stdout.flush()
        return None
    sys.stdout.write(f"\r{ERASE_LINE}")
    sys.stdout.flush()
    return run.poll()


def _context(chat: Chat, raw: str) -> None:
    """Page the preview (long stories run to thousands of lines), the
    role markers dimmed on the way through. `otaku logs` shows what was
    actually sent; this shows what is about to be."""
    preview = api_reports.context(chat.session).text(dim=DIM, reset=RESET)
    # The pager holds the real stream; its output is past the ledger.
    chat.ledger.invalidate()
    # color=True keeps the dim markers through less (-R).
    click.echo_via_pager(preview, color=True)


def _card(chat: Chat, raw: str) -> None:
    """The terminal's path affordance, then the three-step import:
    resolve `FILE [NAME]` on THIS side (the longest leading token run
    naming a real file is the file, a leading `@` stripped, the rest
    renames), read the bytes, then prepare → ask the persona (tty only;
    the remembered default) → add. The exchange echoes like a played
    submission: the typed line (and the ask) erased and re-echoed as the
    grey block, the report and the greeting riding it — so /undo takes
    the screen back whole."""
    argument = raw.strip().removeprefix("@")
    if not argument:
        chat.say("Usage: /card FILE [NAME]")
        chat.ledger.invalidate()
        return
    path, rename = _file_and_name(argument)
    try:
        data = path.read_bytes()
    except OSError as e:
        chat.say(error_line(f"Could not read {path}: {e}"))
        chat.ledger.invalidate()
        return
    try:
        # The row records the argument as typed — a path is what the
        # terminal's user named the file, and /last must read it back.
        prepared = api_cards.prepare(
            chat.session, data, path.name, rename, line=f"/card {argument}"
        )
    except Refused as e:
        chat.say(str(e))
        chat.ledger.invalidate()
        return
    persona = _ask_persona(chat, api_cards.remembered_persona(chat.session) or "you")
    landed = api_cards.add(chat.session, prepared, persona)
    # The import plays like a submission.
    chat.ledger.echo_block(message(landed.line.body, "user"))
    out = chat.ledger.reply
    print(f"{DIM}[ {landed.report} ]{RESET}", file=out)
    if landed.greeting is not None:
        print(file=out)
        print(message(landed.greeting.body, "assistant"), file=out)


def _import(chat: Chat, raw: str) -> None:
    """Resolve and read the file on THIS side (`@` stripped), then
    api.transfer.import_file(text, name); wait on the extraction
    WorkerRun showing `session.status()`, then echo the landing under
    the break rule — as deep in the scene as any other way in."""
    text = raw.strip().removeprefix("@")
    if not text:
        chat.say("Usage: /import FILE")
        return
    path = Path(text).expanduser()
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        chat.say(error_line(f"Could not read {path}: {e}"))
        return
    result = api_transfer.import_file(chat.session, content, path.name)
    for notice in result.notices:
        chat.say(notice)
    if result.extraction is not None:
        sys.stdout.write("\n")  # the import's counts, then its extraction, apart
        report = _wait_run(chat, result.extraction, opening="Closing a scene…")
        if report is not None:
            chat.say(report)
    chat.ledger.rule()
    turns = last_turns(list(chat.session.messages), RESUME_TURNS)
    chat.say(f"The last turns of this story:\n\n{turns}")
    chat.restore_tail(RESUME_TURNS)


def _export(chat: Chat, raw: str) -> None:
    """Render the document, confirm an overwrite, write the file. No
    name writes `<story-title>.md` (or story.md) in the current
    directory; a name carrying no extension of its own gets `.md`, since
    the document is one; an existing file prompts before overwriting
    (default no). A leading `@` — the path-completion trigger — is not
    part of the name."""
    document = api_transfer.export(chat.session)
    name = raw.strip().removeprefix("@")
    path = Path(name).expanduser() if name else Path(api_transfer.export_name(chat.session))
    # An existing DIRECTORY keeps its name: `.md` would write a file
    # beside it, where the write refusing out loud is the honest answer.
    if not path.suffix and not path.is_dir():
        path = path.with_suffix(api_transfer.EXPORT_SUFFIX)
    # A name the filesystem will not even look up (too long, say) is not
    # an existing file: fall through, and let the write refuse out loud.
    if _existing_file(str(path)) is not None:
        sys.stdout.write(chat.ledger.said())
        try:
            answer = latin_key(input(f"{path} already exists — overwrite? [y/N] ").strip())
        except (EOFError, KeyboardInterrupt):
            chat.say("Cancelled.")
            return
        if answer not in YES_ANSWERS:
            chat.say("Not exported.")
            return
    try:
        path.write_text(document, encoding="utf-8")
    except OSError as e:
        chat.say(error_line(f"Could not write {path}: {e}"))
        return
    chat.say(f"Exported to {path}.")


def _model(chat: Chat, raw: str) -> None:
    """Bare: the picker screen (which executes a confirmed switch and
    returns the notice); with an argument: api.providers.switch_spec
    (the split rule lives there)."""
    if raw.strip():
        chat.say(_bold_switch(api_providers.switch_spec(chat.session, raw)))
        return
    notice = screen_models.pick(chat.session, initial_spec=api_providers.listed_spec(chat.session))
    if notice:
        chat.say(_bold_switch(notice))


def _help(chat: Chat, raw: str) -> None:
    # The captions go over as DATA, the way the completion menu gets
    # them — the key table lives here and the page only reads it.
    # Colored at print time, never at build: the page's column math runs
    # on plain text (escapes would count into the padding), and the theme
    # is only settled once the launch has asked the terminal.
    captions = {token: shortcut.caption for token, shortcut in SHORTCUTS.items()}
    chat.say(highlight_commands(help_page.text(captions), command_tokens()))


def _web(chat: Chat, raw: str) -> None:
    """Serve this session to a browser and stand here until the reader
    stops it, then carry on at the prompt below. One session throughout:
    the other frontend is handed the open one rather than opening its
    own, and closing it stays cli's either way.

    This is the one place the terminal reaches the other frontend, and
    the arrow points only this way — a page has nowhere to send anybody
    back to."""
    # The other frontend prints into this terminal itself, which is
    # printing PAST `say` — so the say protocol is announced here
    # instead: the blank under the typed line, the stack invalidated
    # (what it prints lands past every row the ledger was counting), and
    # the gap before the next prompt earned. Without this the banner
    # opens flush against `/web` and the loop prints no gap at all.
    print(chat.ledger.said(), end="")
    try:
        web_frontend.run(chat.session, full=False)
    except web_frontend.ServeError as e:
        # The address is configuration, and a reader who cannot have the
        # browser keeps their session: a sentence, not a crash. The
        # reason is web's own words, under this medium's.
        chat.say(error_line(f"The web interface could not start: {e}"))


def _bye(chat: Chat, raw: str) -> None:
    chat.quit = True


# token → handler, for every INTERACTIVE row of the shared table (and
# the one decorated OPERATION, /new — see OPERATIONS).
_INTERACTIVE: dict[str, Callable[[Chat, str], None]] = {
    "/undo": _undo,
    "/regen": _regen,
    "/last": _last,
    "/clear": _clear,
    "/stories": _stories,
    "/system": _system,
    "/new": _new,
    "/lore": _lore,
    "/cast": _cast,
    "/extract": _extract,
    "/context": _context,
    "/card": _card,
    "/import": _import,
    "/export": _export,
    "/model": _model,
    "/help": _help,
    "/web": _web,
    "/bye": _bye,
}


# ---------- dispatch internals ----------


def _file_and_name(raw: str) -> tuple[Path, str]:
    """`FILE [NAME]` split by what EXISTS: the longest leading run of
    tokens naming a real file is the file, the rest is the rename — so
    both a path with spaces and a multi-word name read correctly. Nothing
    matching falls through whole, for `read_bytes` to refuse honestly."""
    tokens = raw.split()
    for i in range(len(tokens), 0, -1):
        candidate = _existing_file(" ".join(tokens[:i]))
        if candidate is not None:
            return candidate, " ".join(tokens[i:]).strip()
    return Path(raw).expanduser(), ""


def _ask_persona(chat: Chat, default: str) -> str:
    """Who the player is in this story — `{{user}}` in every card text.
    Enter takes the default; a non-tty stdin never blocks on the ask.
    The echoed ask joins the typed rows (the lead blank included), so
    the block echo replaces it along with the typed line."""
    if not sys.stdin.isatty():
        return default
    lead = chat.ledger.said()
    if lead:
        sys.stdout.write(lead)
        chat.ledger.typed(lead)
    ask = f"User name for the card [{default}]: "
    answer = input(ask).strip()
    chat.ledger.typed(ask + answer + "\n")
    return answer or default


def _bold_switch(notice: str) -> str:
    """The switch confirmation with its model spec in bold — styling is
    the terminal's, so it is laid over the backend's sentence here, at
    the two doors that show one."""
    head = "Switched to "
    if notice.startswith(head) and notice.endswith("."):
        return f"{head}{BOLD}{notice[len(head) : -1]}{RESET}."
    return notice


def _existing_file(text: str) -> Path | None:
    """The file `text` names, or None when it names none — including
    when the filesystem REFUSES to answer: a command argument is text
    until proven a path, and the proof is a question the OS can decline
    (a component over 255 bytes raises ENAMETOOLONG instead of returning
    false). Anything the lookup itself raises means the same thing
    here — not a file. Beside its only consumers: the `_system`,
    `_card`, and `_export` file affordances above."""
    try:
        candidate = Path(text).expanduser()
        return candidate if candidate.is_file() else None
    except (OSError, ValueError, RuntimeError):
        return None

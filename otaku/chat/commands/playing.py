"""The playing commands: /me, /you, /ooc, /undo, /regen, /last.

The three roleplay commands do ONE thing each: write their template into
the turn's `framing` verbatim, filling only `{name}` — the `((OOC: …))`
enclosure lives in the template, and the assembler joins framing to body at
wire time. No persona state, no rewriting: the wire role stays fixed by
who produced the row.

On screen they play like any turn: once the input validates, the typed
line re-echoes as the grey played-turn block. /undo and /regen work the
screen through the ledger (chat/screen.py): the erased exchange or reply
simply vanishes, and only when the ledger cannot prove the erase do they
fall back to reporting — every fallback print invalidates the ledger,
because it lands below the exchange it describes.
"""

from otaku.chat.inference import run_inference
from otaku.chat.session import Session
from otaku.store import Store
from otaku.store.schema import Message
from otaku.terminal import DIM, RESET

# Turns /last shows when called bare — a turn being an exchange, the
# prompt and its reply (two message rows).
_LAST_TURNS_DEFAULT = 5


def cmd_me(session: Session, store: Store, args: list[str]) -> None:
    """`/me NAME: PROMPT` — send PROMPT as NAME's line; you keep writing as
    NAME. NAME is free text: an existing cast member resolves (case/alias →
    canonical name), anyone else is taken verbatim and joins the cast at the
    next scene close. ONE row: the line is the body, the template rides its
    framing."""
    name_part, sep, prompt = session.raw_args.partition(":")
    prompt = prompt.strip()
    if not sep or not name_part.strip() or not prompt:
        session.screen.invalidate()
        print("Usage: /me NAME: PROMPT")
        return
    name = _resolve_character(session, store, name_part) or name_part.strip()
    framing = session.prompts.me_framing.replace("{name}", name)
    session.screen.echo_block(session.raw_line)
    session.record_turn(store, Message(role="user", body=prompt, framing=framing))
    run_inference(session, store)


def cmd_you(session: Session, store: Store, args: list[str]) -> None:
    """`/you NAME` — the model plays NAME from now on, and NAME responds to
    the scene immediately. One body-less turn whose framing is the template;
    the reply is a normal assistant turn."""
    if not args:
        session.screen.invalidate()
        print("Usage: /you NAME")
        return
    raw = " ".join(args)
    name = _resolve_character(session, store, raw) or raw.strip()
    had_turn = bool(session.messages)
    framing = session.prompts.you_framing.replace("{name}", name)
    session.screen.echo_block(session.raw_line)
    session.record_turn(store, Message(role="user", body="", kind="ooc", framing=framing))
    if had_turn:
        run_inference(session, store)


def cmd_ooc(session: Session, store: Store, args: list[str]) -> None:
    """`/ooc PROMPT` — talk to the model out of character; the reply is out
    of character too and the story doesn't advance. ONE turn: PROMPT is
    the body, the template rides its framing; `kind="ooc"` marks both
    sides."""
    if not args:
        session.screen.invalidate()
        print("Usage: /ooc PROMPT")
        return
    question = " ".join(args)
    session.screen.echo_block(session.raw_line)
    session.record_turn(
        store, Message(role="user", body=question, kind="ooc", framing=session.prompts.ooc_framing)
    )
    run_inference(session, store, ooc=True)


def cmd_undo(session: Session, store: Store, args: list[str]) -> None:
    """Discard the last exchange: the reply plus the prompt that caused it.
    Nothing is deleted — the head moves back and the undone turns stay in
    the tree as siblings. When the exchange still sits directly above the
    prompt, it is erased from the screen as if never played; otherwise the
    new ending is reported. The re-echoed turns below the report are
    turns — the next /undo or /regen works them — and taking them takes
    the report too, a fresh one printing in its place: the screen always
    shows one, current, report."""
    popped = session.undo(store)
    if not popped:
        session.screen.invalidate()
        print("Nothing to undo.")
        return
    refreshing = session.screen.top_is_report()
    if session.screen.erase_exchange():
        if refreshing:
            session.screen.take_suppressed_gap()  # output follows after all
            _report_ending(session)
        return  # otherwise the ending is still on screen — say nothing
    session.screen.invalidate()
    _report_ending(session)


def _report_ending(session: Session) -> None:
    """The story's new ending, reported and re-echoed — and handed back to
    the ledger, the report line included, so the next /undo or /regen
    works the re-echoed turns."""
    if not session.messages:
        print("Undone. The story is now empty (its turns stay in the tree).")
        return
    report = f"{DIM}[ undone. the story now ends with: ]{RESET}"
    print(report)
    print()
    print(session.render_last_turns(2))
    session.restore_screen_tail(2, above=report)


def cmd_regen(session: Session, store: Store, args: list[str]) -> None:
    """Re-run the last prompt: the current reply becomes a sibling in the
    tree and a fresh one streams — in the old one's place when the screen
    allows. When it is beyond reach, the marker announces and the prompt
    being re-run echoes under it, like an undo report shows its turns:
    the new take reads as an exchange, and /undo and /regen keep working
    it."""
    popped = session.drop_last_reply(store)
    if popped is None:
        session.screen.invalidate()
        print("Nothing to regenerate.")
        return
    if not session.screen.erase_reply():
        session.screen.invalidate()
        marker = f"{DIM}[ regenerating ]{RESET}"
        print(marker)
        print()
        # The typed line stays above the marker — nothing of it to erase.
        session.screen.typed_rows = 0
        session.screen.echo_block(session.messages[-1].body, above=marker)
    # An out-of-character reply regenerates out of character.
    run_inference(session, store, ooc=popped.kind == "ooc")


def cmd_last(session: Session, store: Store, args: list[str]) -> None:
    """`/last [N]` — show the last N turns again (default 5), the way a
    relaunch shows the scene: a clean view after undos, regens, etc. The
    echoed turns go back to the ledger, so /undo and /regen work them
    like freshly played ones."""
    if args and not (args[0].isdigit() and int(args[0]) > 0):
        print("Usage: /last [N]")
        return
    count = int(args[0]) if args else _LAST_TURNS_DEFAULT
    if not session.messages:
        print("No turns yet.")
        return
    print(session.render_last_turns(count * 2))  # a turn is two message rows
    session.restore_screen_tail(count * 2)


def _resolve_character(session: Session, store: Store, raw: str) -> str | None:
    """Canonical cast-member name for `raw` — the store's one name resolver
    decides; None when there is no story or no such character."""
    if session.story_id is None:
        return None
    hit = store.characters.find(session.story_id, raw)
    return hit.name if hit else None

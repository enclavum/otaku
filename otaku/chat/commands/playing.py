"""The playing commands: /me, /you, /ooc, /undo, /regen.

The three roleplay commands do ONE thing each: write their template into
the turn's `framing` verbatim, filling only `{name}` — the `((OOC: …))`
enclosure lives in the template, and the assembler joins framing to body at
wire time. No persona state, no rewriting: the wire role stays fixed by
who produced the row.
"""

from otaku.chat.inference import run_inference
from otaku.chat.state import DIM, RESET, Session
from otaku.store import Store
from otaku.store.schema import Message


def cmd_me(session: Session, store: Store, args: list[str]) -> None:
    """`/me NAME - PROMPT` — send PROMPT as NAME's line; you keep writing as
    NAME. NAME is free text: an existing cast member resolves (case/alias →
    canonical name), anyone else is taken verbatim and joins the cast at the
    next scene close. ONE row: the line is the body, the template rides its
    framing."""
    name_part, sep, prompt = session.raw_args.partition(" - ")
    prompt = prompt.strip()
    if not sep or not name_part.strip() or not prompt:
        print("Usage: /me NAME - PROMPT")
        return
    name = _resolve_character(session, store, name_part) or name_part.strip()
    framing = session.prompts.me_framing.replace("{name}", name)
    session.record_turn(store, Message(role="user", body=prompt, framing=framing))
    run_inference(session, store)


def cmd_you(session: Session, store: Store, args: list[str]) -> None:
    """`/you NAME` — the model plays NAME from now on, and NAME responds to
    the scene immediately. One body-less turn whose framing is the template;
    the reply is a normal assistant turn."""
    if not args:
        print("Usage: /you NAME")
        return
    raw = " ".join(args)
    name = _resolve_character(session, store, raw) or raw.strip()
    had_turn = bool(session.messages)
    framing = session.prompts.you_framing.replace("{name}", name)
    session.record_turn(store, Message(role="user", body="", kind="ooc", framing=framing))
    if had_turn:
        run_inference(session, store)


def cmd_ooc(session: Session, store: Store, args: list[str]) -> None:
    """`/ooc QUESTION` — talk to the model out of character; the reply is out
    of character too and the story doesn't advance. ONE turn: QUESTION is
    the body, the template rides its framing; `kind="ooc"` marks both
    sides."""
    if not args:
        print("Usage: /ooc <question or note>")
        return
    question = " ".join(args)
    session.record_turn(
        store, Message(role="user", body=question, kind="ooc", framing=session.prompts.ooc_framing)
    )
    run_inference(session, store, ooc=True)


def cmd_undo(session: Session, store: Store, args: list[str]) -> None:
    """Discard the last exchange: the reply plus the prompt that caused it.
    Nothing is deleted — the head moves back and the undone turns stay in
    the tree as siblings."""
    popped = session.undo(store)
    if not popped:
        print("Nothing to undo.")
        return
    if not session.messages:
        print("Undone. The story is now empty (its turns stay in the tree).")
        return
    print("Undone. The story now ends with:")
    print(session.render_last_turns(2))


def cmd_regen(session: Session, store: Store, args: list[str]) -> None:
    """Re-run the last prompt: the current reply becomes a sibling in the
    tree and a fresh one streams in its place."""
    popped = session.drop_last_reply(store)
    if popped is None:
        print("Nothing to regenerate.")
        return
    print(f"{DIM}[ regenerating ]{RESET}")
    # An out-of-character reply regenerates out of character.
    run_inference(session, store, ooc=popped.kind == "ooc")


def _resolve_character(session: Session, store: Store, raw: str) -> str | None:
    """Canonical cast-member name for `raw` — the store's one name resolver
    decides; None when there is no story or no such character."""
    if session.story_id is None:
        return None
    hit = store.characters.find(session.story_id, raw)
    return hit.name if hit else None

"""The stories, typed, carried out — the twin of `backend.story.stories`:
browsed and landed in (`/stories`), made (`/new`), copied (`/fork`),
given a premise (`/system`), the standing reply taken again (`/regen`),
the trailing exchange taken back (`/undo`) and what the newest discard
took put back (`/revert`) — each drawn in place where the screen
allows; `/title` is one call, its row the root's."""

from otaku.terminal.api import Conflict, Message, Refused
from otaku.terminal.api import stories as api_stories
from otaku.terminal.api.stories import NOTHING_TO_REGENERATE, NOTHING_TO_REVERT, NOTHING_TO_UNDO
from otaku.terminal.chat import landing, stream
from otaku.terminal.chat.chat import Chat
from otaku.terminal.prompt import paths
from otaku.terminal.screens import stories as screen_stories
from otaku.terminal.tty import DIM, RESET, error_line
from otaku.terminal.tty.cursor import terminal_width
from otaku.terminal.tty.render import (
    last_turns,
    line_text,
    message,
    prose,
    question_block,
    turn,
)


def stories(chat: Chat, raw: str) -> None:
    """Browse every story, drill into any one's dossier, resume
    anywhere — the screen executes what its selection settled and
    returns the landing line; landing echoes the scene under the break
    rule."""
    if not chat.api.stories.list():
        chat.say("No saved stories yet.")
        return
    landing.land_from_screen(chat, screen_stories.pick(chat.api, chat.story_id))


def new(chat: Chat, raw: str) -> None:
    """A new story, played at once: it swaps under the screen, so the
    break rule draws over the notice."""
    made = api_stories.new(chat.api, raw)
    chat.land(made.value.id)
    chat.ledger.rule()
    chat.say(made.notice)


def fork(chat: Chat, raw: str) -> None:
    """Continue in a copy of the story from its head, and play the copy
    from now on."""
    forked = api_stories.fork(chat.api, chat.story_id, raw)
    chat.land(forked.value.id)
    chat.say(forked.notice)


def system(chat: Chat, raw: str) -> None:
    """The terminal's grammar over the one backend setter: bare reports,
    `-` clears, a readable file (a leading `@` stripped) becomes the
    prompt's text; then ONE write, the story's premise (`_set_premise`)."""
    text = raw.strip().removeprefix("@")
    if not text:
        chat.say(api_stories.report_system(chat.premise))
        return
    if text == "-":
        chat.say(_set_premise(chat, ""))
        return
    path = paths.existing_file(text)
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as e:
            chat.say(error_line(f"Could not read {path}: {e}"))
            return
        if not text:
            chat.say(f"{path} is empty — system prompt unchanged.")
            return
    chat.say(_set_premise(chat, text))


def _set_premise(chat: Chat, text: str) -> str:
    """The premise written on the story — made for it where there is none
    yet, as a first line makes one — and the sentence. Cleared with no
    story, there is no premise to clear: the report says so."""
    if chat.story_id is None and not text:
        return api_stories.report_system("")
    with chat.writing() as story_id:
        said = chat.api.stories.update(story_id, premise=text).notice
    chat.refresh()
    return said


def regen(chat: Chat, raw: str) -> None:
    """Re-run the last prompt: the current reply becomes a sibling and a
    fresh one streams — in the old one's place when the screen allows;
    when it is beyond reach, the marker announces and the prompt being
    re-run echoes under it, so the new take reads as an exchange and
    /undo and /regen keep working it."""
    try:
        if chat.story_id is None:
            raise Conflict(NOTHING_TO_REGENERATE)
        story_id = chat.story_id
        with stream.waiting():
            events = chat.api.stories.regens.create(story_id, expected_head=chat.head)
    except Refused as e:
        chat.ledger.invalidate()
        chat.say(str(e))
        return
    # The prompt the take re-runs: the last user row once the trailing
    # reply goes — read while the fresh take may already have dropped it,
    # so either chain gives it. Absent when the story is a promptless
    # reply, which regenerates on its own.
    msgs = chat.messages()
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
        if chat.is_story_mode:
            # the prompt re-run reads as prose under the marker, and the
            # fresh take goes on from it (`ledger.echo_flow`)
            chat.ledger.echo_flow(inline=False, above=marker)
            if prompt is not None:
                chat.ledger.reply.write(prose(line_text(prompt)))
            chat.ledger.reply_begins()
        else:
            chat.ledger.echo_block(
                message(
                    prompt.body.strip(),
                    "user",
                    segments=prompt.segments,
                    pictures=len(prompt.attachments),
                )
                if prompt
                else "",
                above=marker,
            )
    while stream.show(chat, events):
        # the take stopped for a fresh one is recorded as its stream
        # closes, unseen here: the fresh one goes from the head as it stands
        standing = chat.api.stories.get(story_id).head
        with stream.waiting():
            events = chat.api.stories.regens.create(story_id, expected_head=standing)
    chat.mark_drawn()


def undo(chat: Chat, raw: str) -> None:
    """Erase the exchange in place when the ledger proves it; report the
    new ending otherwise. The re-echoed turns below a report are turns —
    the next /undo or /regen works them — and taking them takes the
    report too, a fresh one printing in its place: the screen always
    shows one, current, report."""
    try:
        if chat.story_id is None:
            raise Conflict(NOTHING_TO_UNDO)
        undone = chat.api.stories.undos.create(chat.story_id, expected_head=chat.head)
    except Refused as e:
        # Nothing taken: what the screen shows stands, and a story that
        # moved elsewhere is caught up before the next prompt.
        chat.ledger.invalidate()
        chat.say(str(e))
        return
    chat.mark_drawn(undone.value)
    refreshing = chat.ledger.top_is_report()
    if chat.ledger.erase_exchange():
        if refreshing:
            _report_undo_ending(chat)
        else:
            _pose_again(chat, drawn=False)
        return  # otherwise the ending is still on screen — say nothing
    chat.ledger.invalidate()
    _report_undo_ending(chat)


def revert(chat: Chat, raw: str) -> None:
    """Put back what the newest discard took, in place where the screen
    allows — an undone exchange echoed back under the ending it left, an
    earlier take drawn where the regenerated one stood — and the new
    ending reported otherwise, as an undo's is."""
    drawn = chat.messages()
    try:
        if chat.story_id is None:
            raise Conflict(NOTHING_TO_REVERT)
        reverted = chat.api.stories.reverts.create(chat.story_id, expected_head=chat.head)
    except Refused as e:
        chat.ledger.invalidate()
        chat.say(str(e))
        return
    chat.mark_drawn(reverted.value)
    if not chat.is_story_mode and _draw_back(chat, drawn, chat.messages()):
        _pose_again(chat, drawn=True)
        return
    chat.ledger.invalidate()
    _report_ending(
        chat,
        "[ reverted. the story now ends with: ]",
        "Reverted. The story is now empty (its turns stay in the tree).",
    )


def _draw_back(chat: Chat, drawn: list[Message], chain: list[Message]) -> bool:
    """Draw what a revert put back where the ledger proves the screen: an
    exchange that comes back after the ending the screen shows, echoed as
    a played one is, or a reply that comes back in place of the one
    standing, drawn where that one began. False for anything else — more
    than one exchange back, a reply the screen cannot erase — and nothing
    is drawn."""
    shared = next(
        (at for at, (was, now) in enumerate(zip(drawn, chain, strict=False)) if was.id != now.id),
        min(len(drawn), len(chain)),
    )
    gone, back = drawn[shared:], chain[shared:]
    prompt = back[0] if back and back[0].role == "user" else None
    reply = back[1:] if prompt is not None else back
    if not back or not _is_unit(reply):
        return False
    if not gone:
        if prompt is None or not chat.ledger.is_live:
            return False
        chat.ledger.echo_block(
            message(
                prompt.body.strip(),
                "user",
                segments=prompt.segments,
                pictures=len(prompt.attachments),
            )
        )
    elif prompt is None and reply and _is_unit(gone):
        if not chat.ledger.erase_reply():
            return False
    else:
        return False
    if reply:
        shown = chat.shown_tools
        chat.ledger.reply.write("\n\n".join(turn(row, shown=shown) for row in reply) + "\n")
    return True


def _is_unit(rows: list[Message]) -> bool:
    """Whether `rows` are one reply unit as /undo and /regen take it — a
    reply with its tool chain — or nothing at all."""
    if not rows:
        return True
    return all(row.role != "user" for row in rows) and rows[-1].unit == rows[0].id


def _pose_again(chat: Chat, *, drawn: bool) -> None:
    """An undo that leaves the story on a question offers its answers
    again (`Chat.replied`): the story is back where it was asked. In
    story mode an in-place undo draws the question back too — answering
    took it out of the text (`stream.drop_question`); `drawn` says
    the screen shows it already (a re-echo)."""
    question = chat.story.question if chat.story is not None else None
    if question is None:
        return
    if chat.is_story_mode and not drawn:
        chat.ledger.extend_reply(question_block(question.text, terminal_width()) + "\n")
    chat.replied = True


def _report_undo_ending(chat: Chat) -> None:
    """An undo's new ending, reported (`_report_ending`)."""
    _report_ending(
        chat,
        "[ undone. the story now ends with: ]",
        "Undone. The story is now empty (its turns stay in the tree).",
    )


def _report_ending(chat: Chat, report: str, empty: str) -> None:
    """The story's new ending, reported under `report` and re-echoed — or
    `empty` said, nothing left — and handed back to the ledger, the
    report line included, so the next /undo or /regen works the re-echoed
    turns."""
    chat.ledger.rule()
    chain = chat.messages()
    if not chain:
        chat.say(empty)
        return
    report = f"{DIM}{report}{RESET}"
    turns = last_turns(
        chain, 2, shown=chat.shown_tools, is_story_mode=chat.is_story_mode, posed=chat.posed
    )
    chat.say(f"{report}\n\n{turns}")
    chat.restore_tail(2, above=report)
    _pose_again(chat, drawn=True)

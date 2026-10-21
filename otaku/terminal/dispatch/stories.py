"""The stories, typed, carried out — the twin of `backend.story.stories`:
browsed and landed in (`/stories`), made (`/new`), copied (`/fork`),
given a premise (`/system`), the standing reply taken again (`/regen`)
and the trailing exchange taken back (`/undo`) — each drawn in place
where the screen allows; `/title` is one call, its row the root's."""

from otaku.terminal.api import Conflict, Refused
from otaku.terminal.api import stories as api_stories
from otaku.terminal.api.stories import NOTHING_TO_REGENERATE, NOTHING_TO_UNDO
from otaku.terminal.chat import landing, stream
from otaku.terminal.chat.chat import Chat
from otaku.terminal.prompt import paths
from otaku.terminal.screens import stories as screen_stories
from otaku.terminal.tty import DIM, RESET, error_line
from otaku.terminal.tty.cursor import terminal_width
from otaku.terminal.tty.render import last_turns, message, prose, question_block


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
        events = chat.api.stories.regens.create(story_id, expected_head=chat.head)
    except Refused as e:
        chat.ledger.invalidate()
        chat.say(str(e))
        return
    # The prompt the take re-runs, read BEFORE the stream starts (the
    # generator drops the standing reply on its first pull): the last
    # user row once the trailing reply goes — absent when the story is a
    # promptless reply, which regenerates on its own.
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
                chat.ledger.reply.write(prose(prompt.body))
            chat.ledger.reply_begins()
        else:
            chat.ledger.echo_block(
                message(prompt.body.strip(), "user", pictures=len(prompt.attachments))
                if prompt
                else "",
                above=marker,
            )
    while stream.show(chat, events):
        # the take stopped for a fresh one is recorded as its stream
        # closes, unseen here: the fresh one goes from the head as it stands
        standing = chat.api.stories.get(story_id).head
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


def _pose_again(chat: Chat, *, drawn: bool) -> None:
    """An undo that leaves the story on a question offers its answers
    again (`Chat.replied`): the story is back where it was asked. In
    story mode an in-place undo draws the question back too — answering
    took it out of the text (`stream.drop_past_question`); `drawn` says
    the screen shows it already (a re-echo)."""
    question = chat.story.question if chat.story is not None else None
    if question is None:
        return
    if chat.is_story_mode and not drawn:
        chat.ledger.extend_reply(question_block(question.text, terminal_width()) + "\n")
    chat.replied = True


def _report_undo_ending(chat: Chat) -> None:
    """The story's new ending, reported and re-echoed — and handed back
    to the ledger, the report line included, so the next /undo or /regen
    works the re-echoed turns."""
    chat.ledger.rule()
    chain = chat.messages()
    if not chain:
        chat.say("Undone. The story is now empty (its turns stay in the tree).")
        return
    report = f"{DIM}[ undone. the story now ends with: ]{RESET}"
    turns = last_turns(
        chain, 2, shown=chat.shown_tools, is_story_mode=chat.is_story_mode, posed=chat.posed
    )
    chat.say(f"{report}\n\n{turns}")
    chat.restore_tail(2, above=report)
    _pose_again(chat, drawn=True)

"""Story commands: /stories, /fork, /system, /rename, and /new.

`/stories` opens the browser and owns what a selection means: picking the
last turn resumes the story as-is; picking an earlier turn offers a fork
at that point, and declining rewinds the head instead (the later turns
stay in the tree as siblings — nothing is deleted either way).
"""

from otaku.chat.session import RESUME_TURNS, Session
from otaku.store import Store
from otaku.terminal import NO_ANSWERS, YES_ANSWERS, latin_key


def cmd_stories(session: Session, store: Store, args: list[str]) -> None:
    """`/stories` — browse every story, preview its turns, resume anywhere."""
    rows = store.stories.list()
    if not rows:
        print("No saved stories yet.")
        return
    if session.tui.pick_story is None:
        print("No story browser available.")
        return
    result = session.tui.pick_story(store, rows, session.story_id)
    if result is None:
        _reread_current(session, store)
        return
    story_id, messages, total = result

    # Picking an earlier message offers a fork at that point — the
    # playthrough mechanism. Declining resumes on this story with the head
    # moved back (the later messages stay in the tree as siblings).
    if len(messages) < total:
        try:
            ans = latin_key(
                input(
                    f"Resume at message {len(messages)} of {total}: "
                    f"continue in a fork from here? [Y/n] "
                ).strip()
            )
        except EOFError, KeyboardInterrupt:
            print("Cancelled.")
            _reread_current(session, store)
            return
        if not ans or ans in YES_ANSWERS:  # empty = the [Y/n] default
            story_id = store.stories.fork(
                story_id,
                from_message_id=messages[-1].id,
                settle=session.config.settle_messages,
            )
            messages = store.stories.get_messages(story_id)
            story = store.stories.get(story_id)
            print(f"Forked to '{story.title}'." if story and story.title else "Forked.")
        elif ans in NO_ANSWERS:
            store.stories.set_head(story_id, messages[-1].id)
            print("Resuming here — later messages stay in the tree as siblings.")
        else:
            print("Cancelled.")
            _reread_current(session, store)
            return

    session.switch_to(store, story_id, messages)
    print(f"Resumed at message {len(messages)}.")
    print()
    print(session.render_last_turns(RESUME_TURNS))


def cmd_rename(session: Session, store: Store, args: list[str]) -> None:
    """`/rename <title>` — title this story (shown in /stories and the banner);
    no text prints the current title. The one way a story gets a
    title by hand."""
    title = session.raw_args.strip()
    if not title:
        story = store.stories.get(session.story_id) if session.story_id is not None else None
        current = story.title if story else ""
        print(f'Title: "{current}"' if current else "Usage: /rename NEW-TITLE")
        return
    store.stories.rename(session.ensure_story(store), title)
    print(f'Renamed to "{title}".')


def cmd_fork(session: Session, store: Store, args: list[str]) -> None:
    """`/fork [TITLE]` — continue in a copy of this story from here; the
    original stays as it is. Without TITLE the copy inherits a numbered
    title ("<title> - N") — or none, when the story has none."""
    if session.story_id is None or not session.messages:
        print("Nothing to fork yet — send a message first.")
        return
    title = session.raw_args.strip() or None
    session.story_id = store.stories.fork(
        session.story_id, title=title, settle=session.config.settle_messages
    )
    session.messages = store.stories.get_messages(session.story_id)
    session.save_state()
    story = store.stories.get(session.story_id)
    print(f"Forked to '{story.title}'." if story and story.title else "Forked.")


def cmd_system(session: Session, store: Store, args: list[str]) -> None:
    """`/system <text>` — set this story's system prompt (the premise); no
    text prints the current one. It lives on the story, never on the
    model."""
    # raw_args is everything after `/system`, verbatim, so the prompt keeps
    # its exact spacing.
    text = session.raw_args.strip()
    if not text:
        print(f'System: "{session.system}"' if session.system else "System: (none)")
        return
    session.set_system(store, text)
    print(f"System prompt set ({len(text)} chars).")


def cmd_new(session: Session, store: Store, args: list[str]) -> None:
    """Start a brand-new story: clear the context and detach from the
    current story (which stays intact). The next turn creates a fresh one."""
    session.messages = []
    session.system = ""
    session.story_id = None
    session.save_state()  # a relaunch starts fresh, like this session
    print("Started a new story.")


def _reread_current(session: Session, store: Store) -> None:
    """The browser edits messages in place, so EVERY way out of it that
    keeps the current story must reread — the session must never hold a
    stale copy."""
    if session.story_id is not None:
        session.messages = store.stories.get_messages(session.story_id)

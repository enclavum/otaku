"""Story commands: /system and /new."""

from otaku.chat.state import Session
from otaku.store import Store


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

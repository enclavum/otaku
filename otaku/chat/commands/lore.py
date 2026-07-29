"""Lore commands: /scene, /lore, /cast, /merge — and the worker's jobs.

`build_job` is the one place a session snapshot becomes a worker `Job`, so
the REPL's idle scheduling and the manual close can never disagree on what
a pass sees.
"""

import threading
from dataclasses import replace

from otaku.chat.state import Session
from otaku.lore.extraction import Extractor, PassResult, Report
from otaku.lore.worker import Job
from otaku.store import Store
from otaku.term.ansi import DIM, ERASE_LINE, RESET


def build_job(session: Session) -> Job:
    """The worker job for the session as it stands: the story, the model,
    and the snapshot the warm-up rebuilds the next request from."""
    assert session.story_id is not None  # callers gate on a recorded story
    return Job(
        provider_name=session.provider.name,
        model=session.model,
        story_id=session.story_id,
        prompts=session.prompts,
        system=session.system,
        messages=list(session.messages),
        head_messages=session.config.head_messages,
        tail_messages=session.config.tail_messages,
        settle=session.config.settle_messages,
        min_chars=session.config.scene_min_chars,
        min_messages=session.config.scene_min_messages,
    )


def cmd_scene(session: Session, store: Store, args: list[str]) -> None:
    """`/scene` — close a scene over the unextracted tail right now:
    summarize it, write each character's journal entry, rebuild the
    rollups. The background pass does this automatically once the story
    holds enough settled new play; this drops the gate and the settle
    margin and closes right up to the last message.

    Runs through the background worker (one path into a pass, so a forced
    close can never race an automatic one) while the command waits in the
    foreground, echoing the worker's progress on one updating line. Ctrl+C
    stops the WAITING, not the close — the scene finishes in the background
    and the activity line carries on."""
    if session.story_id is None:
        print("No story yet — send a message first.")
        return
    forced = replace(build_job(session), force=True)
    if session.worker is None:
        # No worker ([lore_extraction] off): nothing else can be running,
        # so the pass is safe to drive here — still the one way to force
        # a scene.
        print("Extracting…")
        client = session.providers.get_client(session.provider.name)
        extractor = Extractor(store, client, session.model, session.story_id, session.prompts)
        result, report = extractor.run(
            settle=0, min_chars=forced.min_chars, min_messages=forced.min_messages, force=True
        )
        _report_pass(store, session.story_id, result, report)
        return

    done = threading.Event()
    outcome: list[tuple[PassResult, Report]] = []

    def on_done(result: PassResult, report: Report) -> None:
        outcome.append((result, report))
        done.set()

    def show(line: str) -> None:
        print(f"\r{ERASE_LINE}{DIM}{line}{RESET}", end="", flush=True)

    session.worker.schedule(replace(forced, on_done=on_done), now=True)
    show("Closing a scene…")
    shown = ""
    try:
        while not done.wait(0.1):
            line = session.worker.get_status()
            if line and line != shown:
                shown = line
                show(line)
    except KeyboardInterrupt:
        show("Still working — the scene closes in the background; watch the activity line.")
        print()
        return
    print(f"\r{ERASE_LINE}", end="")
    result, report = outcome[0]
    _report_pass(store, session.story_id, result, report)


def cmd_lore(session: Session, store: Store, args: list[str]) -> None:
    """`/lore` — browse and edit the story's memory: scenes, cast, and
    journals, in the full-screen browser. Editing covers the write-once
    fields (scene title/summary, journal entries, the latest state,
    character descriptions); the derived histories are read-only — they
    rebuild from what you fix."""
    _open_lore(session, store, "scenes")


def cmd_cast(session: Session, store: Store, args: list[str]) -> None:
    """`/cast` — the same browser, opened directly on the cast."""
    _open_lore(session, store, "cast")


def cmd_merge(session: Session, store: Store, args: list[str]) -> None:
    """`/merge SOURCE into TARGET` — fold an extraction duplicate into the
    real character (speakers and journals follow; SOURCE becomes an alias).
    The ' into ' separator keeps multi-word names unambiguous."""
    if session.story_id is None:
        print("No story yet — send a message first.")
        return
    src_raw, sep, dst_raw = session.raw_args.partition(" into ")
    if not sep or not src_raw.strip() or not dst_raw.strip():
        print("Usage: /merge SOURCE into TARGET")
        return
    source = store.characters.find(session.story_id, src_raw)
    target = store.characters.find(session.story_id, dst_raw)
    if source is None or target is None:
        missing = src_raw if source is None else dst_raw
        print(f"No character named '{missing.strip()}' in this story (see /cast).")
        return
    if source.id == target.id:
        print(f"'{src_raw.strip()}' and '{dst_raw.strip()}' are already the same character.")
        return
    store.characters.merge(session.story_id, source.id, target.id)
    # SOURCE is now an alias of TARGET, so a later /me or /you naming it
    # still resolves — nothing else to update.
    print(f"Merged {source.name} into {target.name} ('{source.name}' is now an alias).")


def _open_lore(session: Session, store: Store, lens: str) -> None:
    if session.story_id is None:
        print("No story yet — send a message first.")
        return
    ids = store.stories.get_messages_ids(session.story_id)
    if not store.scenes.get_current_ends(session.story_id, ids) and not store.characters.list(
        session.story_id
    ):
        print("No lore yet — it builds as scenes close (see /scene).")
        return
    if session.browse_lore is None:
        print("No lore browser available.")
        return
    session.browse_lore(store, session.story_id, lens)


def _report_pass(store: Store, story_id: int, result: PassResult, report: Report) -> None:
    """The outcome of a forced close, printed once it is known — shared by
    the worker-backed wait and the inline no-worker path."""
    if result is PassResult.NO_STORY:
        print("Nothing is recorded for this story, so there is nothing to extract.")
    elif result is PassResult.TOO_SHORT:
        print("Nothing new since the last scene.")
    elif result is PassResult.FAILED:
        print("Extraction failed (bad reply or request error) — the tail stays open; try again.")
    elif result is PassResult.CLOSED:
        rolled = f", {report.histories} history rollup(s)" if report.histories else ""
        journals = f"{report.journals} journal(s) written{rolled}; story-so-far refreshed."
        if report.scenes > 1:
            print(f"{report.scenes} scenes closed: {journals}")
        else:
            ids = store.stories.get_messages_ids(story_id)
            scenes = store.scenes.get_current(story_id, ids)
            title = f" '{scenes[-1].title}'" if scenes and scenes[-1].title else ""
            print(f"Scene{title} closed: {journals}")

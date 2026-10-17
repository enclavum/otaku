"""A forced extraction pass in flight — what a frontend waits on, polls
or cancels, kept per story by the app (`App._extraction_runs`;
`domain.story.lore`'s `start_extraction`, `extraction_run`,
`cancel_extraction`)."""

import threading

from otaku.agents import Worker
from otaku.agents.extraction import PassResult, Report


class WorkerRun:
    """A forced WORKER pass in flight. Channel-safe by contract (like the
    app's worker channels): `wait`, `poll`, and `cancel` touch only
    the run's own event and report — the worker sets, any thread may
    ask — so a web GET can answer without holding the app's one
    thread."""

    def __init__(self, worker: Worker) -> None:
        self._worker = worker
        self._done = threading.Event()
        self._report = ""
        self._failed = False
        self._cancelled = False

    def wait(self) -> str:
        """Block until the pass returns; the report to show — scenes
        closed, journals written, rollups refreshed, or what failed and
        why (the worker's held status names a crash's real cause)."""
        self._done.wait()
        return self._report

    def poll(self) -> str | None:
        """The report when the pass has returned, None while it runs —
        the non-blocking form the web's GET polls."""
        return self._report if self._done.is_set() else None

    def settled(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for the pass to return; True when
        it has. A foreground wait refreshes its status line between
        these, and wakes the moment the pass is done rather than at the
        end of whatever sleep it was in."""
        return self._done.wait(timeout)

    @property
    def failed(self) -> bool:
        """Whether the returned pass FAILED — the report is a refusal,
        and a frontend shows it the way it shows any other."""
        return self._failed

    @property
    def cancelled(self) -> bool:
        """Whether the returned pass was given up mid-way (`cancel`)."""
        return self._cancelled

    def cancel(self) -> str:
        """Abort mid-stream; nothing half-done commits. The notice.

        Guarded on this run's own end first: `_worker.cancel()` aborts
        whatever the worker is running NOW, and a pass that finished
        between the caller's poll and this call may have handed the
        worker something else — an automatic pass the caller never
        started. A finished run answers with its report instead. The
        guard narrows that race to a moment; closing it entirely would
        take cancel-by-identity in the worker."""
        if self._done.is_set():
            return self._report or "The pass already finished."
        self._worker.cancel()
        return "Cancelled — nothing half-done commits; already-closed scenes stay."

    # The worker's side (package-internal): called on the worker thread,
    # before the warm-up.
    def _finish(self, result: PassResult, report: Report) -> None:
        self._report = _pass_report(result, report, held=self._worker.status())
        self._failed = result is PassResult.FAILED
        self._cancelled = result is PassResult.CANCELLED
        self._done.set()


def _pass_report(result: PassResult, report: Report, *, held: str) -> str:
    """The outcome of a forced close, as one sentence. `held` is the
    worker's held status row: when the pass CRASHED it names the real
    cause, and the report repeats it instead of blaming the model's
    reply for every failure. Composed from the pass's own data alone —
    no store reads, so the worker thread can set it."""
    if result is PassResult.NO_STORY:
        return "Nothing is recorded for this story, so there is nothing to extract."
    if result is PassResult.TOO_SHORT:
        return "Nothing new since the last scene."
    if result is PassResult.CANCELLED:
        return "Cancelled — nothing half-done commits; already-closed scenes stay."
    if result is PassResult.FAILED:
        reason = "bad reply or request error"
        if held.startswith("extraction failed (") and held.endswith(")"):
            reason = held[len("extraction failed (") : -1]
        return f"Extraction failed ({reason}) — the tail stays open; try again."
    rolled = f", {report.histories} history rollup(s)" if report.histories else ""
    refreshed = "; story-so-far refreshed" if report.scene_histories else ""
    journals = f"{report.journals} journal(s) written{rolled}{refreshed}."
    if report.scenes > 1:
        return f"{report.scenes} scenes closed: {journals}"
    title = f" '{report.last_scene_title}'" if report.last_scene_title else ""
    return f"Scene{title} closed: {journals}"

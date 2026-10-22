"""The backend's one refusal channel: `Refused`, and the kinds a caller
tells apart — each a `Refused`, so catching the base still catches all —
with the sentences more than one door refuses with, each spelled once;
and `StoppingError`, the answer of a call the closing app will not run."""

# What every model-facing door says while no model is selected, and
# every story-facing one while nothing has been played — each spelled
# once, so no door (or frontend) drifts into its own wording.
NO_MODEL_HINT = "No model selected — pick one with /model."
NO_STORY_HINT = "No story yet — send a message first."
# What every door says for a story id the store does not hold, and for a
# message id that is not on the story's current chain.
NO_SUCH_STORY = "No such story."
OFF_CHAIN = "That message is not on the story's current chain."


class Refused(Exception):  # noqa: N818 — a refusal is an expected answer, not an error
    """An operation declined for an expected reason; str(e) is the exact
    sentence to show. Frontends catch it at every call site and print,
    so no operation needs a `| str` return. Always raised EAGERLY —
    never mid-stream (that is the `Declined` event's job). Raised as one
    of the kinds below, which say WHY for a caller that answers each
    differently; the sentence is the same either way."""


class NotFound(Refused):
    """The subject the call names is not there: a story, a message, a
    character, a setting, a provider."""


class Conflict(Refused):
    """The state the call meets rules it out: no model selected, nothing
    to undo, a pass running, a name the cast already holds."""


class Invalid(Refused):
    """The input cannot be taken as given: a usage line, a value out of
    range, a document that does not parse."""


class Unavailable(Refused):
    """Something the call depends on failed it: a provider, the support
    desk, a file that could not be written."""


class HeadMoved(Conflict):
    """The story's head moved on since the caller last saw it: nothing was
    done, and `head` is where it stands now — for a caller to read the
    story again from there."""

    def __init__(self, head: int | None) -> None:
        super().__init__("The story moved on since you last saw it — nothing was changed.")
        self.head = head


class StoppingError(Exception):
    """The app is closing: the call will not be run. Neither a refusal
    nor a crash — the one answer a call can get that nothing should be
    recorded for."""

"""The backend's one refusal channel."""


class Refused(Exception):  # noqa: N818 — a refusal is an expected answer, not an error
    """An operation declined for an expected reason; str(e) is the exact
    sentence to show. Frontends catch it at every call site and print,
    so no operation needs a `| str` return. Always raised EAGERLY —
    never mid-stream (that is the `Declined` event's job)."""

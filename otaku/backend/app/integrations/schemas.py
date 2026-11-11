"""What `app.integrations.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema


class CardSite(Schema):
    """A site a card is fetched from by its page's link: its name, and
    the hosts its links name, the main one first."""

    name: str
    hosts: tuple[str, ...]


class WebSearch(Schema):
    """Where web search stands: whether it holds a key."""

    has_key: bool

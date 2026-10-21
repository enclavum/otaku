"""What `app.integrations.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema


class WebSearch(Schema):
    """Where web search stands: whether it holds a key."""

    has_key: bool

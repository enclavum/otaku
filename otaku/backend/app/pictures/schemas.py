"""What `app.pictures.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema


class Picture(Schema):
    """A picture's bytes as stored, and its media type."""

    data: bytes
    media_type: str

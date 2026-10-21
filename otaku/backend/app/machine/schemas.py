"""What `app.machine.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema


class Machine(Schema):
    """The machine as a picker states it: the memory gauge ("RAM: 12.4 /
    32.0 GB (39%)"), "" where the platform will not say."""

    memory: str

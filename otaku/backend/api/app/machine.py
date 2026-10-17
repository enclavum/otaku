"""The machine a model fills, as a frontend reads it: its memory, read
now — one syscall, so a picker can watch it fill while a model loads.
How it is read is the domain's (`domain.app.machine`)."""

from __future__ import annotations

from dataclasses import dataclass

from otaku.backend.domain.app import App
from otaku.backend.domain.app import machine as domain_machine


class MachineResource:
    """/app/machine — the machine a model fills."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self) -> Machine:
        """The machine's memory, read now — one syscall."""
        return Machine(domain_machine.get_machine())


@dataclass(frozen=True)
class Machine:
    """The machine as a picker states it: the memory gauge ("RAM: 12.4 /
    32.0 GB (39%)"), "" where the platform will not say."""

    memory: str

"""The machine a model fills, as a frontend reads it: its memory, read
now — one syscall, so a picker can watch it fill while a model loads.
How it is read is the domain's (`app.machine.services`)."""

from __future__ import annotations

from otaku.backend.app.machine import services as domain_machine
from otaku.backend.app.machine.schemas import Machine
from otaku.backend.base import Resource


class MachineResource(Resource):
    """/app/machine — the machine a model fills."""

    def get(self) -> Machine:
        """The machine's memory, read now — one syscall."""
        return Machine(domain_machine.get_machine())

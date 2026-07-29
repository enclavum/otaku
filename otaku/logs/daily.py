"""Day-rotated log files: `<prefix>YYYYMMDD<suffix>` under logs/.

The base every log shares: where a day's file lives and which days exist.
Subclasses own what a line is and how it is written.
"""

import builtins
from pathlib import Path
from typing import ClassVar

from otaku.paths import Paths


class DailyLog:
    _prefix: ClassVar[str]
    _suffix: ClassVar[str]

    def __init__(self, paths: Paths) -> None:
        self._paths = paths

    def get_days(self) -> builtins.list[tuple[str, int]]:
        """The available log days as (YYYYMMDD, file size), oldest first."""
        if not self._paths.logs_dir.exists():
            return []
        out: builtins.list[tuple[str, int]] = []
        for path in sorted(self._paths.logs_dir.glob(f"{self._prefix}????????{self._suffix}")):
            out.append((path.name[len(self._prefix) : -len(self._suffix)], path.stat().st_size))
        return out

    def get_path(self, day: str) -> Path:
        return self._paths.logs_dir / f"{self._prefix}{day}{self._suffix}"

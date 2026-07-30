"""The error log: every unexpected traceback, in one place.

`logs/errors-YYYYMMDD.log` — a timestamped `=== <context>` header and the
full traceback, appended wherever a crash is contained (a command in the
REPL, a worker pass, the last-resort handler in `cli.main`), and readable
with `otaku logs errors`. Frames and exception messages only, NEVER
locals: this file sits in plain text beside a possibly-encrypted
database, and a traceback with locals would leak what the cipher
protects. Best-effort: a logging failure warns once and never blocks
anything.
"""

import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from otaku.logs.daily import DailyLog
from otaku.paths import Paths


class ErrorLog(DailyLog):
    _prefix = "errors-"
    _suffix = ".log"

    def __init__(self, paths: Paths) -> None:
        super().__init__(paths)
        self._lock = threading.Lock()  # appends come from worker and REPL threads
        self._warned = False

    def record(self, context: str, exc: BaseException) -> Path:
        """Append one crash: a `=== <timestamp> <context>` header and the
        traceback. Returns the day's file (for the on-screen notice) and
        never raises — the log is an account, not a dependency."""
        now = datetime.now().astimezone()
        path = self.get_path(now.strftime("%Y%m%d"))
        header = f"=== {now.isoformat(timespec='seconds')} {context}\n"
        body = "".join(traceback.format_exception(exc))
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(header + body + "\n")
        except OSError as e:
            if not self._warned:
                self._warned = True
                print(f"otaku: error logging failed: {e}", file=sys.stderr)
        return path

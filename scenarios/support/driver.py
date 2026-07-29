"""Drives the real `otaku` binary in a pseudo-terminal.

The truest scenarios run the actual application: keystrokes go down a pty,
assertions read the terminal output. The output is a stream of incremental
redraws, so `expect` searches the WHOLE accumulated transcript (ANSI
stripped) — and assertions must pick markers a partial redraw cannot split,
like a word from a line that renders in one write.

One timing caveat: between prompt sessions — right after streamed output,
before the next prompt takes the terminal raw — the tty is briefly in
canonical mode, where the KERNEL consumes control characters (Ctrl+U is
line-kill there). `settle` before sending a control key rides that window
out.
"""

import os
import pty
import re
import select
import subprocess
import sys
import time

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[78]|\x1b\][^\x07]*\x07")

CTRL_C = b"\x03"
CTRL_D = b"\x04"
CTRL_O = b"\x0f"
CTRL_R = b"\x12"
CTRL_T = b"\x14"
CTRL_U = b"\x15"
ENTER = b"\r"
ESC = b"\x1b"


class Terminal:
    """One app run in a pty. `send` types, `expect` waits for markers in
    the transcript so far, `quit` ends the session."""

    def __init__(self, state_dir: str, *, env: dict[str, str] | None = None) -> None:
        self._master, slave = pty.openpty()
        run_env = (
            os.environ
            | {
                "OTAKU_CONFIG_DIR": state_dir,
                "TERM": "xterm-256color",
            }
            | (env or {})
        )
        self._proc = subprocess.Popen(
            [sys.executable, "-c", "from otaku.cli import main; main()"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            env=run_env,
        )
        os.close(slave)
        self._raw = b""

    @property
    def transcript(self) -> str:
        """Everything the app has drawn so far, ANSI stripped."""
        return _ANSI.sub("", self._raw.decode("utf-8", "replace"))

    def send(self, data: bytes | str, settle: float = 0.5) -> None:
        os.write(self._master, data.encode() if isinstance(data, str) else data)
        self._drain(settle)

    def settle(self, seconds: float = 1.0) -> None:
        """Let the app catch up (see the canonical-mode caveat above)."""
        self._drain(seconds)

    def expect(self, *markers: str, timeout: float = 10.0) -> None:
        """Wait until every marker has appeared in the transcript."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if all(marker in self.transcript for marker in markers):
                return
            self._drain(0.2)
        missing = [m for m in markers if m not in self.transcript]
        raise AssertionError(f"never saw {missing!r} in:\n{self.transcript[-2000:]}")

    def quit(self, *, timeout: float = 10.0) -> int:
        """End the session (Ctrl+D past any open UI) and return the exit
        code."""
        try:
            self.send(ESC, 0.3)
            self.send(CTRL_D, 0.3)
            return self._proc.wait(timeout=timeout)
        finally:
            self._proc.kill()
            os.close(self._master)

    def wait(self, *, timeout: float = 10.0) -> int:
        """The exit code of an app expected to end on its own."""
        try:
            deadline = time.time() + timeout
            while self._proc.poll() is None and time.time() < deadline:
                self._drain(0.2)
            return self._proc.wait(timeout=1)
        finally:
            self._proc.kill()
            os.close(self._master)

    def _drain(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self._master], [], [], 0.1)
            if ready:
                try:
                    self._raw += os.read(self._master, 65536)
                except OSError:
                    return

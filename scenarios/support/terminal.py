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

import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[78]|\x1b\][^\x07]*\x07")

_CPR_QUERY = b"\x1b[6n"
_BG_QUERY = b"\x1b]11;?"

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

    def __init__(
        self,
        state_dir: str,
        *,
        env: dict[str, str] | None = None,
        rows: int = 24,
        cols: int = 80,
    ) -> None:
        self._master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
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
        self._cpr_row: int | None = None
        self._cpr_from = 0
        self._bg_reply: bytes | None = None
        self._bg_from = 0

    @property
    def transcript(self) -> str:
        """Everything the app has drawn so far, ANSI stripped."""
        return _ANSI.sub("", self._raw.decode("utf-8", "replace"))

    @property
    def raw(self) -> bytes:
        """Everything the app has drawn, escapes included — for asserting
        the erase sequences themselves."""
        return self._raw

    def arm_cpr(self, row: int) -> None:
        """Answer the app's next cursor-position query (ESC[6n) with `row`
        — one-shot, armed right before the keystroke that triggers an
        erase, so it meets that query and no other."""
        self._cpr_row = row
        self._cpr_from = len(self._raw)

    def arm_background(self, *, dark: bool) -> None:
        """Answer the app's next background query (OSC 11) as a dark or a
        light terminal — one-shot, like `arm_cpr`."""
        shade = b"1c1c" if dark else b"fafa"
        self._bg_reply = b"\x1b]11;rgb:%s/%s/%s\x07" % (shade, shade, shade)
        self._bg_from = len(self._raw)

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
                if self._cpr_row is not None and _CPR_QUERY in self._raw[self._cpr_from :]:
                    os.write(self._master, b"\x1b[%d;1R" % self._cpr_row)
                    self._cpr_row = None
                if self._bg_reply is not None and _BG_QUERY in self._raw[self._bg_from :]:
                    os.write(self._master, self._bg_reply)
                    self._bg_reply = None

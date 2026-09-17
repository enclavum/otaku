"""The terminal demo's browser bootstrap: the REAL terminal frontend on
Pyodide, its terminal a page-side xterm.js reached through `termbridge`
(a JS module the host registers before running this; see worker.js and
smoke.mjs for its two implementations).

The philosophy is demo.js's, one layer down: the web demo is the real
page with `fetch` faked in the browser; this is the real TUI with the
provider faked under `providers.registry.ALL_CLIENTS`. Everything between —
the chat loop, the ledger, the prompt, the store, the context assembler,
extraction — is the product's own code, imported unmodified.

What the bridge must provide:

    read(timeout_ms) -> Uint8Array          block for terminal bytes (empty on timeout)
    write(text)      -> None                text (with ANSI) to the terminal
    size()           -> [columns, rows]     the terminal's current size

Where the product touches the OS in ways a browser cannot answer, this
module swaps ONE seam each, always at a name the product already
declares:

- prompt_toolkit I/O: a pipe-free `Input` fed by the byte queue and a
  `Vt100_Output` over the bridge, injected through `create_app_session`
  (the same seam scenarios/support/screens.py uses).
- The event loop: `Application.run` ends in `asyncio.run`, so a policy
  whose loop blocks on the bridge instead of a selector is the whole
  hook; no prompt_toolkit code is patched.
- `tty.ask`: the same two queries (OSC 11, DSR 6) written to xterm.js,
  which answers both for real; only the termios posture is replaced.
- Threads: Pyodide cannot start any. The worker runs its forced passes
  inline (`/extract` and `/import` wait on them anyway), the spinner
  becomes one static frame, the model picker's fire-once threads run
  synchronously, the completion's relay — a pump thread, for a cancel
  that cuts a waiting request — passes the chunks straight through, and
  the in-stream Ctrl+C/Ctrl+R watcher moves into the fake client's
  pacing loop — where the bytes actually arrive.
"""

import asyncio
import builtins
import codecs
import contextlib
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import termbridge  # the JS module the host registers

# ---------- the byte queue from the terminal ----------

_queue = bytearray()


def _pump(timeout: float | None) -> bool:
    """Wait up to `timeout` seconds (None = forever) for terminal bytes;
    True when any arrived. Output is flushed first — nothing may block on
    a reader who has not seen what was asked."""
    _flush_out()
    ms = -1 if timeout is None else max(0, int(timeout * 1000))
    data = termbridge.read(ms)
    payload = data.to_py() if hasattr(data, "to_py") else data
    if not payload:
        return False
    _queue.extend(payload)
    return True


# ---------- output to the terminal ----------

_out_buffer: list[str] = []


def _flush_out() -> None:
    if _out_buffer:
        data = "".join(_out_buffer)
        _out_buffer.clear()
        termbridge.write(data)


class _TermWriter:
    """sys.stdout (and __stdout__): text straight to the terminal, with
    light batching — a newline or an explicit flush sends the batch, so
    streamed prose appears chunk by chunk while a burst of escape codes
    rides one message."""

    encoding = "utf-8"

    def write(self, data: str) -> int:
        _out_buffer.append(data)
        if "\n" in data or sum(len(part) for part in _out_buffer) > 8192:
            _flush_out()
        return len(data)

    def flush(self) -> None:
        _flush_out()

    def isatty(self) -> bool:
        return True


class _TermStderr:
    """stderr: the same terminal, plus the host's console when the bridge
    offers one (`err`) — a traceback must land somewhere visible."""

    encoding = "utf-8"

    def write(self, data: str) -> int:
        _flush_out()
        termbridge.write(data.replace("\n", "\r\n"))
        err = getattr(termbridge, "err", None)
        if err is not None:
            err(data)
        return len(data)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True


class _TermStdin:
    """sys.stdin stands in only for its `isatty` answer (the confirm
    prompts check it); every actual read goes through the patched
    `input()` below."""

    encoding = "utf-8"

    def isatty(self) -> bool:
        return True


# ---------- the event loop ----------

_INPUT_CB: list = [None]  # the attached prompt_toolkit input callback


class _TermSelector:
    """The selector the demo loop blocks in: no file descriptors — the
    one input source is the byte queue, and "ready" means bytes arrived
    while a prompt_toolkit input is attached."""

    def __init__(self) -> None:
        self._map: dict = {}

    def register(self, fileobj, events, data=None):  # pragma: no cover - unused
        import selectors

        key = selectors.SelectorKey(fileobj, hash(fileobj), events, data)
        self._map[key.fd] = key
        return key

    def unregister(self, fileobj):  # pragma: no cover - unused
        return self._map.pop(hash(fileobj), None)

    def modify(self, fileobj, events, data=None):  # pragma: no cover - unused
        self.unregister(fileobj)
        return self.register(fileobj, events, data)

    def select(self, timeout=None):
        if _INPUT_CB[0] is not None and _queue:
            return [True]
        got = _pump(timeout)
        return [True] if got and _INPUT_CB[0] is not None else []

    def get_map(self):
        return self._map

    def get_key(self, fileobj):  # pragma: no cover - unused
        return self._map[hash(fileobj)]

    def close(self) -> None:
        pass


class _DemoLoop(asyncio.selector_events.BaseSelectorEventLoop):
    """A blocking-capable loop over the terminal bridge. The self-pipe
    and signal handlers assume an OS this runtime does not have; nothing
    here is multi-threaded, so dropping them costs nothing. Executor work
    (prompt_toolkit history loading) runs inline for the same reason."""

    def __init__(self) -> None:
        super().__init__(_TermSelector())

    def _make_self_pipe(self) -> None:
        self._ssock = None
        self._csock = None
        self._internal_fds = 0

    def _close_self_pipe(self) -> None:
        pass

    def _write_to_self(self) -> None:
        pass

    def add_signal_handler(self, sig, callback, *args) -> None:
        pass

    def remove_signal_handler(self, sig) -> bool:
        return False

    def _process_events(self, event_list) -> None:
        if event_list and _INPUT_CB[0] is not None:
            _INPUT_CB[0]()

    def run_in_executor(self, executor, func, *args):
        future = self.create_future()
        try:
            future.set_result(func(*args))
        except BaseException as e:
            future.set_exception(e)
        return future

    async def shutdown_default_executor(self, timeout=None) -> None:
        return


class _DemoPolicy(asyncio.events.BaseDefaultEventLoopPolicy):
    _loop_factory = _DemoLoop


def _demo_run(coro, *, debug=None, loop_factory=None):
    """`asyncio.run` over the demo loop. Pyodide replaces `asyncio.run`
    with a WebLoop stack-switching version that ignores the installed
    policy, so `Application.run`'s final `asyncio.run(coro)` must be
    answered by this instead — the stdlib semantics, one loop per call."""
    loop = _DemoLoop()
    # Pyodide's WebLoop registers itself as the running loop for the
    # life of the runtime; while the demo loop blocks, nothing else can
    # run anyway, so the marker is cleared for the duration.
    outer = asyncio.events._get_running_loop()
    asyncio.events._set_running_loop(None)
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        shutdown = loop.shutdown_asyncgens()
        try:
            loop.run_until_complete(shutdown)
        except Exception:
            shutdown.close()
        asyncio.set_event_loop(None)
        loop.close()
        asyncio.events._set_running_loop(outer)


# ---------- prompt_toolkit I/O over the bridge ----------


def _build_ptk_io():
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input.base import Input
    from prompt_toolkit.input.vt100_parser import Vt100Parser
    from prompt_toolkit.output.color_depth import ColorDepth
    from prompt_toolkit.output.vt100 import Vt100_Output
    from prompt_toolkit.utils import DummyContext

    class TermInput(Input):
        """prompt_toolkit input over the byte queue — no pipe, no fd, no
        termios: `attach` hands the ready-callback to the demo loop, and
        `read_keys` drains whatever the selector saw arrive."""

        def __init__(self) -> None:
            self._keys: list = []
            self._decoder = codecs.getincrementaldecoder("utf-8")("surrogateescape")
            self._parser = Vt100Parser(self._keys.append)

        @property
        def closed(self) -> bool:
            return False

        def fileno(self) -> int:
            raise NotImplementedError

        def typeahead_hash(self) -> str:
            return "demo-terminal-input"

        def read_keys(self):
            data = bytes(_queue)
            _queue.clear()
            self._parser.feed(self._decoder.decode(data))
            keys = self._keys[:]
            del self._keys[:]
            return keys

        def flush_keys(self):
            self._parser.flush()
            keys = self._keys[:]
            del self._keys[:]
            return keys

        @contextmanager
        def attach(self, input_ready_callback):
            previous = _INPUT_CB[0]
            _INPUT_CB[0] = input_ready_callback
            try:
                yield
            finally:
                _INPUT_CB[0] = previous

        @contextmanager
        def detach(self):
            previous = _INPUT_CB[0]
            _INPUT_CB[0] = None
            try:
                yield
            finally:
                _INPUT_CB[0] = previous

        def raw_mode(self):
            return DummyContext()

        def cooked_mode(self):
            return DummyContext()

        def close(self) -> None:
            pass

    def get_size() -> Size:
        columns, rows = termbridge.size()
        return Size(rows=int(rows), columns=int(columns))

    output = Vt100_Output(
        sys.stdout,
        get_size,
        term="xterm-256color",
        default_color_depth=ColorDepth.DEPTH_24_BIT,
    )
    return TermInput(), output


# ---------- the terminal queries (tty.ask), for real ----------

_ASK_DEADLINE = 1.0  # xterm.js answers both queries; the deadline is for a host that cannot


def _demo_ask(query: str, response: "re.Pattern[bytes]") -> "re.Match[bytes] | None":
    """The product's `tty.ask` over the bridge: write the query, read the
    queue until the answer matches. Bytes typed while a query is in
    flight are dropped with the match, exactly as the termios original
    drops them."""
    _flush_out()
    termbridge.write(query)
    deadline = time.monotonic() + _ASK_DEADLINE
    while True:
        match = response.search(bytes(_queue))
        if match is not None:
            del _queue[: match.end()]
            return match
        left = deadline - time.monotonic()
        if left <= 0 or not _pump(left):
            return None


# ---------- input() with echo (the confirm prompts) ----------


def _demo_input(prompt: str = "") -> str:
    """A cooked-mode line read: the kernel's echo and line editing, done
    by hand because there is no kernel. Used by the overwrite confirm and
    the persona ask; Ctrl+C answers like a signal would."""
    writer = sys.stdout
    writer.write(str(prompt))
    writer.flush()
    decoder = codecs.getincrementaldecoder("utf-8")()
    line = ""
    while True:
        if not _queue:
            _pump(None)
        byte = bytes([_queue.pop(0)])
        if byte in (b"\r", b"\n"):
            writer.write("\r\n")
            writer.flush()
            return line
        if byte == b"\x03":
            writer.write("\r\n")
            writer.flush()
            raise KeyboardInterrupt
        if byte == b"\x04" and not line:
            raise EOFError
        if byte in (b"\x7f", b"\x08"):
            if line:
                line = line[:-1]
                writer.write("\b \b")
                writer.flush()
            continue
        ch = decoder.decode(byte)
        if ch and ch.isprintable():
            line += ch
            writer.write(ch)
            writer.flush()


# ---------- the scripted model ----------

PROVIDER = "demo"
MODEL = "demo-model"
CONTEXT_SIZE = 32768  # matches the web demo's WINDOW (demo/web/store.js)

_FIRST_TOKEN_WAIT = 0.55
_CHUNK_WAIT = 0.045
_CHUNK_WORDS = 3


def _scan_stream_keys() -> None:
    """What the kernel and the watcher thread do during a real stream:
    Ctrl+C interrupts, Ctrl+R interrupts asking for a fresh take, and
    every other byte typed mid-stream is discarded (the watcher flushes
    the tty on exit for the same reason)."""
    import otaku.terminal.chat.stream as chat_stream

    data = bytes(_queue)
    _queue.clear()
    if b"\x12" in data:
        watcher = getattr(chat_stream._StreamWatcher, "current", None)
        if watcher is not None:
            watcher.regen_requested = True
        raise KeyboardInterrupt
    if b"\x03" in data:
        raise KeyboardInterrupt


def _pace(seconds: float) -> None:
    """Let `seconds` of streaming time pass, watching the keys the whole
    while — the demo's stand-in for a model that takes time to answer."""
    end = time.monotonic() + seconds
    while True:
        _scan_stream_keys()
        left = end - time.monotonic()
        if left <= 0:
            return
        _pump(min(left, 0.1))
        _scan_stream_keys()


def _build_demo_client():
    import demo_script

    from otaku.providers.openai.client import OpenAIClient
    from otaku.providers.openai.completion import OpenAICompletion, Reasoning, Text
    from otaku.providers.openai.models import Locality, ModelInfo, ModelState, OpenAIModels

    class DemoModels(OpenAIModels):
        def _list(self, http):
            # Loaded and sized like a serving engine, or /info and the
            # picker would show a model nobody started.
            return [
                ModelInfo(
                    name=MODEL,
                    max_context_catalogue=CONTEXT_SIZE,
                    max_context_loaded=CONTEXT_SIZE,
                    state=ModelState.LOADED,
                )
            ]

    class DemoCompletion(OpenAICompletion):
        """The wire calls answered from `demo_script` instead of a
        socket. The orchestration above `_generate` — the knob and
        sampler retries, cancel-and-keep on close — is the base class's
        own; this hook does what the base's does with the request log
        and the caller's `Stats`: the request recorded, the stats filled
        as the stream goes, the answer filed however it ends, the stats
        yielded whole at a clean end."""

        def _generate(self, url, body, purpose, timeout, read_delta, stats, cut=None, retried=""):
            name = self._config.name
            request_id = ""
            if self._request_sink is not None:
                request_id = self._request_sink.record_request(name, purpose, body)
            reasoning, text = demo_script.reply(body, purpose)
            start = time.monotonic()
            stats.prompt_tokens = _estimate(body)
            spoken: list[str] = []
            thought: list[str] = []
            status = "cancelled"  # a close before the end is a cancel, as on the wire
            try:
                if purpose != "chat":
                    # Background work (extraction, rollups, warm-ups) is
                    # nobody's screen: answer whole, at once.
                    stats.first_token_seconds = 0.01
                    spoken.append(text)
                    yield Text(text=text)
                else:
                    _pace(_FIRST_TOKEN_WAIT)
                    stats.first_token_seconds = time.monotonic() - start
                    if reasoning:
                        for chunk in _chunks(reasoning):
                            thought.append(chunk)
                            yield Reasoning(text=chunk)
                            _pace(_CHUNK_WAIT)
                    for chunk in _chunks(text):
                        spoken.append(chunk)
                        yield Text(text=chunk)
                        _pace(_CHUNK_WAIT)
                stats.completion_tokens = max(1, len("".join(spoken)) // 4)
                stats.finish_reason = "stop"
                status = "ok"
            finally:
                stats.total_seconds = time.monotonic() - start
                if self._request_sink is not None and request_id:
                    self._request_sink.record_answer(
                        name,
                        purpose,
                        request_id,
                        status=status,
                        stats=stats,
                        text="".join(spoken),
                        reasoning="".join(thought),
                    )
            yield stats

    class DemoClient(OpenAIClient):
        """The scripted provider: the real client with its two halves
        answering from the script."""

        id = "demo"
        label = "demo"
        locality = Locality.LOCAL
        models_class = DemoModels
        completion_class = DemoCompletion

    def _estimate(body) -> int:
        total = 0
        for message in body.get("messages", []):
            content = message.get("content", "")
            if isinstance(content, list):
                content = " ".join(str(part.get("text", "")) for part in content)
            total += len(str(content))
        return max(1, total // 4)

    def _chunks(text: str):
        words = re.split(r"(?<=\s)", text)
        for i in range(0, len(words), _CHUNK_WORDS):
            yield "".join(words[i : i + _CHUNK_WORDS])

    return DemoClient


# ---------- the thread stand-ins ----------


def _patch_threads() -> None:
    import otaku.terminal.chat.stream as chat_stream
    import otaku.terminal.screens.models as screens_models
    from otaku.providers import smoothing
    from otaku.providers.registry import Registry
    from otaku.worker import Worker
    from otaku.worker.extraction import Extractor, PassResult, Report

    class DemoSpinner:
        """One static frame where the real spinner animates on a thread;
        stop erases it the same way."""

        def __init__(self) -> None:
            self._shown = False

        def start(self) -> None:
            if not self._shown:
                sys.stdout.write("⠋ ")
                sys.stdout.flush()
                self._shown = True

        def stop(self) -> None:
            if self._shown:
                sys.stdout.write("\r\x1b[2K")
                sys.stdout.flush()
                self._shown = False

    class DemoStreamWatcher:
        """The in-stream Ctrl+R watcher without its thread: the pacing
        loop (`_scan_stream_keys`) reads the keys and flags the CURRENT
        watcher, because the pacing loop is where stream time passes."""

        current = None

        def __init__(self) -> None:
            self.regen_requested = False

        def __enter__(self):
            DemoStreamWatcher.current = self
            return self

        def __exit__(self, *exc) -> None:
            DemoStreamWatcher.current = None

    chat_stream.Spinner = DemoSpinner
    chat_stream._StreamWatcher = DemoStreamWatcher

    # The worker's two daemon threads cannot start; the demo never
    # schedules idle passes (lore_enabled=False), and a FORCED pass —
    # /extract, /import — runs inline, which is exactly what their
    # foreground waits want. The body mirrors Worker._loop's one-job arc.
    def demo_start(self) -> None:
        pass

    def demo_schedule(self, job, *, now: bool = False) -> None:
        if not now:
            return
        self._abort = threading.Event()
        self._deferred = threading.Event()
        result = PassResult.FAILED
        report = Report()
        store = None
        try:
            try:
                store = self._store_factory()
                client = self._providers.get(job.provider)
                extractor = Extractor(
                    store,
                    client,
                    job.model,
                    job.story_id,
                    extraction_settings=job.extraction_settings,
                    cancel=self._abort,
                    progress=lambda line: None,
                    log=self._log.record,
                )
                result, report = extractor.run(force=job.force)
            except Exception as e:
                with contextlib.suppress(Exception):
                    self._log.record(
                        f"extraction crashed (story {job.story_id}): {type(e).__name__}"
                    )
                    if self._errors is not None:
                        self._errors.record(f"lore pass (story {job.story_id})", e)
            if job.on_done is not None:
                with contextlib.suppress(Exception):
                    job.on_done(result, report)
        finally:
            if store is not None:
                store.close()

    Worker.start = demo_start
    Worker.schedule = demo_schedule

    # The completion's relay pumps a stream on a thread of its own so a
    # cancel can cut a request mid-wait (`providers.smoothing`); the
    # forced pass asks for that relay. The demo has no thread and
    # nothing to cut — its pacing is the script's own — so the chunks
    # pass straight through, the tick and the cut left unused.
    def demo_smoothen(chunks, on_idle=None, cut=None, *, paced=True):
        yield from chunks

    smoothing.smoothen = demo_smoothen

    # Provider fan-out without its pool: one scripted provider answers
    # instantly, so configuration order needs no overlap.
    def demo_map(self, fn, names=None):
        asked = list(self.configs) if names is None else list(names)
        return [fn(name, self.configs[name]) for name in asked]

    Registry.map = demo_map

    # The model picker's threads all run a fire-once body (the animator
    # loops only while `busy`, which the inline body has already
    # cleared); running them synchronously keeps every path alive.
    class InlineThread:
        def __init__(self, target=None, daemon=None, name=None, args=(), kwargs=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self) -> None:
            if self._target is not None:
                self._target(*self._args, **self._kwargs)

        def join(self, timeout=None) -> None:
            pass

    screens_models.threading = SimpleNamespace(
        Thread=InlineThread, Lock=threading.Lock, Event=threading.Event
    )


# ---------- the state dir ----------


def _seed_state_dir(root: Path) -> None:
    """What scenarios/support/harness.py seeds, minus the HTTP server:
    one scripted provider, smoothing off (the pacing is the script's),
    idle extraction off (a browser tab has no worker thread to run it —
    /extract is the demo's door), samples on."""
    from otaku.backend.paths import Paths
    from otaku.settings import config as config_file
    from otaku.settings import providers as providers_file
    from otaku.settings import write_atomic
    from otaku.settings.providers import ProviderConfig

    paths = Paths.resolve(root)
    paths.ensure_tree()
    write_atomic(
        paths.config_file,
        config_file.Config(smooth_streaming=False, lore_enabled=False).to_toml(),
    )
    write_atomic(
        paths.providers_file,
        providers_file.render(
            {PROVIDER: ProviderConfig(name=PROVIDER, url="https://demo.model/v1")}
        ),
    )


# ---------- main ----------


def main() -> None:
    import os

    os.environ["TERM"] = "xterm-256color"
    os.environ.pop("NO_COLOR", None)
    os.environ.pop("COLORFGBG", None)
    columns, rows = termbridge.size()
    os.environ["COLUMNS"] = str(int(columns))
    os.environ["LINES"] = str(int(rows))

    sys.stdout = _TermWriter()
    sys.stderr = _TermStderr()
    sys.stdin = _TermStdin()
    sys.__stdout__ = sys.stdout  # the status line writes past the wrappers
    sys.__stderr__ = sys.stderr
    builtins.input = _demo_input
    asyncio.set_event_loop_policy(_DemoPolicy())
    asyncio.run = _demo_run
    asyncio.runners.run = _demo_run

    from prompt_toolkit.application import create_app_session

    import otaku.terminal.chat.bindings as bindings
    import otaku.terminal.tty as tty_mod
    from otaku.backend import launch as backend_launch
    from otaku.backend.api import providers as api_providers
    from otaku.providers import registry as providers_registry
    from otaku.terminal.chat import loop as chat_loop

    tty_mod.ask = _demo_ask
    # No local engines to detect: first-run and the ensure-providers
    # migration would otherwise write sections probing this machine.
    backend_launch.autoconfigure_providers = lambda: {}
    providers_registry.ALL_CLIENTS[PROVIDER] = _build_demo_client()
    _patch_threads()

    # Two doors a tab cannot honor: /web binds a socket, and /bye — with
    # Ctrl+D, the app's own shortcut submitting /bye on an empty line —
    # would end a session only a reload can restart. Both answer with
    # one sentence instead.
    def demo_disabled(chat, raw: str) -> None:
        chat.say("This command is disabled in the demo.")

    bindings._INTERACTIVE["/web"] = demo_disabled
    bindings._INTERACTIVE["/bye"] = demo_disabled

    # /card is REAL here — parser, persona ask, join, greeting all run —
    # so a bare /card points at the card the demo ships instead of
    # leaving the visitor with no file to name.
    real_card = bindings._INTERACTIVE["/card"]

    def demo_card(chat, raw: str) -> None:
        if not raw.strip():
            chat.say("Usage: /card FILE [NAME] — the demo ships one: /card /cards/odo.json")
            chat.ledger.invalidate()
            return
        real_card(chat, raw)

    bindings._INTERACTIVE["/card"] = demo_card
    # /context pages through `less`; the terminal here scrolls instead.
    bindings.click = SimpleNamespace(
        echo_via_pager=lambda text, color=True: (
            sys.stdout.write(text if text.endswith("\n") else text + "\n"),
            sys.stdout.flush(),
        )
    )

    root = Path("/state/otaku")
    _seed_state_dir(root)
    # The visitor's "own" file, outside the state dir: the card /card
    # imports — a real file the real parser reads.
    import demo_script

    cards = Path("/cards")
    cards.mkdir(parents=True, exist_ok=True)
    (cards / "odo.json").write_text(demo_script.SAMPLE_CARD, encoding="utf-8")
    session = backend_launch.open_session(root)
    if not session.model:
        api_providers.switch_model(session, PROVIDER, MODEL)

    term_input, term_output = _build_ptk_io()
    try:
        with create_app_session(input=term_input, output=term_output):
            chat_loop.run(session)
    finally:
        session.close()
    sys.stdout.write("\r\n\x1b[2mThe session is over — reload the page to start again.\x1b[0m\r\n")
    sys.stdout.flush()

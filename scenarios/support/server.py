"""A scripted OpenAI-compatible server the real app talks to.

Scenario tests need a real protocol peer, not a real model: the unmodified
application — registry, streaming, request log and all — connects to this
server over HTTP and gets deterministic, instant replies. What it answers
comes from `script`, a callable from the request body to the completion
text — or to a `(thinking, text)` pair, the thinking streamed as a
reasoning delta before the content. `default_script` recognizes the lore
prompts by their fixed openings and answers with canned extraction JSON
and rollups, so even the whole extraction pipeline plays end to end
offline. Every request body is kept in `requests` for assertions — the
wire promise is checked against it.
"""

import contextlib
import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CHAT_REPLY = "The light went out, and something stirred in the dark."
STORY_SO_FAR = "The guest reached the gate and met the Keeper."
CHARACTER_HISTORY = "The Keeper remembers the guest."
EXTRACTION = {
    "scene": {"title": "The Meeting", "summary": "A guest came in and met the Keeper."},
    "speakers": [],
    "characters": [{"name": "Keeper", "aliases": [], "description": "warden of the gate"}],
    "journals": [{"character": "Keeper", "entry": "I saw the guest.", "state": "at the gate"}],
}


class ModelServer:
    """The server, on a free localhost port from construction to `close`.
    `script` is swappable per test; `reset` restores the default and
    clears the recorded requests. `managed=True` adds ollama's native
    endpoints — `loaded` is the load state, mutated by /api/generate the
    way the real engine mutates it. `chunk_delay` slows the stream down
    for stories that act mid-stream."""

    def __init__(self, models: tuple[str, ...] = ("test-model",), *, managed: bool = False) -> None:
        self.models = list(models)
        self.managed = managed
        self.loaded: set[str] = set()
        self.sizes: dict[str, int] = {}  # reported bytes per model; absent → 1 MB
        self.contexts: dict[str, int] = {}  # context per model; absent → 8192
        self.status = False  # True → serve omlx's rich /v1/models/status
        self.credits: tuple[float, float] | None = (
            10.0,
            0.0,
        )  # openrouter (total, used); None → 404
        self.balances: dict[str, Any] = {"usd_balance": "10"}  # nanogpt check-balance; empty → 404
        self.api_key: str | None = None  # set → balance endpoints demand this Bearer key
        self.chunk_delay = 0.0
        self.chunk_size: int | None = None  # stream in pieces this long; None → thirds
        self.fail_after: int | None = None  # abort the stream after N content chunks
        self.requests: list[dict[str, Any]] = []
        self.script: Callable[[dict[str, Any]], str | tuple[str, str]] = default_script
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                pass  # quiet — test output belongs to the tests

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0].rstrip("/")
                if outer.status and path.endswith("/models/status"):
                    self._json(
                        {
                            "models": [
                                {
                                    "id": name,
                                    "actual_size": outer.sizes.get(name, 1_048_576),
                                    "loaded": name in outer.loaded,
                                    "max_context_window": outer.contexts.get(name, 8192),
                                }
                                for name in outer.models
                            ]
                        }
                    )
                    return
                if path.endswith("/models"):
                    # `context_length` rides along when a test sets it —
                    # the cloud catalogs report it there.
                    rows: list[dict[str, Any]] = []
                    for name in outer.models:
                        entry: dict[str, Any] = {"id": name}
                        if name in outer.contexts:
                            entry["context_length"] = outer.contexts[name]
                        rows.append(entry)
                    self._json({"data": rows})
                elif path.endswith("/credits") and outer.credits is not None:
                    if not self._authorized():
                        return
                    total, used = outer.credits
                    self._json({"data": {"total_credits": total, "total_usage": used}})
                elif outer.managed and path.endswith("/api/ps"):
                    self._json(
                        {
                            "models": [
                                {
                                    "name": name,
                                    "size": outer.sizes.get(name, 1_000_000),
                                    "context_length": outer.contexts.get(name, 8192),
                                }
                                for name in sorted(outer.loaded)
                            ]
                        }
                    )
                elif outer.managed and path.endswith("/api/tags"):
                    self._json(
                        {
                            "models": [
                                {"name": name, "size": outer.sizes.get(name, 1_000_000)}
                                for name in outer.models
                            ]
                        }
                    )
                else:
                    self.send_response(404)
                    self.end_headers()

            def _authorized(self) -> bool:
                """True unless the test armed `api_key` and this request
                carries a different Bearer key — then a 401 is sent."""
                if outer.api_key is None:
                    return True
                if self.headers.get("Authorization") == f"Bearer {outer.api_key}":
                    return True
                self.send_response(401)
                self.end_headers()
                return False

            def _json(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(body)
                if outer.balances and self.path.rstrip("/").endswith("/check-balance"):
                    if not self._authorized():
                        return
                    self._json(outer.balances)
                    return
                if outer.managed and self.path.rstrip("/").endswith("/api/show"):
                    context = outer.contexts.get(str(body.get("model")), 8192)
                    self._json({"model_info": {"test.context_length": context}})
                    return
                if outer.managed and self.path.rstrip("/").endswith("/api/generate"):
                    # Ollama's load door: an empty prompt with a keep_alive
                    # loads the model; keep_alive 0 unloads it.
                    if body.get("keep_alive") == 0:
                        outer.loaded.discard(str(body.get("model")))
                    else:
                        outer.loaded.add(str(body.get("model")))
                    self._json({})
                    return
                result = outer.script(body)
                thinking, text = result if isinstance(result, tuple) else ("", result)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                if outer.fail_after is not None:
                    # Promise more than will ever come, so the hangup below
                    # is a transport ERROR client-side, not a clean end.
                    self.send_header("Content-Length", "1048576")
                self.end_headers()
                # A client hanging up mid-stream is a legitimate scenario
                # (an interrupted reply), not server noise worth a trace.
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    if thinking:
                        self._event({"choices": [{"delta": {"reasoning_content": thinking}}]})
                    # A few chunks, so the streaming path is exercised for real.
                    third = outer.chunk_size or max(1, len(text) // 3)
                    for sent, i in enumerate(range(0, len(text), third)):
                        if outer.fail_after is not None and sent >= outer.fail_after:
                            # A mid-stream transport failure: hang up hard.
                            self.wfile.flush()
                            self.connection.close()
                            return
                        if outer.chunk_delay:
                            time.sleep(outer.chunk_delay)
                        event = {"choices": [{"delta": {"content": text[i : i + third]}}]}
                        self._event(event)
                    self._event(
                        {
                            "choices": [{"delta": {}}],
                            "usage": {"prompt_tokens": 7, "completion_tokens": 5},
                        }
                    )
                    self.wfile.write(b"data: [DONE]\n\n")

            def _event(self, payload: dict[str, Any]) -> None:
                self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def reset(self) -> None:
        self.script = default_script
        self.requests.clear()

    def close(self) -> None:
        self._httpd.shutdown()


def chat_request(server: ModelServer, last_line: str) -> dict[str, Any]:
    """The recorded request whose newest message ends with `last_line` —
    the turn under test, picked explicitly because the post-close prompt
    warm-up races the next turn onto the server, making the newest
    recorded request ambiguous."""
    for body in reversed(server.requests):
        if str(body["messages"][-1]["content"]).endswith(last_line):
            return body
    raise AssertionError(f"no recorded request ends with {last_line!r}")


def default_script(body: dict[str, Any]) -> str:
    """Answers by prompt kind: the extraction prompt gets valid JSON, the
    rollup prompts get one-line rollups, anything else gets the chat
    reply. Recognition is by each lore prompt's fixed opening words."""
    prompt = str(body.get("messages", [{}])[-1].get("content", ""))
    if "You are a story analyst" in prompt:
        return json.dumps(EXTRACTION, ensure_ascii=False)
    if prompt.startswith("Combine the scene summaries"):
        return STORY_SO_FAR
    if prompt.startswith("Write ") and "'s history" in prompt:
        return CHARACTER_HISTORY
    return CHAT_REPLY


def numbered_script(summary_chars: int = 0) -> Callable[[dict[str, Any]], str]:
    """Like the default script, but every extraction call closes a DISTINCT
    scene — "Scene 1", "Scene 2", … in call order — so long-story tests can
    assert which scene ended up where. `summary_chars` pads each summary to
    roughly that size, for stories about the recap outgrowing its budget."""
    state = {"scene": 0}

    def script(body: dict[str, Any]) -> str:
        prompt = str(body.get("messages", [{}])[-1].get("content", ""))
        if "You are a story analyst" not in prompt:
            return default_script(body)
        state["scene"] += 1
        n = state["scene"]
        summary = f"Scene summary {n}."
        if summary_chars > len(summary):
            summary += " x" * ((summary_chars - len(summary)) // 2)
        extraction = {
            "scene": {"title": f"Scene {n}", "summary": summary},
            "speakers": [],
            "characters": [{"name": "Keeper", "description": "warden of the gate"}]
            if n == 1
            else [],
            "journals": [{"character": "Keeper", "entry": f"Entry {n}.", "state": f"state {n}"}],
        }
        return json.dumps(extraction, ensure_ascii=False)

    return script

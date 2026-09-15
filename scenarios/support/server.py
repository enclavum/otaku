"""A scripted OpenAI-compatible server the real app talks to.

Scenario tests need a real protocol peer, not a real model: the unmodified
application — registry, streaming, request log and all — connects to this
server over HTTP and gets deterministic, instant replies. What it answers
comes from `script`, a callable from the request body to the completion
text — or to a `(thinking, text)` pair, the thinking streamed as a
reasoning delta before the content. `default_script` recognizes the lore
prompts by their fixed openings and answers with canned extraction JSON
and rollups, so even the whole extraction pipeline plays end to end
offline. Every request body is kept in `requests` for assertions, and
every GET path in `gets` — the
wire promise is checked against it — and its headers in
`request_headers`, row for row.
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
REFUSAL = "refused by the script"  # what a refused POST's error message says
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
        # A single-model engine's one loaded window: llama.cpp's listing
        # `meta` and /props, KoboldCpp's true_max_context_length; None →
        # 404, not such an engine.
        self.window: int | None = None
        self.list_delay = 0.0  # seconds /models waits before answering — arrival order
        self.list_status: int | None = None  # set → /models answers this status instead
        self.status = False  # True → serve omlx's rich /v1/models/status
        self.status_code: int | None = None  # set → /models/status answers this status instead
        self.types: dict[str, str] = {}  # omlx status: model_type per model ("vlm", "llm")
        self.thinking: dict[str, bool] = {}  # omlx status: thinking_default per model
        # What a listing entry carries beyond its id and context — the
        # catalogs' capability fields, merged into the entry as sent.
        self.extras: dict[str, dict[str, Any]] = {}
        # llama.cpp's /props beyond the window (modalities, chat_template_caps).
        self.props: dict[str, Any] = {}
        self.version: dict[str, Any] | None = None  # KoboldCpp's /api/extra/version; None → 404
        # KoboldCpp's /api/v1/model `result` — "koboldcpp/<name>", "inactive",
        # or the mask a wrong key gets; None → 404
        self.kobold_model: str | None = None
        self.capabilities: dict[str, list[str]] = {}  # ollama's /api/show capabilities per model
        self.remote: set[str] = set()  # ollama tags: the names served by ollama.com
        self.unload_status: int | None = None  # set → LM Studio's /unload answers this status
        self.ps_status: int | None = None  # set → /api/ps answers this status instead
        self.token_count: int | None = None  # every count endpoint answers this; None → 404
        self.router = False  # True → llama.cpp's router listing and load/unload doors
        self.router_failed: set[str] = set()  # names whose load ends failed, unloaded
        self.router_sleeping: set[str] = set()  # loaded names the router put to sleep
        self.router_loading: set[str] = set()  # names loading by themselves: loaded once listed
        self.router_downloading: set[str] = set()  # names fetched first: loading once listed
        self.no_done = False  # True → the stream ends cleanly without [DONE]
        self.decline: str | None = None  # set → the stream is one error frame, no content
        # set → a chat POST is answered 200 with this bare `{"error": …}`
        # object and no stream: LM Studio's answer to a wrong path
        self.flat_error: str | None = None
        self.credits: tuple[float, float] | None = (
            10.0,
            0.0,
        )  # openrouter (total, used); None → 404
        self.balances: dict[str, Any] = {"usd_balance": "10"}  # nanogpt check-balance; empty → 404
        self.api_key: str | None = None  # set → balance endpoints demand this Bearer key
        self.chunk_delay = 0.0
        self.cached_tokens: int | None = None
        # set → the text wire's last frame carries NanoGPT's pricing block
        # (inputTokens, outputTokens) and no usage report
        self.pricing: tuple[int, int] | None = None  # set → usage reports this many cached
        self.chunk_size: int | None = None  # stream in pieces this long; None → thirds
        self.fail_after: int | None = None  # abort the stream after N content chunks
        # Answer a chat POST with this status instead of serving it;
        # None serves. Per request, so a test can refuse the knobbed
        # request and serve its bare retry.
        self.refuse: Callable[[dict[str, Any]], int | None] = lambda body: None
        self.refusal = REFUSAL  # the message a refusal carries; a test makes it an engine's
        self.requests: list[dict[str, Any]] = []
        self.request_headers: list[dict[str, str]] = []  # one row per POST, same order
        self.posts: list[str] = []  # every POST path, same order
        self.gets: list[str] = []  # every GET path, in order
        self.script: Callable[[dict[str, Any]], str | tuple[str, str]] = default_script
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                pass  # quiet — test output belongs to the tests

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0].rstrip("/")
                outer.gets.append(path)
                if outer.window is not None and path.endswith("/props"):
                    if not self._authorized():
                        return  # llama.cpp's key check covers /props, never /v1/models
                    props = {"default_generation_settings": {"n_ctx": outer.window}}
                    self._json({**props, **outer.props})
                    return
                if outer.version is not None and path.endswith("/api/extra/version"):
                    self._json(outer.version)
                    return
                if outer.kobold_model is not None and path.endswith("/api/v1/model"):
                    self._json({"result": outer.kobold_model})
                    return
                if outer.window is not None and path.endswith("/true_max_context_length"):
                    self._json({"value": outer.window})
                    return
                if outer.status and path.endswith("/models/status"):
                    if outer.status_code is not None:
                        self.send_response(outer.status_code)
                        self.end_headers()
                        return
                    self._json(
                        {
                            "models": [
                                {
                                    "id": name,
                                    "estimated_size": outer.sizes.get(name, 1_048_576),
                                    "loaded": name in outer.loaded,
                                    "is_loading": False,
                                    "model_context_length": outer.contexts.get(name, 8192),
                                    "max_context_window": outer.contexts.get(name, 8192),
                                    **(
                                        {"model_type": outer.types[name]}
                                        if name in outer.types
                                        else {}
                                    ),
                                    **(
                                        {"thinking_default": outer.thinking[name]}
                                        if name in outer.thinking
                                        else {}
                                    ),
                                }
                                for name in outer.models
                            ]
                        }
                    )
                    return
                if outer.managed and path.endswith("/api/v1/models"):
                    # LM Studio's registry: one entry per model, its
                    # capabilities where a test named them, a loaded
                    # instance (id = the name) where it is loaded. Behind
                    # the token, as LM Studio's is.
                    if not self._authorized():
                        return
                    self._json(
                        {
                            "models": [
                                {
                                    "key": name,
                                    "type": "llm",
                                    "size_bytes": outer.sizes.get(name, 1_048_576),
                                    "max_context_length": outer.contexts.get(name, 8192),
                                    **(
                                        {"capabilities": {"vision": "vision" in caps}}
                                        if (caps := outer.capabilities.get(name)) is not None
                                        else {}
                                    ),
                                    "loaded_instances": (
                                        [
                                            {
                                                "id": name,
                                                "config": {
                                                    "context_length": outer.contexts.get(name, 8192)
                                                },
                                            }
                                        ]
                                        if name in outer.loaded
                                        else []
                                    ),
                                }
                                for name in outer.models
                            ]
                        }
                    )
                    return
                if path.endswith("/models"):
                    if outer.list_status is not None:
                        self.send_response(outer.list_status)
                        self.end_headers()
                        return
                    if outer.list_delay:
                        time.sleep(outer.list_delay)
                    # `context_length` rides along when a test sets it —
                    # the cloud catalogs report it there.
                    rows: list[dict[str, Any]] = []
                    for name in outer.models:
                        entry: dict[str, Any] = {"id": name}
                        if name in outer.contexts:
                            entry["context_length"] = outer.contexts[name]
                        if outer.router:
                            # The router's rows: a status object, as the
                            # build in use spells it.
                            state = "loaded" if name in outer.loaded else "unloaded"
                            if name in outer.router_sleeping and name in outer.loaded:
                                state = "sleeping"
                            if name in outer.router_downloading:
                                # Fetched first (a build past b9290); the
                                # next listing finds it loading.
                                state = "downloading"
                                outer.router_downloading.discard(name)
                                outer.router_loading.add(name)
                            elif name in outer.router_loading:
                                # In flight on its own (the router autoloads);
                                # the next listing finds it loaded.
                                state = "loading"
                                outer.router_loading.discard(name)
                                outer.loaded.add(name)
                            entry["status"] = {"value": state}
                            if name in outer.router_failed:
                                entry["status"]["failed"] = True
                                entry["status"]["exit_code"] = 1
                            # The router names every entry's modalities.
                            entry["architecture"] = {
                                "input_modalities": ["text"]
                                + (
                                    ["image"]
                                    if "vision" in outer.capabilities.get(name, [])
                                    else []
                                )
                            }
                        if outer.window is not None and (not outer.router or name in outer.loaded):
                            # llama.cpp's meta, on an entry with a server
                            # behind it: the slot's window, the trained
                            # length, the size on disk.
                            entry["meta"] = {
                                "n_ctx": outer.window,
                                "n_ctx_train": outer.contexts.get(name, 8192),
                                "size": outer.sizes.get(name, 1_048_576),
                            }
                        rows.append({**entry, **outer.extras.get(name, {})})
                    self._json({"data": rows})
                elif path.endswith("/v1/key"):
                    # OpenRouter's key endpoint: the key's own facts, behind
                    # the key; nothing a test reads yet.
                    if not self._authorized():
                        return
                    self._json({"data": {"limit": None, "limit_remaining": None, "usage": 0}})
                elif path.endswith("/credits") and outer.credits is not None:
                    if not self._authorized():
                        return
                    total, used = outer.credits
                    self._json({"data": {"total_credits": total, "total_usage": used}})
                elif outer.managed and path.endswith("/api/ps"):
                    if outer.ps_status is not None:
                        self.send_response(outer.ps_status)
                        self.end_headers()
                        return
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
                    # Ollama's registry: a row names capabilities too, as the
                    # test set them for the card, and the trained length
                    # where a test set a context; a remote row is ollama.com's.
                    self._json(
                        {
                            "models": [
                                {
                                    "name": name,
                                    "size": outer.sizes.get(name, 1_000_000),
                                    **(
                                        {"capabilities": outer.capabilities[name]}
                                        if name in outer.capabilities
                                        else {}
                                    ),
                                    **(
                                        {"details": {"context_length": outer.contexts[name]}}
                                        if name in outer.contexts
                                        else {}
                                    ),
                                    **(
                                        {"remote_host": "ollama.com"}
                                        if name in outer.remote
                                        else {}
                                    ),
                                }
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
                outer.request_headers.append(dict(self.headers))
                outer.posts.append(self.path.split("?", 1)[0].rstrip("/"))
                if outer.balances and self.path.rstrip("/").endswith("/check-balance"):
                    if not self._authorized():
                        return
                    self._json(outer.balances)
                    return
                posted = self.path.split("?", 1)[0].rstrip("/")
                if outer.managed and posted.endswith("/api/show"):
                    # Ollama's card: the capabilities of one model, and its
                    # trained context length where a test set one.
                    name = str(body.get("model"))
                    card: dict[str, Any] = {"capabilities": outer.capabilities.get(name, [])}
                    if name in outer.contexts:
                        card["model_info"] = {
                            "general.architecture": "llama",
                            "llama.context_length": outer.contexts[name],
                        }
                    self._json(card)
                    return
                if (
                    outer.status
                    and posted.endswith(("/load", "/unload"))
                    and "/v1/models/" in posted
                ):
                    # omlx's doors: the model is in the path, the state flips.
                    name = posted.split("/v1/models/", 1)[1].rsplit("/", 1)[0]
                    (outer.loaded.add if posted.endswith("/load") else outer.loaded.discard)(name)
                    self._json({"status": "ok"})
                    return
                if outer.managed and posted.endswith(
                    ("/api/v1/models/load", "/api/v1/models/unload")
                ):
                    # LM Studio's doors: a model to load, an instance to unload.
                    if outer.unload_status is not None and posted.endswith("/unload"):
                        self.send_response(outer.unload_status)
                        self.end_headers()
                        return
                    name = str(body.get("model") or body.get("instance_id"))
                    (outer.loaded.add if posted.endswith("/load") else outer.loaded.discard)(name)
                    self._json({})
                    return
                if outer.router and posted.endswith(("/models/load", "/models/unload")):
                    # The router's doors: the order is taken at once and
                    # the state flips with it.
                    if posted.endswith("/models/load"):
                        if str(body.get("model")) not in outer.router_failed:
                            outer.loaded.add(str(body.get("model")))
                    else:
                        outer.loaded.discard(str(body.get("model")))
                    self._json({"success": True})
                    return
                if outer.token_count is not None and posted.endswith(
                    "/chat/completions/input_tokens"
                ):
                    self._json({"input_tokens": outer.token_count})  # llama.cpp, the chat body
                    return
                if outer.token_count is not None and posted.endswith("/tokenize"):
                    self._json({"tokens": [0] * outer.token_count})  # llama.cpp, a raw prompt
                    return
                if outer.token_count is not None and posted.endswith("/api/extra/tokencount"):
                    self._json({"value": outer.token_count})  # KoboldCpp, either shape
                    return
                if outer.token_count is not None and posted.endswith("/v1/messages/count_tokens"):
                    self._json({"input_tokens": outer.token_count})  # omlx, Anthropic's shape
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
                status = outer.refuse(body)
                if status is not None:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": outer.refusal}}).encode())
                    return
                result = outer.script(body)
                thinking, text = result if isinstance(result, tuple) else ("", result)
                # The text wire: the raw continuation, in the completion
                # frame's shape — no thinking delta exists there.
                as_text = posted.endswith("/completions") and not posted.endswith(
                    "/chat/completions"
                )
                if outer.flat_error is not None:
                    # A wrong path's answer: a 200 with a bare error
                    # object and no stream (LM Studio's).
                    self._json({"error": outer.flat_error})
                    return
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
                    if outer.decline is not None:
                        # A refusal frame where content would be: the
                        # provider's own sentence, then a clean end.
                        self._event({"error": {"message": outer.decline}})
                        self.wfile.write(b"data: [DONE]\n\n")
                        return
                    if thinking and not as_text:
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
                        piece = text[i : i + third]
                        choice = {"text": piece} if as_text else {"delta": {"content": piece}}
                        self._event({"choices": [choice]})
                    if as_text and outer.pricing is not None:
                        prompt_count, completion_count = outer.pricing
                        pricing = {"inputTokens": prompt_count, "outputTokens": completion_count}
                        self._event({"choices": [{"text": ""}], "x_nanogpt_pricing": pricing})
                    else:
                        usage: dict[str, Any] = {"prompt_tokens": 7, "completion_tokens": 5}
                        if outer.cached_tokens is not None:
                            usage["prompt_tokens_details"] = {"cached_tokens": outer.cached_tokens}
                        self._event({"choices": [{"delta": {}}], "usage": usage})
                    if not outer.no_done:
                        self.wfile.write(b"data: [DONE]\n\n")

            def _event(self, payload: dict[str, Any]) -> None:
                self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"
        # The poll interval is `shutdown`'s latency: the loop only notices
        # the request between selects, and the server is torn down once
        # per test. The stdlib default of half a second would cost the
        # suite more than everything it actually runs.
        serve = self._httpd.serve_forever
        threading.Thread(target=lambda: serve(poll_interval=0.01), daemon=True).start()

    def reset(self) -> None:
        self.script = default_script
        self.refuse = lambda body: None
        self.refusal = REFUSAL
        self.requests.clear()
        self.request_headers.clear()
        self.posts.clear()
        self.gets.clear()

    def close(self) -> None:
        # The socket too, so the port is really dead afterwards: a
        # connection is refused instead of accepted and never answered.
        self._httpd.shutdown()
        self._httpd.server_close()


def content_text(message: dict[str, Any]) -> str:
    """A recorded message's text, whichever shape it was sent in: a plain
    string, or the parts form the prompt-cache markers use."""
    content = message.get("content", "")
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content)


def chat_request(server: ModelServer, last_line: str) -> dict[str, Any]:
    """The recorded request whose newest message ends with `last_line` —
    the turn under test, picked explicitly because the post-close prompt
    warm-up races the next turn onto the server, making the newest
    recorded request ambiguous."""
    for body in reversed(server.requests):
        if content_text(body["messages"][-1]).endswith(last_line):
            return body
    raise AssertionError(f"no recorded request ends with {last_line!r}")


def default_script(body: dict[str, Any]) -> str:
    """Answers by prompt kind: the extraction prompt gets valid JSON, the
    rollup prompts get one-line rollups, anything else gets the chat
    reply. Recognition is by each lore prompt's fixed opening words."""
    prompt = content_text(body.get("messages", [{}])[-1])
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
        prompt = content_text(body.get("messages", [{}])[-1])
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

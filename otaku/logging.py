"""Day-rotated logs under one directory, handed in as a plain Path.

One base and three logs, one class each in this one module: `RequestLog`
(every model-bound request body AND, as its own paired line, the answer
that came back with its timings — both sealed with the cipher the
caller hands in, the session's, so the log protects exactly what the
database protects; the timings and token counts ride the plaintext
envelope like `provider` and `purpose`, numbers and labels being the
class of thing the envelope already carries), `SystemLog` (the app's
account of unattended work; CONTENT-FREE by contract: ids and counts,
never prose), and `ErrorLog` (every contained crash's traceback; frames
and messages only, NEVER locals — this file sits in plain text beside a
possibly-encrypted database). All best-effort: a logging failure warns
on stderr once and never blocks anything — the one sanctioned stderr
voice below cli. The view functions at the bottom render the logs for
`otaku logs`.
"""

import base64
import json
import re
import secrets
import sys
import threading
import traceback
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from otaku.encryption import Cipher, PlainCipher
from otaku.formatting import decode_text, printable
from otaku.providers import Stats

__all__ = ["DailyLog", "Entry", "ErrorLog", "RequestLog", "SystemLog"]


class DailyLog:
    """The base every log shares: `<prefix>YYYYMMDD<suffix>` files, which
    days exist, and the one locked mkdir-append-warn write body."""

    _prefix: ClassVar[str]
    _suffix: ClassVar[str]
    # What a failed write calls itself on stderr; each log names its own,
    # so the warning says which of the three could not be written.
    _failure: ClassVar[str]

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._lock = threading.Lock()  # appends come from worker and REPL threads
        self._warned = False

    def get_days(self) -> list[tuple[str, int]]:
        """The available log days as (YYYYMMDD, file size), oldest first."""
        if not self._dir.exists():
            return []
        out: list[tuple[str, int]] = []
        for path in sorted(self._dir.glob(f"{self._prefix}????????{self._suffix}")):
            out.append((path.name[len(self._prefix) : -len(self._suffix)], path.stat().st_size))
        return out

    def get_path(self, day: str) -> Path:
        return self._dir / f"{self._prefix}{day}{self._suffix}"

    def _append(self, text: str) -> Path:
        """Append `text` to today's file under the lock; warn on stderr
        once per instance when the write fails, naming which log it was —
        three of these run at once. Returns the day's path."""
        path = self.get_path(datetime.now().astimezone().strftime("%Y%m%d"))
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(text)
        except OSError as e:
            if not self._warned:
                self._warned = True
                print(f"otaku: {self._failure}: {e}", file=sys.stderr)
        return path


class ErrorLog(DailyLog):
    _prefix = "error-"
    _suffix = ".log"
    _failure = "error logging failed"

    def record(self, context: str, exc: BaseException) -> Path:
        """Append one crash: a `=== <timestamp> <context>` header and the
        traceback. Returns the day's file (for the on-screen notice);
        never raises."""
        now = datetime.now().astimezone()
        header = f"=== {now.isoformat(timespec='seconds')} {context}\n"
        body = "".join(traceback.format_exception(exc))
        return self._append(header + body + "\n")


class SystemLog(DailyLog):
    _prefix = "system-"
    _suffix = ".log"
    _failure = "system logging failed"

    def record(self, action: str) -> None:
        """Append one timestamped action line — work done or declined
        with the reason, never scheduling noise. Never raises."""
        now = datetime.now().astimezone()
        self._append(f"{now.isoformat(timespec='seconds')} {action}\n")


@dataclass(frozen=True)
class Entry:
    """One line of the request log, either kind: a request (`body` is
    the request as sent) or its answer (`body` holds `text`, and
    `reasoning` when the model reasoned; the numbers below are set). The
    two pair by `request_id` — an answer that never arrived is a stream
    that never finished, which is itself a fact worth reading."""

    ts: str
    provider: str
    purpose: str
    body: dict[str, object] | None  # None when the body cannot be read back
    kind: str = "request"  # "request" | "answer" (absent in old lines = request)
    request_id: str = ""  # pairs an answer to its request; "" in old lines
    status: str = ""  # answers: "ok", "cancelled", or "failed: <type>"
    seconds: float | None = None  # answers: request start → stream end
    first_token_seconds: float | None = None  # answers: the prefill wait
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None


class RequestLog(DailyLog):
    _prefix = "requests-"
    _suffix = ".jsonl"
    _failure = "request log failed"

    def __init__(self, directory: Path, cipher: Cipher) -> None:
        """The envelope is plaintext; the body is sealed with `cipher`
        (readable inline JSON under the plain cipher)."""
        super().__init__(directory)
        self._cipher = cipher

    def record_request(self, provider: str, purpose: str, body: dict[str, object]) -> str:
        """Append one request, BEFORE it is sent — a crash mid-stream
        must not unrecord what left the machine. Returns the request id
        the answer is later filed under. Best-effort; never fails the
        request."""
        request_id = secrets.token_hex(4)
        self._append_entry(
            {"provider": provider, "purpose": purpose, "request_id": request_id}, body
        )
        return request_id

    def record_answer(
        self,
        provider: str,
        purpose: str,
        request_id: str,
        *,
        status: str,
        stats: Stats,
        text: str,
        reasoning: str = "",
    ) -> None:
        """Append what a request's stream came to: the status, the
        timings and token counts on the envelope, the answer's text (and
        reasoning, when the model sent any) sealed like a request body.
        Written however the stream ended — a cancelled or failed one
        records what had arrived. Best-effort; never raises."""
        envelope: dict[str, object] = {
            "provider": provider,
            "purpose": purpose,
            "kind": "answer",
            "request_id": request_id,
            "status": status,
            "seconds": round(stats.total_seconds, 2),
        }
        if stats.first_token_seconds is not None:
            envelope["first_token_seconds"] = round(stats.first_token_seconds, 2)
        for name, tokens in (
            ("prompt_tokens", stats.prompt_tokens),
            ("completion_tokens", stats.completion_tokens),
            ("cached_tokens", stats.cached_tokens),
        ):
            if tokens is not None:
                envelope[name] = tokens
        body: dict[str, object] = {"text": text}
        if reasoning:
            body["reasoning"] = reasoning
        self._append_entry(envelope, body)

    def _append_entry(self, envelope: dict[str, object], body: dict[str, object]) -> None:
        """One line: the plaintext envelope stamped, the body riding it
        sealed — inline JSON under the plain cipher."""
        entry: dict[str, object] = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            **envelope,
        }
        if isinstance(self._cipher, PlainCipher):
            entry["body"] = body
        else:
            sealed = self._cipher.seal(json.dumps(body, ensure_ascii=False).encode("utf-8"))
            entry["body_sealed"] = base64.b64encode(sealed).decode()
        self._append(json.dumps(entry, ensure_ascii=False) + "\n")

    def read(self, day: str) -> Iterator[Entry]:
        """The day's entries in order; a body the cipher cannot open (or
        a corrupt line) yields body=None rather than failing the day."""
        for line in decode_text(self.get_path(day).read_bytes()).splitlines():
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                yield Entry(ts="?", provider="?", purpose="?", body=None)
                continue
            yield Entry(
                ts=str(raw.get("ts", "?")),
                provider=str(raw.get("provider", "?")),
                purpose=str(raw.get("purpose", "?")),
                body=self._get_body(raw),
                kind=str(raw.get("kind", "request")),
                request_id=str(raw.get("request_id", "")),
                # Lines written before 0.5.0 say "outcome".
                status=str(raw.get("status", raw.get("outcome", ""))),
                seconds=_number(raw.get("seconds")),
                first_token_seconds=_number(raw.get("first_token_seconds")),
                prompt_tokens=_count(raw.get("prompt_tokens")),
                completion_tokens=_count(raw.get("completion_tokens")),
                cached_tokens=_count(raw.get("cached_tokens")),
            )

    def _get_body(self, raw: dict[str, object]) -> dict[str, object] | None:
        """One raw JSON line's body, opened with the session cipher —
        None when it cannot be read back (wrong key, corrupt line)."""
        body = raw.get("body")
        if isinstance(body, dict):
            return body
        sealed = raw.get("body_sealed")
        if not isinstance(sealed, str):
            return None
        try:
            plain = self._cipher.unseal(base64.b64decode(sealed))
            parsed = json.loads(plain.decode("utf-8"))
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None


def _number(value: object) -> float | None:
    """A read-back envelope number — None for anything a hand edit or
    corruption put there instead."""
    return float(value) if isinstance(value, int | float) else None


def _count(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


# ---------- rendering for `otaku logs` ----------


def render_plain(log: DailyLog, stamp: str) -> str:
    """One day of a plain-text log (system, error) as its pager text —
    those files are written display-ready, so rendering is reading."""
    return decode_text(log.get_path(stamp).read_bytes())


def render_requests(log: RequestLog, stamp: str) -> Iterator[str]:
    """One day's request log as pager text: per request a header row,
    the non-message fields as one JSON row, then each message; per
    answer, its status-and-timings row and the text that arrived. The
    day closes with a per-purpose summary — counts, seconds and tokens
    summed off the answers' envelopes, which is the profile of where a
    day's model time went. Display goes through `formatting.printable`;
    the log itself stores every byte."""
    asked: set[str] = set()
    answered: set[str] = set()
    spent: dict[str, list[float]] = {}  # purpose → [answers, seconds, prompt, cached, reply]
    for entry in log.read(stamp):
        if entry.kind == "answer":
            answered.add(entry.request_id)
            tally = spent.setdefault(entry.purpose, [0, 0.0, 0, 0, 0])
            tally[0] += 1
            tally[1] += entry.seconds or 0.0
            tally[2] += entry.prompt_tokens or 0
            tally[3] += entry.cached_tokens or 0
            tally[4] += entry.completion_tokens or 0
            yield from _answer_lines(entry)
            continue
        if entry.request_id:
            asked.add(entry.request_id)
        tag = f"  #{entry.request_id}" if entry.request_id else ""
        yield f"=== {entry.ts}  {entry.provider}  [{entry.purpose}]{tag}\n"
        if entry.body is None:
            yield "  <unreadable: wrong key or corrupted>\n\n"
            continue
        meta = {k: v for k, v in entry.body.items() if k != "messages"}
        yield f"  {printable(json.dumps(meta, ensure_ascii=False))}\n"
        messages = entry.body.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, list):
                        # Parts-form content (prompt-cache markers): the
                        # text is what a reader audits; the markers show
                        # in the meta row's own request, not per part.
                        content = " ".join(
                            str(part.get("text", "")) for part in content if isinstance(part, dict)
                        )
                    yield f"  [{message.get('role')}] {printable(str(content))}\n"
        yield "\n"
    yield from _summary_lines(asked, answered, spent)


def _answer_lines(entry: Entry) -> Iterator[str]:
    """One answer as the pager shows it: what the stream came to on the
    header row, then the text (and reasoning) that arrived."""
    account = [entry.status or "?"]
    if entry.seconds is not None:
        account.append(f"total {entry.seconds:.1f}s")
    if entry.first_token_seconds is not None:
        account.append(f"first token {entry.first_token_seconds:.1f}s")
    if entry.prompt_tokens is not None:
        cached = f" (cached {entry.cached_tokens:,})" if entry.cached_tokens else ""
        account.append(f"prompt {entry.prompt_tokens:,}{cached} tok")
    if entry.completion_tokens is not None:
        account.append(f"reply {entry.completion_tokens:,} tok")
    tag = f"  #{entry.request_id}" if entry.request_id else ""
    yield f"--- {entry.ts}  answer  [{entry.purpose}]{tag}  {' · '.join(account)}\n"
    if entry.body is None:
        yield "  <unreadable: wrong key or corrupted>\n\n"
        return
    # Lines written before 0.5.0 say "thinking".
    reasoning = entry.body.get("reasoning", entry.body.get("thinking"))
    if reasoning:
        yield f"  [reasoning] {printable(str(reasoning))}\n"
    yield f"  [assistant] {printable(str(entry.body.get('text', '')))}\n"
    yield "\n"


def _summary_lines(
    asked: set[str], answered: set[str], spent: dict[str, list[float]]
) -> Iterator[str]:
    """The day in numbers, per purpose — nothing sealed rides here, so
    the profile reads even without the key."""
    if not spent and not asked:
        return
    yield "=== summary\n"
    width = max((len(p) for p in spent), default=0)
    for purpose in sorted(spent):
        answers, seconds, prompt, cached, reply = spent[purpose]
        cached_note = f" (cached {int(cached):,})" if cached else ""
        yield (
            f"  {purpose:<{width}}  {int(answers)} answered · {seconds:.1f}s"
            f" · prompt {int(prompt):,}{cached_note} → reply {int(reply):,} tok\n"
        )
    unanswered = len(asked - answered)
    if unanswered:
        yield f"  {unanswered} request(s) without a recorded answer\n"


def resolve_day(day: str | None) -> str | None:
    """The file stamp (YYYYMMDD) a `logs` DAY argument names: today's
    when it is absent, either spelling — bare or dashed — when given.
    None for anything else."""
    if day is None:
        return datetime.now().astimezone().strftime("%Y%m%d")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return day.replace("-", "")
    if re.fullmatch(r"\d{8}", day):
        return day
    return None


def dashed(stamp: str) -> str:
    """YYYYMMDD as YYYY-MM-DD — the human way the listings print."""
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"


def day_rows(days: list[tuple[str, int]]) -> list[str]:
    """The `--list` rows: one dashed day and its size per line."""
    return [f"{dashed(name)}  {size:>10,} B" for name, size in days]

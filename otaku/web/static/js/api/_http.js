/* The client's machinery: how the page speaks HTTP to otaku, beside the
   twins that name what it asks (`app/`, `story/`) and the root that
   assembles them (`index.js`). A module of this layer imports it; a
   screen never does — what a screen needs of it, the root re-exports.

   Two shapes cross this line and no others: a JSON value, and the reply
   stream. A refusal is NOT an error here — the backend answers one with
   a sentence, and the caller shows it — so only a broken connection or a
   bug throws. A refusal arrives as RFC 9457 problem details under the
   status that says why — a subject that is not there (404), a state
   that rules the request out (409), a head that moved from the one this
   page read (412), input otaku cannot take (422), a service that failed
   it (502) — its sentence the `detail`; it reaches the callers as
   `{notice, refused}`, so a caller reads the flag and never the wording.
   A problem with no sentence is a fault.

   The METHOD is the lane. A `get` only reads, so the server answers it
   in the gaps of a streaming reply and a screen opens while the model
   talks; everything else moves the story and waits its turn. That is the
   whole reason a read is never written as a POST here. */

/* Where each story's head stood when this page last heard of it: every
   answer inside a story carries it as its `ETag`, a refusal of a moved
   head included, and every row the reply stream lands moves it. A write
   that moves a story's head names it back in `If-Match`, so one made
   over a story that moved meanwhile — another tab, the terminal — is
   refused with nothing written, rather than played over a chain this
   page never drew. A story this page has not read yet is written to as
   it stands (`*`). */
const heads = new Map();
const _STORY = /^\/api\/stories\/(\d+)/;

/* The stories whose head this page moved itself without learning where:
   a reply it stopped — the backend keeps what streamed, recorded once
   the socket is gone — or a fresh take that landed nothing, the reply it
   set aside gone first. Their next write plays on the head as it stands
   (`*`), and no read says otherwise meanwhile: one answered in the gap
   before the stopped reply is recorded would name the head it left. A
   write's answer settles the story again. */
const unsettled = new Set();

function heard(path, response, write) {
  const found = path.match(_STORY)?.[1];
  const tag = response.headers.get("ETag");
  if (!found || !tag) return;
  const story = Number(found);
  if (write) unsettled.delete(story);
  if (!unsettled.has(story)) heads.set(story, tag);
}

const ifMatch = (story) => ({
  "If-Match": unsettled.has(story) ? "*" : (heads.get(story) ?? "*"),
});

/* Every answer is proof that otaku is there, and every connection that
   cannot be made is proof it is not, so the page's connected state is
   settled HERE on every request rather than at each call site. A 404 or
   a 500 is not a loss: the server answered, and answered badly, which
   is a bug to show and not a state to draw. */
let onReach = () => {};
let onLost = () => {};

export function whenReached(callback) {
  onReach = callback;
}

export function whenLost(callback) {
  onLost = callback;
}

/* What a refused credential waits on: a promise that settles once the
   reader is signed in again. Set from above, the way the two hooks
   above are, so this file still imports nothing — the sign-in itself
   comes back through here. */
let onUnauthorized = null;

export function whenUnauthorized(callback) {
  onUnauthorized = callback;
}

const OUTCOMES = new Set([404, 409, 412, 422, 502]);

const isProblem = (response) =>
  response.headers.get("Content-Type")?.startsWith("application/problem+json") ?? false;

/** The sentence a problem answer carries for the reader, or null for one
    that carries none — a path nothing answers is a 404 with none. */
async function sentenceOf(response) {
  if (!isProblem(response)) return null;
  const problem = await response.clone().json();
  return typeof problem.detail === "string" ? problem.detail : null;
}

/** A refusal, in the shape the callers read: the sentence, the flag, the
    status that says why, and where a moved head now stands. */
async function refusal(response) {
  const problem = await response.json();
  return {
    notice: problem.detail,
    refused: true,
    status: response.status,
    ...("head" in problem && { head: problem.head }),
  };
}

async function ask(path, options, retried = false) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    // A reader's own Stop ABORTS the fetch: giving up on a reply is
    // not losing the server, and must not draw the page offline.
    if (e?.name !== "AbortError") onLost();
    throw e;
  }
  onReach();
  heard(path, response, (options?.method ?? "GET") !== "GET");
  /* A sign-in refused: the wrong password answers 401 WITH its sentence,
     which is how it is told from a request that needs one. It goes back
     to the caller as an answer, never to the sign-in below — that would
     leave the dialog waiting on itself. */
  if (response.status === 401 && (await sentenceOf(response)) !== null) return response;
  if (response.status === 401 && onUnauthorized && !retried) {
    /* Signed out, or the cookie ran out. The reader signs in and the
       request is asked again as if nothing had happened, so no caller
       learns there was a dialog in between — and every request refused
       meanwhile waits on the same sign-in rather than opening its own.
       Once only: a second refusal is not a sign-in problem. */
    await onUnauthorized();
    return ask(path, options, true);
  }
  /* 401 and 403 are ANSWERS, not faults: the server understood and said
     no. 401 is a token missing, wrong or out of date; 403 is a write
     that could not show it came from this page. Neither is a bug, so
     neither goes to the console as one, and nothing is SAID for either
     — the sentence below would blame otaku for deciding, and a reader
     can do nothing with the other one.

     The message is EMPTY on purpose, which is this page's way of saying
     nothing (`landed("")`): several catch sites show `e.message` with no
     flag to consult, and one of them would otherwise put a status code
     in front of a reader. What rides instead is the status, for a
     caller to act on — 401 is where the login belongs — and the request
     itself is in the network tab for anybody debugging. */
  if (response.status === 401 || response.status === 403) {
    const refused = new Error("");
    refused.status = response.status;
    throw refused;
  }
  if (OUTCOMES.has(response.status) && (await sentenceOf(response)) !== null) return response;
  if (!response.ok) {
    /* A fault, not a refusal — a refusal carries the backend's own
       sentence. There is none for this, so the page says the only thing
       it is entitled to: something about the MEDIUM. The address and the
       code go to the console, where a bug is read. */
    console.error(`${path} — ${response.status}`, await response.text());
    const error = new Error("otaku could not answer that.");
    // Answered, however badly: the caller may show this sentence, and
    // must not draw the offline state for a server that spoke.
    error.answered = true;
    throw error;
  }
  return response;
}

export async function get(path) {
  const response = await ask(path);
  return response.ok ? response.json() : refusal(response);
}

/** A write. `moves` is the story whose head it moves, named back in
    `If-Match` (`ifMatch`); null for every other. */
async function send(method, path, body, { moves = null } = {}) {
  const response = await ask(path, {
    method,
    headers: { "Content-Type": "application/json", ...(moves === null ? {} : ifMatch(moves)) },
    body: JSON.stringify(body ?? {}),
  });
  return response.ok ? response.json() : refusal(response);
}

export const post = (path, body, options) => send("POST", path, body, options);
export const patch = (path, body, options) => send("PATCH", path, body, options);
export const remove = (path, body, options) => send("DELETE", path, body, options);

export const query = (pairs) => {
  const asked = new URLSearchParams(Object.entries(pairs).filter(([, v]) => v));
  return asked.toString() ? `?${asked}` : "";
};

/** A collection's rows (`{"items": […]}`); none where it was refused. */
export const items = (answer) => answer.items ?? [];

/** A name inside a path — a provider's, a model's, a setting's. */
export const segment = (name) => encodeURIComponent(name);

// ---------- the server's own paths, not the interface ----------

/** The sign-in: whether this otaku asks for a password, and whether
    this browser is already through it — the page cannot read its own
    cookie to say (`get`); the password, and whether to stay signed in
    for 30 days rather than 4 hours (`create`) — a wrong one answers 401
    with its sentence, which comes back as an answer, not an error; and
    the way out (`delete`). */
export const login = {
  get: () => get("/api/login"),
  create: (fields) => post("/api/login", fields),
  delete: () => remove("/api/login"),
};

// ---------- the reply stream ----------

/** A write whose answer is the reply as it arrives — a line played, a
    fresh take — from the head `story` stood at when this page last heard
    of it. Answers either the refusal (`refusal`), checked before anything
    is recorded, or the event stream. `signal` is how a reader gives up:
    aborting takes the socket away, which is the backend's
    cancel-and-keep door. */
export async function stream(path, body, { story, signal }) {
  const response = await ask(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...ifMatch(story) },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  if (!response.ok) return refusal(response);
  return { events: events(response, story) };
}

async function* events(response, story) {
  /* An SSE stream read off a POST, which EventSource cannot do. Frames
     are separated by a blank line and can split across chunks, so what
     is left over stays in the buffer until the rest of it arrives.

     However this loop is left — the last frame, a throw while drawing,
     a caller that stops asking — the reader is cancelled, and that is
     what takes the socket away. Without it the backend waits on a reply
     nobody is reading. */
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // the stream ran to its end, and a row it landed says where the head is
  let finished = false;
  let landed = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        finished = true;
        return;
      }
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop();
      for (const frame of frames) {
        const data = frame.split("\n").find((row) => row.startsWith("data: "));
        if (!data) continue;
        const happened = JSON.parse(data.slice(6));
        // a row landed is where the story's head now stands
        if (happened.message) {
          heads.set(story, `"${happened.message.id}"`);
          landed = true;
        }
        yield happened;
      }
    }
  } finally {
    // given up on, or a fresh take that landed nothing: `unsettled`
    if (finished && landed) unsettled.delete(story);
    else unsettled.add(story);
    await reader.cancel().catch(() => {});
  }
}

/* The demo's whole server, in the page: `window.fetch` is patched for
   `/api/*` before app.js runs (module scripts execute in document order,
   and the patch is installed synchronously at the top of this one), and
   `EventSource` is replaced for the watch stream. Everything else — the
   page, its scripts, the fixtures — passes through to the static host
   untouched.

   The router mirrors `otaku/web/api` route for route — the same method
   and path template, `ROUTES` and `STREAMS` — and the answers mirror the
   product's: a collection as `{items}`, a write as `{value, notice}`, an
   entity made 201 and work started 202, each with its `Location`; an
   answer inside a story tagged with the head it stands at (`ETag`), and a
   write that moves the head held to the one it names (`If-Match`: 428
   without one, 412 where the head moved); a refusal a problem body under
   the status its kind takes, its sentence the `detail`.
   `tests/test_architecture.py` holds the spec's path list against this
   file, so an endpoint the demo forgot fails the suite. Nothing a visitor
   does leaves the tab. */

import * as script from "./script.js";
import * as store from "./store.js";

const realFetch = window.fetch.bind(window);

// ---------- fixtures ----------

const ready = (async () => {
  const load = async (name) =>
    (await realFetch(new URL(`./fixtures/${name}.json`, import.meta.url))).json();
  const [app, river, tour] = await Promise.all([load("app"), load("river"), load("tour")]);
  store.seed({ app, river, tour });
})();

// ---------- the routes ----------

/* One row per row of `web.api.ROUTES`, keyed the same way: the METHOD and
   a path template, `{name}` standing for one segment. A handler is handed
   the path's parameters, the query, the body and the request's
   `If-Match`, and answers what `_request` makes of an answer: a payload
   (200), `items`, `changed`, `created` or `accepted`. */
const ROUTES = {
  // the app
  "GET /api/app": () => store.app(),
  "PATCH /api/app": (p, q, b) => changed(store.updateApp(appFields(b))),
  "GET /api/app/machine": () => store.machine(),
  "GET /api/settings": () => store.settings(),
  "PATCH /api/settings": (p, q, b) => changed(store.updateSettings(settingsFields(b))),
  "GET /api/language": () => store.language(),
  "GET /api/history": () => items(store.history()),
  "POST /api/history": (p, q, b) => changed(store.addHistory(need(b, "text"))),
  "GET /api/shared-reminder": () => store.sharedReminder(),
  "PATCH /api/shared-reminder": (p, q, b) => changed(store.updateSharedReminder(need(b, "text"))),
  "GET /api/tools/{tool}/prompt": (p) => store.toolPrompt(p.tool),
  "PATCH /api/tools/{tool}/prompt": (p, q, b) => changed(store.updateToolPrompt(p.tool, need(b, "text"))),
  "DELETE /api/tools/{tool}/prompt": (p) => changed(store.updateToolPrompt(p.tool, "")),
  "GET /api/prompts/{prompt}": (p) => store.prompt(p.prompt),
  "PATCH /api/prompts/{prompt}": (p, q, b) => changed(store.updatePrompt(p.prompt, need(b, "text"))),
  "DELETE /api/prompts/{prompt}": (p) => changed(store.updatePrompt(p.prompt, "")),
  "GET /api/integrations/web-search": () => store.webSearch(),
  "PATCH /api/integrations/web-search": (p, q, b) => changed(store.updateWebSearch(need(b, "api_key"))),
  "POST /api/integrations/support-requests": (p, q, b) => changed(store.supportRequest(need(b, "body"))),
  // providers and models
  "GET /api/providers": (p, q) => store.providers(q.get("scope") ?? ""),
  "GET /api/providers/{provider}": (p) => store.provider(p.provider),
  "PATCH /api/providers/{provider}": (p) => changed(store.updateProvider(p.provider)),
  "GET /api/providers/{provider}/models": (p) => items(store.models(p.provider)),
  "PATCH /api/providers/{provider}/models/{model}": (p, q, b) =>
    changed(store.updateModel(p.provider, p.model, flag(b, "loaded"))),
  "GET /api/providers/{provider}/models/{model}/settings": (p) => store.modelSettings(p.provider, p.model),
  "PATCH /api/providers/{provider}/models/{model}/settings": (p, q, b) =>
    changed(store.updateModelSettings(p.provider, p.model, modelFields(b))),
  // reports
  "GET /api/usage": (p, q) => store.usage(queryId(q, "story")),
  "GET /api/info": (p, q) => store.info(queryId(q, "story")),
  "GET /api/balance": () => store.balance(),
  // the stories
  "GET /api/stories": (p, q) => items(store.stories(q.get("query") ?? "")),
  "POST /api/stories": (p, q, b) => {
    const made = store.createStory({ title: text(b, "title"), premise: text(b, "premise") });
    return created(made, `/api/stories/${made.value.id}`);
  },
  "GET /api/stories/{story}": (p) => store.story(num(p.story)),
  "PATCH /api/stories/{story}": (p, q, b, ask) => {
    const story = num(p.story);
    const fields = {};
    for (const name of ["title", "premise"]) if (name in b) fields[name] = need(b, name);
    if ("head" in b) fields.head = integer(b, "head");
    const expected = "head" in fields ? expectedHead(story, ask) : undefined;
    return changed(store.updateStory(story, fields, expected));
  },
  "DELETE /api/stories/{story}": (p) => changed(store.deleteStory(num(p.story))),
  "POST /api/stories/{story}/forks": (p, q, b) => {
    const at = b.at == null ? null : integer(b, "at");
    const forked = store.fork(num(p.story), { at, title: text(b, "title") });
    return created(forked, `/api/stories/${forked.value.id}`);
  },
  "POST /api/stories/{story}/undos": (p, q, b, ask) => {
    const story = num(p.story);
    return changed(store.undo(story, expectedHead(story, ask)));
  },
  "GET /api/stories/{story}/export": (p) => store.exportDocument(num(p.story)),
  "POST /api/imports": (p, q, b) => {
    const landed = store.importDocument(need(b, "text"), need(b, "file_name"));
    return created(landed, `/api/stories/${landed.value.story_id}`);
  },
  // inside a story
  "GET /api/stories/{story}/messages": (p, q) =>
    items(store.messages(num(p.story), q.get("last") == null ? null : Number(q.get("last")))),
  "GET /api/stories/{story}/messages/{message}": (p) => store.message(num(p.story), num(p.message)),
  "PATCH /api/stories/{story}/messages/{message}": (p, q, b) =>
    changed(store.updateMessage(num(p.story), num(p.message), need(b, "body"))),
  "GET /api/stories/{story}/settings": (p) => items(store.storySettings(num(p.story))),
  "GET /api/stories/{story}/settings/{setting}": (p) => store.storySetting(num(p.story), p.setting),
  "PATCH /api/stories/{story}/settings/{setting}": (p, q, b) =>
    changed(store.updateStorySetting(num(p.story), p.setting, settingFields(b))),
  "GET /api/stories/{story}/context": (p) => store.context(num(p.story)),
  "GET /api/stories/{story}/context/fill": (p) => store.fill(num(p.story)),
  "POST /api/stories/{story}/cards": (p, q, b, ask) => card(num(p.story), q, b, ask),
  // lore
  "GET /api/stories/{story}/lore": (p) => store.lore(num(p.story)),
  "GET /api/stories/{story}/scenes": (p) => items(store.scenes(num(p.story))),
  "GET /api/stories/{story}/scenes/{scene}": (p) => store.scene(num(p.story), num(p.scene)),
  "PATCH /api/stories/{story}/scenes/{scene}": (p, q, b) =>
    changed(store.updateScene(num(p.story), num(p.scene), given(b, ["title", "summary"]))),
  "GET /api/stories/{story}/characters": (p, q) => items(store.characters(num(p.story), q.get("name"))),
  "GET /api/stories/{story}/characters/{character}": (p) => store.character(num(p.story), num(p.character)),
  "PATCH /api/stories/{story}/characters/{character}": (p, q, b) =>
    changed(store.updateCharacter(num(p.story), num(p.character), given(b, ["description", "card"]))),
  "DELETE /api/stories/{story}/characters/{character}": (p) => changed(store.deleteCharacter(num(p.story))),
  "POST /api/stories/{story}/characters/{character}/merges": (p) =>
    changed(store.mergeCharacter(num(p.story))),
  "GET /api/stories/{story}/journals": (p) => items(store.journals(num(p.story))),
  "GET /api/stories/{story}/journals/{journal}": (p) => store.journal(num(p.story), num(p.journal)),
  "PATCH /api/stories/{story}/journals/{journal}": (p, q, b) =>
    changed(store.updateJournal(num(p.story), num(p.journal), given(b, ["entry"]))),
  "POST /api/stories/{story}/extraction": (p) => {
    const story = num(p.story);
    return accepted(store.startExtraction(story), `/api/stories/${story}/extraction`);
  },
  "GET /api/stories/{story}/extraction": (p) => store.extraction(num(p.story)),
  "DELETE /api/stories/{story}/extraction": (p) => changed(store.stopExtraction(num(p.story))),
  // a message's pictures: the sample's own, shipped with the fixtures and
  // served as the product serves a stored file — a name nothing is stored
  // under answers 404, as it does there
  "GET /api/pictures/{name}": (p) => picture(p.name, false),
  "GET /api/pictures/{name}/thumbnail": (p) => picture(p.name, true),
};

// The two writes answered with the reply as it arrives (`web.api.STREAMS`).
const STREAMS = {
  "POST /api/stories/{story}/messages": (p, q, b, ask, signal) => play(num(p.story), b, ask, false, signal),
  "POST /api/stories/{story}/regens": (p, q, b, ask, signal) => play(num(p.story), b, ask, true, signal),
};

// The path parameters that are row ids, as `web.server._NUMERIC` declares
// them. Every other parameter is a name and may be anything.
const _NUMERIC = ["story", "message", "scene", "character", "journal"];

/* The templates, compiled once — longest first, as the server sorts them,
   so a literal segment is never eaten by a parameter. A row id matches
   DIGITS, as the product's router matches it: a page that lost its story
   addresses `/api/stories/null/…`, and the demo 404s it exactly where
   otaku does. */
const compile = (table, stream) =>
  Object.entries(table).map(([key, call]) => {
    const [method, template] = key.split(" ");
    const pattern = new RegExp(
      `^${template.replace(/\{(\w+)}/g, (_, name) =>
        `(?<${name}>${_NUMERIC.includes(name) ? "\\d+" : "[^/]+"})`,
      )}$`,
    );
    return { method, pattern, call, stream };
  });
const MATCHERS = [...compile(ROUTES, false), ...compile(STREAMS, true)].sort(
  (a, b) => b.pattern.source.length - a.pattern.source.length,
);

// ---------- what a route answers ----------

const items = (list) => ({ items: list });
const changed = (made) => made;
const created = (made, location) => ({ _status: 201, _location: location, body: made });
const accepted = (made, location) => ({ _status: 202, _location: location, body: made });

/* A malformed request: the server's 400, its `detail` a developer's
   note, as `Wire._fault` says it — never a sentence for the reader. */
class Fault extends Error {
  constructor(status, why = "") {
    super(why);
    this.status = status;
    this.why = why;
  }
}

function need(body, name) {
  const value = body[name];
  if (typeof value !== "string") throw new Fault(400, `KeyError: '${name}'`);
  return value;
}

const text = (body, name) => (typeof body[name] === "string" ? body[name] : "");

function flag(body, name) {
  if (typeof body[name] !== "boolean") throw new Fault(400, `TypeError: ${name} is not a boolean`);
  return body[name];
}

function integer(body, name) {
  const value = body[name];
  if (!Number.isInteger(value) || typeof value === "boolean") {
    throw new Fault(400, `TypeError: ${name} is not an integer`);
  }
  return value;
}

function given(body, names) {
  const fields = {};
  for (const name of names) if (name in body) fields[name] = need(body, name);
  return fields;
}

function appFields(body) {
  const ref = (value) => {
    if (!value || typeof value.provider !== "string" || typeof value.name !== "string") {
      throw new Fault(400, "TypeError: a model is not an object of provider and name");
    }
    return { provider: value.provider, name: value.name };
  };
  const fields = {};
  if ("model" in body) fields.model = ref(body.model);
  if ("extraction_model" in body) fields.extraction_model = body.extraction_model === null ? null : ref(body.extraction_model);
  return fields;
}

function settingsFields(body) {
  const fields = {};
  if ("last_story" in body) fields.last_story = body.last_story === null ? null : integer(body, "last_story");
  for (const name of ["verbose", "autocorrect", "notification"]) if (name in body) fields[name] = flag(body, name);
  if ("context_limit" in body) fields.context_limit = integer(body, "context_limit");
  return fields;
}

function settingFields(body) {
  const fields = {};
  for (const name of ["enabled", "display_notes"]) if (name in body) fields[name] = flag(body, name);
  if ("reminder_text" in body) fields.reminder_text = need(body, "reminder_text");
  if ("position" in body) {
    const value = body.position;
    if (value !== "system" && !(Number.isInteger(value) && value >= 1)) {
      throw new Fault(400, 'TypeError: position is not "system" or a count from 1');
    }
    fields.position = value;
  }
  if ("layout" in body) {
    if (!["novel", "canvas", "manuscript", "chat"].includes(body.layout)) {
      throw new Fault(400, "TypeError: layout is not one of novel, canvas, manuscript, chat");
    }
    fields.layout = body.layout;
  }
  return fields;
}

function modelFields(body) {
  const fields = {};
  if ("think" in body) {
    const value = body.think;
    if (typeof value === "boolean" || !(typeof value === "string" || Number.isInteger(value))) {
      throw new Fault(400, "TypeError: think is not a level or a number of tokens");
    }
    fields.think = String(value);
  }
  if (body.parameters != null) {
    if (typeof body.parameters !== "object" || Array.isArray(body.parameters)) {
      throw new Fault(400, "TypeError: parameters is not an object");
    }
    fields.parameters = body.parameters;
  }
  return fields;
}

function queryId(query, name) {
  const value = query.get(name);
  return value == null ? null : Number(value);
}

/* The head a write moves from, as `web.api.story.stories.expected_head`
   reads `If-Match`: none is the client's fault (428); `*` the head as it
   stands; one tag naming a head, that head; tags naming none of them a
   precondition that failed (412, saying where the head stands). */
function expectedHead(story, ask) {
  if (ask.ifMatch == null) {
    throw new Fault(428, "PreconditionRequired: this write moves the story's head: If-Match must name the ETag it was read at");
  }
  const head = store.headOf(story);
  const stated = ask.ifMatch.split(",").map((tag) => tag.trim());
  if (stated.length === 1 && stated[0] === "*") return head;
  const found = stated.length === 1 ? /^"(\d+)"$/.exec(stated[0]) : null;
  if (found) return Number(found[1]) || null;
  if (stated.includes(etag(head))) return head;
  throw new store.Refused(412, "The story moved on since you last saw it — nothing was changed.", { head });
}

const etag = (head) => `"${head ?? 0}"`;

// ---------- character cards ----------

/* The real import, one layer down: the parse mirrors
   `otaku/backend/domain/_formats/cards.py` — the PNG text chunks and JSON,
   ccv3 preferred, the same allowlist, the same refusal sentences — and the
   flow mirrors `web.api.story.cards`: the file vetted with
   `?validate_only=true` and nothing stored, then imported by the same
   request with the persona and `If-Match`, 201 with the character's
   `Location`. The token estimate is the demo's own arithmetic, as its
   numbers always are. */

const _PNG_MAGIC = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const _LARGE_CARD_TOKENS = 4000; // the same warning threshold as `backend.api.cards`
const _CHAR_MACRO = /\{\{\s*char\s*\}\}|<BOT>/gi;
const _USER_MACRO = /\{\{\s*user\s*\}\}|<USER>/gi;
const _ANY_MACRO = /\{\{[^{}]{1,40}\}\}/g;

class _NotACard extends Error {}

const _latin = (bytes) => Array.from(bytes, (b) => String.fromCharCode(b)).join("");
const _b64bytes = (text) =>
  Uint8Array.from(atob(text.replace(/[^A-Za-z0-9+/=]/g, "")), (ch) => ch.charCodeAt(0));
const _newlines = (text) => text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
const _bind = (text, char, user) =>
  text.replace(_CHAR_MACRO, () => char).replace(_USER_MACRO, () => user);

async function _inflate(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function _pngCandidates(bytes) {
  const out = [];
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let pos = _PNG_MAGIC.length;
  while (pos + 8 <= bytes.length) {
    const length = view.getUint32(pos);
    const ctype = _latin(bytes.subarray(pos + 4, pos + 8));
    const chunk = bytes.subarray(pos + 8, pos + 8 + length);
    pos += 12 + length; // length + type + payload + crc, never validated
    if (ctype === "IEND") break;
    if (!["tEXt", "zTXt", "iTXt"].includes(ctype)) continue;
    const zero = chunk.indexOf(0);
    if (zero < 0) continue;
    const keyword = _latin(chunk.subarray(0, zero)).toLowerCase();
    if (keyword !== "chara" && keyword !== "ccv3") continue;
    let rest = chunk.subarray(zero + 1);
    try {
      if (ctype === "zTXt") {
        rest = await _inflate(rest.subarray(1)); // one compression-method byte first
      } else if (ctype === "iTXt") {
        const compressed = rest[0] === 1;
        rest = rest.subarray(2); // compression flag + method
        rest = rest.subarray(rest.indexOf(0) + 1); // language tag
        rest = rest.subarray(rest.indexOf(0) + 1); // translated keyword
        if (compressed) rest = await _inflate(rest);
      }
      const payload = JSON.parse(new TextDecoder().decode(_b64bytes(_latin(rest))));
      if (payload && typeof payload === "object" && !Array.isArray(payload)) {
        out.push([keyword, payload]);
      }
    } catch {
      // a chunk that will not read is not the card
    }
  }
  return out;
}

function _cardDict(payload) {
  const data = payload.data;
  return data && typeof data === "object" && !Array.isArray(data) ? data : payload;
}

function _normalizeCard(payload) {
  const card = _cardDict(payload);
  const text = (key) => (typeof card[key] === "string" ? _newlines(card[key]).trim() : "");
  const name = text("name");
  if (!name) return null;
  const extensions = card.extensions;
  const depth =
    extensions && typeof extensions === "object" && extensions.depth_prompt &&
    typeof extensions.depth_prompt === "object"
      ? _newlines(String(extensions.depth_prompt.prompt ?? "")).trim()
      : "";
  return {
    name,
    description: text("description"),
    personality: text("personality"),
    scenario: text("scenario"),
    greeting: text("first_mes"),
    alternate_greetings: (Array.isArray(card.alternate_greetings) ? card.alternate_greetings : [])
      .map((item) => _newlines(String(item)).trim())
      .filter(Boolean),
    examples: text("mes_example").split(/<START>/i).map((block) => block.trim()).filter(Boolean),
    depth_note: depth,
    system_prompt: text("system_prompt"),
    post_history_instructions: text("post_history_instructions"),
    creator_notes: text("creator_notes"),
  };
}

function _unknownMacros(card) {
  const text = [
    card.description, card.personality, card.scenario, card.greeting, card.depth_note,
    ...card.alternate_greetings, ...card.examples,
  ].join("\n");
  const seen = new Map();
  for (const macro of text.match(_ANY_MACRO) ?? []) {
    if (!/^\{\{\s*char\s*\}\}$/i.test(macro) && !/^\{\{\s*user\s*\}\}$/i.test(macro)) {
      seen.set(macro, null);
    }
  }
  return [...seen.keys()];
}

async function _loadCard(bytes, fileName) {
  let candidates;
  if (_PNG_MAGIC.every((value, i) => bytes[i] === value)) {
    candidates = await _pngCandidates(bytes);
    if (!candidates.length) throw new _NotACard("no character card embedded in this PNG");
  } else {
    let parsed;
    try {
      let text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
      parsed = JSON.parse(text);
    } catch (e) {
      throw new _NotACard(`not a PNG and not readable as JSON (${e.message})`);
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new _NotACard("the JSON holds no card object");
    }
    candidates = [["json", parsed]];
  }
  const normalized = candidates.map(([keyword, payload]) => [keyword, payload, _normalizeCard(payload)]);
  const valid = normalized.filter(([, , card]) => card !== null);
  if (!valid.length) throw new _NotACard("the card carries no character name");
  const chosen = valid.find(([keyword]) => keyword === "ccv3") ?? valid[0];
  const [keyword, payload, card] = chosen;
  const notes = [];
  const same = JSON.stringify(card);
  if (valid.some(([, , other]) => JSON.stringify(other) !== same)) {
    notes.push(`the file embeds ${valid.length} cards that disagree; imported the ${keyword} one`);
  }
  const book = _cardDict(payload).character_book;
  const entries = book && typeof book === "object" ? book.entries : null;
  if (Array.isArray(entries) && entries.length) {
    notes.push(`dropped the card's lorebook (${entries.length} entries) — not supported`);
  }
  const unbound = _unknownMacros(card);
  if (unbound.length) notes.push("left as written (nothing binds them): " + unbound.join(", "));
  card.file_name = fileName;
  return { card, notes };
}

function _cardToml(card, user) {
  // The archive's keys in `context.cards.card_toml`'s order; the demo
  // writes plain TOML — the same fields, its own formatting.
  const pair = (key, value) =>
    Array.isArray(value)
      ? `${key} = [${value.map((item) => JSON.stringify(item)).join(", ")}]`
      : `${key} = ${JSON.stringify(value)}`;
  const lines = [pair("name", card.name), pair("user", user)];
  const keys = [
    "description", "personality", "scenario", "greeting", "alternate_greetings",
    "examples", "depth_note", "system_prompt", "post_history_instructions",
    "creator_notes", "file_name",
  ];
  for (const key of keys) {
    const value = card[key];
    if (value == null || value.length === 0) continue;
    lines.push(pair(key, value));
  }
  return lines.join("\n\n");
}


async function card(story, query, body, ask) {
  const fileName = need(body, "file_name");
  const rename = text(body, "rename");
  let loaded;
  try {
    loaded = await _loadCard(_b64bytes(need(body, "data")), fileName);
  } catch (e) {
    if (!(e instanceof _NotACard)) throw e;
    throw new store.Refused(422, `Not a character card: ${e.message}`);
  }
  const found = loaded.card;
  if (rename) found.name = rename;
  const persona = store.personaOf(story);
  const tokens = Math.ceil(_cardToml(found, persona || "you").length / 4);
  if (query.get("validate_only") === "true") {
    if (store.characters(story, found.name).length) {
      const existing = store.characters(story, found.name)[0];
      const source = existing.card ? "imported earlier" : "extracted from play";
      throw new store.Refused(
        409,
        `${found.name} already exists in this story (${source}) — import under another name: /card FILE NAME.`,
      );
    }
    return { name: found.name, notes: loaded.notes, block_tokens: tokens, large: tokens > _LARGE_CARD_TOKENS, persona };
  }
  const speaking = need(body, "persona");
  const report = [`imported ${found.name} (card ≈ ${tokens} tokens)`, ...loaded.notes];
  if (tokens > _LARGE_CARD_TOKENS) report.push("a large card — it rides every request and never leaves the context");
  const imported = store.importCard(story, expectedHead(story, ask), {
    name: found.name,
    description: found.description,
    archive: _cardToml(found, speaking),
    greeting: _bind(found.greeting, found.name, speaking),
    fileName: found.file_name,
    line: `/card ${fileName}` + (rename ? ` ${rename}` : ""),
    persona: speaking,
    report: report.join(" · "),
  });
  return created(imported, `/api/stories/${story}/characters/${imported.value.character_id}`);
}

// ---------- the reply stream ----------

/* A line played, or a fresh take on the last reply: refused EAGERLY, as
   the product refuses, before anything is recorded — a story that is not
   there, a head that moved, a picture the model cannot see, an empty line
   outside the canvas, nothing to regenerate — then the events in the
   product's order: the line as recorded (`user_message`), the reply's
   pieces (`assistant_text`, the first saying how it joins), the reply as
   recorded (`assistant_message`), and `done`. */
function play(story, body, ask, regenerate, signal) {
  store.story(story);
  const expected = expectedHead(story, ask);
  store.checkHead(story, expected);
  const line = regenerate ? "" : text(body, "line");
  const facts = store.story(story);
  if (!regenerate) {
    if (Array.isArray(body.files) && body.files.length) {
      throw new store.Refused(409, "This model cannot see pictures.");
    }
    if (!line.trim() && facts.mode !== "story") throw new store.Refused(422, "Nothing to send: type a line.");
  }
  const events = [];
  if (regenerate) store.dropReply(story);
  // an empty line in the canvas is the continue: no row of the reader's
  else if (line.trim()) events.push({ type: "user_message", message: store.record(story, "user", line), note: "" });
  const reply = script.reply(regenerate);
  const promptTokens = store.contextTokens(story);
  const started = Date.now();
  const join = store.replyJoin(story);
  let streamed = "";
  let timer = null;

  const land = () => {
    // Whatever ended the stream — the last piece or a closed reader — what
    // arrived is recorded, exactly as the backend keeps a partial reply.
    if (!streamed) return null;
    const recorded = store.record(story, "assistant", streamed);
    store.recordUsage(story, promptTokens, Math.ceil(streamed.length / 4), (Date.now() - started) / 1000);
    streamed = "";
    return recorded;
  };

  const encoder = new TextEncoder();
  const frame = (controller, event) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
  let over = false;
  const stream = new ReadableStream({
    start(controller) {
      /* Stop is the reader taking the socket away, and the backend reads
         that as cancel-and-keep: what arrived is recorded, the rest never
         asked for. `fetch` carries it as an abort signal, which the fake
         honours itself — otherwise Stop waits out the whole reply. */
      const abandon = () => {
        if (over) return;
        over = true;
        clearTimeout(timer);
        land();
        controller.error(new DOMException("Aborted", "AbortError"));
      };
      if (signal) {
        if (signal.aborted) return abandon();
        signal.addEventListener("abort", abandon, { once: true });
      }
      for (const event of events) frame(controller, event);
      const words = reply.split(/(?<=\s)/);
      let at = 0;
      const tick = () => {
        if (at >= words.length) {
          const seconds = (Date.now() - started) / 1000;
          const tokens = Math.ceil(streamed.length / 4);
          const rate = seconds > 0 ? tokens / seconds : 0;
          const stats = store.verbose()
            ? `[ total ${seconds.toFixed(1)}s, prompt ${promptTokens} tok, eval ${tokens} tok @ ${rate.toFixed(1)} tok/s ]`
            : "";
          const recorded = land();
          if (recorded) frame(controller, { type: "assistant_message", message: recorded });
          frame(controller, {
            type: "done",
            stats,
            report: {
              prompt_tokens: promptTokens,
              completion_tokens: tokens,
              cached_tokens: null,
              first_token_seconds: Math.min(0.2, seconds),
              total_seconds: seconds,
              finish_reason: "stop",
              context_in_force: store.fill(story).context_in_force,
              truncated: false,
              rate,
              context_used: null,
            },
            notice: "",
          });
          over = true;
          controller.close();
          return;
        }
        const chunk = words.slice(at, at + 3).join("");
        at += 3;
        // the first piece says how the reply joins the story; null on every later one
        frame(controller, { type: "assistant_text", text: chunk, join_type: streamed ? null : join });
        streamed += chunk;
        timer = setTimeout(tick, 24);
      };
      timer = setTimeout(tick, regenerate ? 350 : 650); // the wait before the first token
    },
    cancel() {
      over = true;
      clearTimeout(timer);
      land();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
  });
}

// ---------- the pictures ----------

function picture(name, thumbnail) {
  if (!store.pictureNames().has(name)) throw new store.Refused(404, "No such picture.");
  const file = thumbnail ? `${name.replace(/\.[^.]+$/, "")}-thumb.jpg` : name;
  return realFetch(new URL(`./fixtures/files/${file}`, import.meta.url));
}

/* A message's picture is loaded by the browser itself, as an <img>, never
   through fetch — so the fake reaches it on the image element's `src`: a
   stored file's address, as the page spells it (`api.pictures.url`, its
   `thumbnail.url`), becomes the fixture's copy of it. */
const realSrc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src");
Object.defineProperty(HTMLImageElement.prototype, "src", {
  configurable: true,
  get() {
    return realSrc.get.call(this);
  },
  set(value) {
    const found = /^\/api\/pictures\/([^/]+?)(\/thumbnail)?$/.exec(String(value));
    if (found) {
      const name = decodeURIComponent(found[1]);
      const file = found[2] ? `${name.replace(/\.[^.]+$/, "")}-thumb.jpg` : name;
      value = new URL(`./fixtures/files/${file}`, import.meta.url).href;
    }
    realSrc.set.call(this, value);
  },
});

// ---------- the patch ----------

const PHRASES = {
  400: "Bad Request",
  404: "Not Found",
  405: "Method Not Allowed",
  409: "Conflict",
  412: "Precondition Failed",
  422: "Unprocessable Entity",
  428: "Precondition Required",
  502: "Bad Gateway",
};

window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  const [path, search = ""] = url.split("?");
  if (!path.startsWith("/api/")) return realFetch(input, init);
  await ready;
  const method = (init.method || "GET").toUpperCase();
  const query = new URLSearchParams(search);
  const header = (name) => new Headers(init.headers || {}).get(name);
  // The three the server holds itself, out of both lanes.
  if (path === "/api/app/status" && method === "GET") return json(store.status());
  if (path === "/api/login") {
    // The demo asks for no password: the product's answers for an otaku
    // with none set — nothing to sign in to, and every attempt refused.
    if (method === "GET") return json({ required: false, signed_in: false });
    if (method === "POST") return problem(409, "This otaku asks for no password.");
    return problem(405);
  }
  let body = {};
  if (method !== "GET" && init.body) {
    try {
      body = JSON.parse(init.body);
    } catch {
      return problem(400, "JSONDecodeError: the body is not JSON");
    }
    if (!body || typeof body !== "object" || Array.isArray(body)) return problem(400, "the body is not a JSON object");
  }
  const route = MATCHERS.find((each) => each.method === method && each.pattern.test(path));
  if (!route) {
    // a path some route answers under another method is a 405, any other a plain 404
    const allowed = MATCHERS.filter((each) => each.pattern.test(path)).map((each) => each.method);
    return allowed.length ? problem(405, undefined, {}, { Allow: [...new Set(allowed)].join(", ") }) : problem(404);
  }
  const params = route.pattern.exec(path).groups ?? {};
  const story = params.story == null ? null : Number(params.story);
  const ask = { ifMatch: header("If-Match") };
  try {
    const answer = await route.call(params, query, body, ask, init.signal);
    if (answer instanceof Response) return answer;
    const status = answer?._status ?? 200;
    const payload = answer?._status ? answer.body : answer;
    const headers = {};
    if (answer?._location) headers.Location = answer._location;
    // An answer inside a story says the head it stands at; a deleted
    // story's and a fork's — an answer about the copy — are aside.
    const aside = (method === "DELETE" && path === `/api/stories/${story}`) || path.endsWith("/forks");
    if (story !== null && !aside) {
      try {
        headers.ETag = etag(store.headOf(story));
      } catch {
        // the story is gone: nothing to tag
      }
    }
    return json(payload, status, headers);
  } catch (e) {
    if (e instanceof store.Refused) {
      const headers = e.status === 412 ? { ETag: etag(e.extra.head) } : {};
      return problem(e.status, e.detail, e.extra, headers);
    }
    if (e instanceof Fault) return problem(e.status, e.why);
    throw e;
  }
};

function json(payload, code = 200, headers = {}) {
  return new Response(JSON.stringify(payload), {
    status: code,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}

/* Whatever is not a payload is a problem body (RFC 9457), as `Wire._problem`
   writes it: the status, its phrase as `title`, the sentence as `detail`
   where there is one, and what a refusal adds beside them. */
function problem(code, detail = undefined, extra = {}, headers = {}) {
  const body = { type: "about:blank", title: PHRASES[code] ?? "Error", status: code };
  if (detail !== undefined) body.detail = detail;
  return new Response(JSON.stringify({ ...body, ...extra }), {
    status: code,
    headers: { "Content-Type": "application/problem+json; charset=utf-8", ...headers },
  });
}

function num(value) {
  return Number(value);
}

// The watch stream ("/api/watch") never has news in the demo: the deployed
// files change only with a deploy, and a deploy serves a new page anyway.
// The fake opens once and stays silently open, so the page's reload logic
// never fires; a stream to any other address is nothing the page makes.
window.EventSource = class {
  constructor(url) {
    console.assert(url === "/api/watch", "the page opened an unexpected stream:", url);
    this.listeners = {};
    setTimeout(() => (this.listeners.open || []).forEach((fn) => fn({})), 0);
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  close() {}
};

// ---------- the demo's own chrome ----------

// The one thing the demo adds to the page: the ribbon that says what this
// is and where the real one lives. Styled by the demo's own custom.css —
// the reader-stylesheet contract, dogfooded.
const ribbon = document.createElement("a");
ribbon.className = "demo-ribbon";
ribbon.href = "https://otaku.sh";
ribbon.target = "_blank";
ribbon.rel = "noopener";
ribbon.textContent = "demo";
document.addEventListener("DOMContentLoaded", () => {
  document.body.append(ribbon);
  // Story import is not offered here: the button goes quiet instead of
  // answering with a sentence — the real formats need the real parsers.
  const door = document.querySelector('button[data-command="/import"]');
  if (door) door.disabled = true;
});

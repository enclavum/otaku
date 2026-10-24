/* The demo's backend: what a real otaku keeps below both frontends, held
   in this tab's memory instead. Seeded from the captured fixtures — the
   real server's own answers over the shipped sample stories
   (`demo/capture_fixtures_web.py`) — changed by the same operations the
   page performs, and forgotten on reload, which makes reload the demo's
   reset button.

   Every read answers a representation in the product's shape, and every
   write `{value, notice}` as the product's `Changed` does; a refusal is
   thrown (`Refused`: the status its kind takes and the sentence), which
   `demo.js` answers as the product's problem body. The sentences are the
   backend's, copied — the demo has no backend to ask — and what the demo
   cannot do at all says so and points at the install. */

import * as script from "./script.js";

// What the demo's provider claims to be: a section of its own, with two
// models of its own, so the picker's switch, pin, load and unload have
// something real to do. Its models think nothing, see nothing, and
// complete no raw text.
export const PROVIDER = "demo";
const MODEL_CAPABILITIES = {
  vision: false,
  audio: false,
  supported_params: null,
  reasoning_efforts: [],
  reasoning_switch: false,
  reasoning_budget: false,
  text_completion: false,
  structured_output: false,
};
const MODELS = [
  { name: "demo-model", size: 4_700_000_000, window: 32768 },
  { name: "demo-model-mini", size: 1_900_000_000, window: 8192 },
];
// The window the fixtures' context previews were captured under — the
// demo's own model claims the same, or its numbers argue with them.
const WINDOW = 32768;
// The reply's reserve the assembler keeps out of the window.
const RESERVE = 194;
const LABEL_WIDTH = 50;

export const INSTALL = "install otaku from otaku.sh to do this for real";

// ---------- the refusals ----------

/** What the backend declines, under the status its kind says: 404 a
    subject that is not there, 409 a state that rules it out, 412 a head
    that moved, 422 input it cannot take, 502 a service that failed it. */
export class Refused extends Error {
  constructor(status, detail, extra = {}) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.extra = extra;
  }
}

const notFound = (detail) => new Refused(404, detail);
const conflict = (detail) => new Refused(409, detail);
const invalid = (detail) => new Refused(422, detail);
const unavailable = (detail) => new Refused(502, detail);
// The sentences more than one operation refuses with (`backend.errors`).
const NO_SUCH_STORY = "No such story.";
const OFF_CHAIN = "That message is not on the story's current chain.";
const HEAD_MOVED = "The story moved on since you last saw it — nothing was changed.";
const NOT_IN_DEMO = (what) => `${what} is not in the demo — ${INSTALL}.`;

// ---------- the state ----------

const state = {
  stories: new Map(), // id → a held story (`hold`)
  nextStory: 1,
  nextMessage: 1,
  nextLore: 1, // scenes, characters and journals alike: a demo's ids need not be the store's
  defaults: [], // the settings a story made now starts with
  model: MODELS[0].name,
  pinned: null, // the model lore extraction is pinned to; null follows the one above
  loaded: new Set([MODELS[0].name]),
  launch_facts: { notices: [], hint: "", newer_version: "" },
  version: "",
  settings: null, // `Settings`, minus what the demo keeps elsewhere
  language: null,
  history: [], // the composer's ↑/↓ lines, most recent first
  sharedReminder: "",
  toolPrompts: {}, // tool → {name, text}, as it stands
  shippedToolPrompts: {},
  prompts: {}, // prompts.toml key → {name, text}, as it stands
  shippedPrompts: {},
  modelSettings: new Map(), // model → `ModelSettings`
  modelTemplate: null, // the captured `ModelSettings`, a fresh model's own
  listing: null, // the captured provider listing: its vocabulary
  providerCapabilities: null,
  usage: [], // {story, purpose, prompt, completion, seconds}
  status: "", // the worker's one line
  cards: new Map(), // id → a codex card, as `Card` minus the stories it is in; in the reader's order
  nextCard: 1,
  cardDigests: new Map(), // id → the digests of the card files it was made from
};

export function seed(fixtures) {
  const { app, river, tour, codex } = fixtures;
  state.version = app.app.version;
  state.launch_facts = { notices: [], hint: app.app.launch_facts.hint, newer_version: "" };
  state.settings = structuredClone(app.settings);
  state.language = app.language;
  state.defaults = app.story_defaults;
  state.toolPrompts = structuredClone(app.tool_prompts);
  state.shippedToolPrompts = structuredClone(app.tool_prompts);
  state.prompts = structuredClone(app.prompts);
  state.shippedPrompts = structuredClone(app.prompts);
  state.modelTemplate = app.model_settings;
  state.listing = app.providers;
  state.providerCapabilities = app.providers.providers.find((row) => row.capabilities)?.capabilities ?? null;
  // Both shipped samples, as a fresh install seeds them, under the ids the
  // capture read them at, and the one a launch resumes as it resumes it.
  const listed = new Map(app.stories.map((row) => [row.id, row]));
  for (const sample of [river, tour]) {
    const facts = sample.story;
    const held = hold(facts.id, {
      title: facts.title,
      premise: facts.premise,
      updatedAt: facts.updated_at,
      storySoFar: listed.get(facts.id)?.story_so_far ?? "",
      messages: structuredClone(sample.messages),
      settings: structuredClone(sample.settings),
      lore: structuredClone(sample.lore),
    });
    held.context = sample.context;
    held.fill = sample.fill;
    state.nextStory = Math.max(state.nextStory, facts.id + 1);
    for (const message of held.messages) state.nextMessage = Math.max(state.nextMessage, message.id + 1);
    for (const row of [...held.lore.scenes, ...held.lore.characters, ...held.lore.journals]) {
      state.nextLore = Math.max(state.nextLore, row.id + 1);
    }
  }
  // The samples' cards, as a fresh install keeps them, in its order (the
  // list's): the stories they are in are read off the casts, as the store
  // reads them.
  for (const { id } of codex.cards) {
    const row = structuredClone(codex.card[String(id)]);
    delete row.stories;
    state.cards.set(id, row);
    state.nextCard = Math.max(state.nextCard, id + 1);
  }
}

function hold(id, { title = "", premise = "", updatedAt = now(), storySoFar = "", messages = [], settings = null, lore = null, forkedFrom = null } = {}) {
  const held = {
    id,
    forkedFrom, // the story it was forked from: kept after that one goes, as `stories.forked_from_id`
    title,
    premise,
    updatedAt,
    storySoFar,
    messages,
    settings: settings ?? structuredClone(state.defaults),
    lore: lore ?? { story_id: id, message_count: 0, extracted_up_to: 0, scenes: [], characters: [], journals: [] },
    context: null, // the captured preview, served until the story moves
    fill: null,
    persona: "",
    extraction: null, // the forced pass: {story_id, status, report}
  };
  state.stories.set(id, held);
  return held;
}

function held(id) {
  const found = state.stories.get(id);
  if (!found) throw notFound(NO_SUCH_STORY);
  return found;
}

// ---------- a story, as a client draws it ----------

/** Where a story's head stands: its newest message, null with none. */
export function headOf(id) {
  return held(id).messages.at(-1)?.id ?? null;
}

/** The head a write moves from must be the one it names (`If-Match`):
    a head moved meanwhile is the product's 412, nothing written. */
export function checkHead(id, expected) {
  const head = headOf(id);
  if (head !== expected) throw new Refused(412, HEAD_MOVED, { head });
}

function storyOf(story) {
  const layout = layoutOf(story);
  const newest = story.messages.at(-1) ?? null;
  return {
    id: story.id,
    title: story.title,
    label: labelOf(story),
    premise: story.premise,
    head: newest?.id ?? null,
    message_count: story.messages.length,
    word_count: wordsOf(story),
    updated_at: story.updatedAt,
    layout,
    mode: modeOf(layout),
    next_join: newest === null ? null : joinOf(newest, { mode: modeOf(layout), body: "", kind: "dialogue" }),
    // the demo's models complete no raw text: every layout goes over the
    // chat wire, a novel played as the canvas (`Layout.completion_type`)
    completion_type: "chat",
    // a picture rides where the model sees, the chat wire having room for
    // one (`ModelFacts.can_attach`)
    can_attach: MODEL_CAPABILITIES.vision,
    question: questionOf(story),
    shown_tools: shownTools(story),
    folded_tools: foldedTools(story),
  };
}

function summaryOf(story) {
  return {
    id: story.id,
    title: story.title,
    label: labelOf(story),
    story_so_far: story.storySoFar,
    first_message: story.messages[0]?.body ?? "",
    model: "",
    updated_at: story.updatedAt,
    message_count: story.messages.length,
    word_count: wordsOf(story),
  };
}

// The story's words, counted as the store counts each message
// (`store.ops.stories._count_words`): a tool's result none, a Chinese or
// Japanese character one, then every run between spaces holding a letter
// or a digit.
const CHARACTER_WORDS = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f]/gu;
const wordsOf = (story) =>
  story.messages
    .filter((message) => message.role !== "tool")
    .reduce((sum, { body }) => {
      const characters = body.match(CHARACTER_WORDS)?.length ?? 0;
      const runs = body.replace(CHARACTER_WORDS, " ").split(/\s+/).filter((run) => /[\p{L}\p{N}]/u.test(run));
      return sum + characters + runs.length;
    }, 0);

const layoutOf = (story) => story.settings.find((row) => row.name === "layout")?.layout ?? "manuscript";
// `Layout.mode`: the canvas and the novel are read as one text
const modeOf = (layout) => (layout === "canvas" || layout === "novel" ? "story" : "chat");
const settingOf = (story, name) => story.settings.find((row) => row.name === name);

function labelOf(story) {
  const first = story.messages.find((row) => row.role === "user")?.body ?? "";
  return flatten(story.title || story.storySoFar || first);
}

/* The join rule (`assembler.get_join_type`), copied: INLINE where both rows
   were written in story mode, neither stands alone — a card's line or
   greeting — and no line break stands between them; PARAGRAPH otherwise. */
function joinOf(previous, next) {
  if (previous === null) return "paragraph";
  const alone = (row) => row.kind === "card" || row.provider === "card";
  const inline =
    previous.mode === "story" &&
    next.mode === "story" &&
    !alone(previous) &&
    !alone(next) &&
    !/\n\s*$/.test(previous.body) &&
    !/^\s*\n/.test(next.body);
  return inline ? "inline" : "paragraph";
}

function shownTools(story) {
  const shown = [];
  if (settingOf(story, "allow_questions")?.enabled) shown.push("question");
  const notes = settingOf(story, "allow_assistant_notes");
  if (notes?.enabled && notes.display_notes) shown.push("note");
  return shown;
}

function foldedTools(story) {
  const notes = settingOf(story, "allow_assistant_notes");
  return notes?.enabled && !notes.display_notes ? ["note"] : [];
}

/* The question the story stands on (`messages.posed_question`), copied:
   the newest reply, the questions tool on, whose last call is a question
   with nothing after it but notes and whitespace. */
function questionOf(story) {
  const newest = story.messages.at(-1);
  if (newest?.role !== "assistant" || !shownTools(story).includes("question")) return null;
  for (const segment of [...newest.segments].reverse()) {
    if (segment.kind === "prose") {
      if (segment.text.trim()) return null;
    } else if (segment.tool === "question") {
      return { text: segment.text, question: segment.question, options: segment.options };
    } else if (segment.tool !== "note") {
      return null;
    }
  }
  return null;
}

// ---------- the app ----------

export function app() {
  const model = modelRow(state.model);
  const extracting = state.pinned ?? state.model;
  return {
    version: state.version,
    launch_facts: structuredClone(state.launch_facts),
    model: {
      provider: PROVIDER,
      name: model.name,
      listed_name: model.name,
      context_window: model.window,
      capabilities: { ...MODEL_CAPABILITIES },
      can_complete_text: false,
      can_attach: MODEL_CAPABILITIES.vision,
    },
    extraction_model: {
      provider: PROVIDER,
      name: extracting,
      listed_name: extracting,
      pinned: state.pinned !== null,
    },
    on_cloud: false,
  };
}

/* The model in use and the one lore extraction is pinned to — the
   product's sentences (`app.providers.services`), its refusals. */
export function updateApp({ model, extraction_model: pin } = {}) {
  const said = [];
  if (model !== undefined) {
    const wanted = knownModel(model);
    if (wanted === state.model) said.push(`Already using ${PROVIDER}/${wanted}.`);
    else {
      state.model = wanted;
      said.push(`Switched to ${PROVIDER}/${wanted}.`);
    }
  }
  if (pin !== undefined) {
    if (pin === null) {
      if (state.pinned === null) said.push("Lore extraction already uses the model you play with.");
      else {
        state.pinned = null;
        said.push("Lore extraction uses the model you play with.");
      }
    } else {
      const wanted = knownModel(pin);
      if (state.pinned === wanted) said.push(`Lore extraction already uses ${PROVIDER}/${wanted}.`);
      else {
        state.pinned = wanted;
        said.push(`Lore extraction uses ${PROVIDER}/${wanted}.`);
      }
    }
  }
  if (!said.length) throw invalid("Nothing to change.");
  return { value: app(), notice: said.join(" ") };
}

function knownModel({ provider, name }) {
  if (provider !== PROVIDER) {
    if (!state.listing.providers.some((row) => row.id === provider)) {
      throw notFound(`No supported provider is named ${provider}.`);
    }
    // every real engine is out of the tab's reach
    throw unavailable(`Could not reach ${provider}.`);
  }
  return name;
}

export function status() {
  return { status: state.status, notices: [] };
}

export function setStatus(line) {
  state.status = line;
}

/* The machine's gauge, which the picker reads again every second. There
   is no machine here, so it drifts a little rather than standing still —
   a figure that never moves would say the poll was not running. */
export function machine() {
  const used = 23.4 + (Math.round(Date.now() / 1000) % 7) / 10;
  return { memory: `RAM: ${used.toFixed(1)} / 64.0 GB (${Math.round((100 * used) / 64)}%)` };
}

// ---------- settings ----------

export function settings() {
  return structuredClone(state.settings);
}

/* The fields given, each with its sentence (`app.settings.services`). */
export function updateSettings(fields) {
  const said = [];
  for (const name of ["verbose", "autocorrect", "notification"]) {
    if (fields[name] === undefined) continue;
    state.settings[name] = fields[name];
    said.push(`${name[0].toUpperCase()}${name.slice(1)}: ${fields[name] ? "on" : "off"}.`);
  }
  if (fields.context_limit !== undefined) {
    const tokens = fields.context_limit;
    if (tokens < 0) throw invalid("Max context cannot be negative — 0 means the model's own max context.");
    state.settings.context_limit = tokens;
    state.settings.context_limit_text = limitText(tokens);
    said.push(`Max context: ${limitText(tokens)}.`);
  }
  if (fields.last_story !== undefined) {
    const story = fields.last_story;
    state.settings.last_story = story !== null && state.stories.has(story) ? story : null;
    said.push(story === null ? "A launch resumes no story." : `A launch resumes story ${story}.`);
  }
  if (!said.length) throw invalid("Nothing to change.");
  return { value: settings(), notice: said.join(" ") };
}

const limitText = (tokens) =>
  tokens === 0 ? "0 (the model's own max context)" : `${tokens.toLocaleString("en-US")} tokens`;

export function language() {
  return state.language;
}

export function history() {
  return [...state.history];
}

export function addHistory(text) {
  const said = text.trim();
  if (said && state.history[0] !== said) {
    state.history.unshift(said);
    state.history.length = Math.min(state.history.length, 100);
  }
  return { value: history(), notice: "Kept in the history." };
}

// ---------- the texts the settings send ----------

export function sharedReminder() {
  return { text: state.sharedReminder };
}

export function updateSharedReminder(text) {
  state.sharedReminder = text.trim();
  return {
    value: sharedReminder(),
    notice: state.sharedReminder ? "Shared reminder saved." : "Shared reminder cleared.",
  };
}

export function toolPrompt(tool) {
  return structuredClone(knownTool(tool));
}

export function updateToolPrompt(tool, text) {
  const prompt = knownTool(tool);
  return savePrompt(prompt, text, state.shippedToolPrompts[tool]);
}

function knownTool(tool) {
  const found = state.toolPrompts[tool];
  if (!found) {
    const tools = Object.keys(state.toolPrompts).join(", ");
    throw notFound(`Unknown tool '${tool}'. Tools: ${tools}.`);
  }
  return found;
}

export function prompt(name) {
  return structuredClone(knownPrompt(name));
}

export function updatePrompt(name, text) {
  return savePrompt(knownPrompt(name), text, state.shippedPrompts[name]);
}

function knownPrompt(name) {
  const found = state.prompts[name];
  if (!found) {
    const prompts = Object.keys(state.prompts).join(", ");
    throw notFound(`Unknown prompt '${name}'. Prompts: ${prompts}.`);
  }
  return found;
}

function savePrompt(prompt, text, shipped) {
  const typed = text.trim();
  prompt.text = typed ? text : shipped.text;
  return {
    value: structuredClone(prompt),
    notice: typed ? "Prompt saved." : "Prompt restored to the built-in.",
  };
}

// ---------- integrations ----------

export function webSearch() {
  return { has_key: false };
}

export function updateWebSearch(key) {
  if (!key.trim()) return { value: webSearch(), notice: "No web search key to clear." };
  throw conflict(NOT_IN_DEMO("Saving a web search key"));
}

export function supportRequest(body) {
  if (!body.trim()) throw invalid("Nothing to send: write what you need help with.");
  throw conflict(NOT_IN_DEMO("Sending a support request"));
}

// ---------- providers and models ----------

/* The panel: the demo's own provider, then the supported eight as a tab
   reaches them — not at all. The vocabulary is the captured listing's. */
export function providers(scope = "") {
  const rows = [demoProvider(), ...state.listing.providers.map(unreachable)];
  let shown = rows;
  if (scope === "local") shown = rows.filter((row) => row.locality !== "remote");
  else if (scope === "cloud") shown = rows.filter((row) => row.locality !== "local");
  else if (scope) {
    shown = rows.filter((row) => row.id === scope);
    if (!shown.length) throw notFound(`No provider is named ${scope}.`);
  }
  return {
    providers: shown,
    parameters: [...state.listing.parameters],
    effort_levels: [...state.listing.effort_levels],
  };
}

export function provider(name) {
  const row = providers().providers.find((each) => each.id === name);
  if (!row) throw notFound(`No supported provider is named ${name}.`);
  const { id, label, locality, configured, url, key_source } = row;
  return { id, label, locality, configured, url, key_source };
}

export function updateProvider(name) {
  provider(name);
  throw conflict(NOT_IN_DEMO("Saving a provider's settings"));
}

function demoProvider() {
  return {
    id: PROVIDER,
    label: "Demo",
    locality: "unknown",
    configured: true,
    url: "in this browser tab",
    asked: true,
    answered: true,
    failure: null,
    key_source: null,
    capabilities: structuredClone(state.providerCapabilities),
    models: models(PROVIDER),
  };
}

function unreachable(row) {
  return {
    ...structuredClone(row),
    // none of them is reachable from a browser tab
    url: row.configured ? row.url : "",
    asked: row.configured,
    answered: false,
    failure: row.configured ? `Could not reach ${row.id}.` : null,
    key_source: null,
    capabilities: null,
    models: [],
  };
}

export function models(providerName) {
  if (providerName !== PROVIDER) {
    provider(providerName);
    throw unavailable(`Could not reach ${providerName}.`);
  }
  return MODELS.map((model) => ({
    name: model.name,
    size: model.size,
    context_window: model.window,
    context_length: model.window,
    context_loaded: state.loaded.has(model.name) ? model.window : null,
    max_output_tokens: null,
    capabilities: { ...MODEL_CAPABILITIES },
    state: state.loaded.has(model.name) ? "loaded" : "unloaded",
    locality: null,
    text_template: null,
    reasoning_words: "not supported",
    capability_words: "none",
  }));
}

export function updateModel(providerName, name, loaded) {
  models(providerName);
  modelRow(name, true);
  if (loaded) state.loaded.add(name);
  else state.loaded.delete(name);
  const row = models(PROVIDER).find((model) => model.name === name);
  return { value: row, notice: loaded ? `Loaded ${name}.` : `Unloaded ${name}.` };
}

export function modelSettings(providerName, name) {
  models(providerName);
  modelRow(name, true);
  if (!state.modelSettings.has(name)) state.modelSettings.set(name, structuredClone(state.modelTemplate));
  return structuredClone(state.modelSettings.get(name));
}

/* A model's thinking level and its parameters, read against the model's
   vocabulary (`app.models.services`) — the levels the template carries, the
   bounds each parameter states. */
export function updateModelSettings(providerName, name, { think, parameters }) {
  const held = modelSettings(providerName, name);
  const said = [];
  if (think !== undefined) {
    const levels = held.choices.levels;
    if (!levels.includes(think)) {
      throw invalid(`${name} does not take ${think}. Levels for this model: ${levels.join(", ")}.`);
    }
    held.think = think;
    held.think_text = think;
    said.push(`Think: ${think}.`);
  }
  for (const [param, value] of Object.entries(parameters ?? {})) {
    const row = held.parameters.find((each) => each.name === param);
    if (!row) throw invalid(`Unknown parameter '${param}'.`);
    if (value === null || value === "") {
      row.value = null;
      row.text = "";
      delete held.params[param];
      said.push(`Parameter ${param} reset to default.`);
      continue;
    }
    const number = Number(value);
    if (Number.isNaN(number) || (row.kind === "int" && !Number.isInteger(number))) {
      throw invalid(`Could not parse '${value}' as ${row.kind}.`);
    }
    if ((row.low !== null && number < row.low) || (row.high !== null && number > row.high)) {
      const range = row.high === null ? `at least ${row.low}` : `between ${row.low} and ${row.high}`;
      throw invalid(`${param} must be ${range}.`);
    }
    row.value = number;
    row.text = String(value);
    held.params[param] = String(value); // the saved value, as models.toml holds it
    said.push(`${param} = ${value}.`);
  }
  if (!said.length) throw invalid("Nothing to change.");
  state.modelSettings.set(name, held);
  return { value: structuredClone(held), notice: said.join(" ") };
}

function modelRow(name, refuse = false) {
  const found = MODELS.find((model) => model.name === name);
  if (!found && refuse) throw notFound(`No model is named ${name}.`);
  return found ?? MODELS[0];
}

// ---------- reports ----------

export function usage(storyId = null) {
  const rows = storyId === null ? state.usage : state.usage.filter((row) => row.story === storyId);
  if (!rows.length) {
    throw notFound(storyId === null ? "No recorded usage yet." : "No recorded usage for this story.");
  }
  const prompt = rows.reduce((n, row) => n + row.prompt, 0);
  const completion = rows.reduce((n, row) => n + row.completion, 0);
  const seconds = rows.reduce((n, row) => n + row.seconds, 0);
  return {
    scope: storyId === null ? "all stories" : "this story",
    rows: [
      {
        purpose: "chat",
        provider: PROVIDER,
        model: state.model,
        requests: rows.length,
        prompt_tokens: prompt,
        completion_tokens: completion,
        cached_tokens: 0, // the demo's model keeps no cache to read from
        rate: seconds > 0 ? completion / seconds : 0,
        purpose_label: "Played turns",
      },
    ],
    requests: rows.length,
    prompt_tokens: prompt,
    completion_tokens: completion,
    cached_tokens: 0,
    total_tokens: prompt + completion,
    note: `${prompt.toLocaleString("en-US")} asked, ${completion.toLocaleString("en-US")} answered.`,
  };
}

export function info(storyId = null) {
  const story = storyId === null ? null : state.stories.get(storyId) ?? null;
  const rows = [];
  if (story) {
    rows.push(["Story", cut(labelOf(story), LABEL_WIDTH)], ["Messages", String(story.messages.length)]);
  }
  rows.push(["Extraction", state.pinned ? `${PROVIDER}/${state.pinned}` : "the story model"]);
  return {
    sections: [
      { rows: [["State dir", "(this browser tab — nothing leaves it)"]], note: "" },
      {
        rows: [
          ["Backend", "demo (a model that lives in the page)"],
          ["Model", state.model],
          ["Reasoning", "not supported"],
          ["Capabilities", "none"],
        ],
        note: "",
      },
      { rows, note: `otaku ${state.version} — this is the scripted demo; ${INSTALL}.` },
    ],
  };
}

export function balance() {
  // Nobody has a key in a browser tab, so every account says the same —
  // and the total is null, as it is whenever nothing answered.
  const nothing = (id, label) => ({ provider: id, label, money: null, note: "no key set", value: "no key set" });
  return {
    rows: [nothing("openrouter", "OpenRouter"), nothing("nanogpt", "NanoGPT")],
    total: null,
    note: `This story runs on ${PROVIDER}, which spends nothing. Paid providers are charged only when you switch to one.`,
  };
}

// ---------- the stories ----------

export function stories(query = "") {
  const needle = query.trim().toLowerCase();
  return [...state.stories.values()]
    .filter((story) => {
      if (!needle) return true;
      const face = [labelOf(story), story.storySoFar, ...story.messages.map((row) => row.body)];
      return face.join(" ").toLowerCase().includes(needle);
    })
    .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : a.updatedAt > b.updatedAt ? -1 : b.id - a.id))
    .map(summaryOf);
}

export function story(id) {
  return storyOf(held(id));
}

/** Every other story a chain of forks joins this one to, most recently
    played first (`story.stories.services.list_family`). */
export function forks(id) {
  held(id);
  const family = familyOf(id);
  return stories().filter((row) => family.includes(row.id));
}

/* The stories forks join one to, by id (`store.ops.stories.get_family`):
   a deleted story still joins the forks made from it. */
function familyOf(id) {
  const joined = new Map();
  const join = (a, b) => joined.set(a, [...(joined.get(a) ?? []), b]);
  for (const story of state.stories.values()) {
    if (story.forkedFrom == null) continue;
    join(story.id, story.forkedFrom);
    join(story.forkedFrom, story.id);
  }
  const seen = new Set([id]);
  const waiting = [id];
  while (waiting.length) {
    for (const other of joined.get(waiting.pop()) ?? []) {
      if (seen.has(other)) continue;
      seen.add(other);
      waiting.push(other);
    }
  }
  return [...seen].filter((other) => other !== id && state.stories.has(other)).sort((a, b) => a - b);
}

/* A story made at once — and, given codex cards (`cards`: each its id,
   name, aliases, the card its copy is of and its greeting, composed by
   the router), one started with them as `cards.bring_in_cards` starts
   it: a card row and a character each, linked — the row carrying the
   card's portrait for a card in `pictures` — then the first card's
   greeting; an untitled one named for them. */
export function createStory({ title = "", premise = "", cards = [], persona = "", pictures = [] } = {}) {
  if (new Set(cards.map((card) => card.id)).size !== cards.length) throw invalid("A card is picked twice.");
  const seen = new Set();
  for (const card of cards) {
    if (seen.has(card.name.toLowerCase())) {
      throw conflict(`Two of the cards are called ${card.name} — rename one first.`);
    }
    seen.add(card.name.toLowerCase());
  }
  const named = title.trim() || cards.map((card) => card.name).join(", ");
  const story = hold(state.nextStory++, { title: named, premise });
  let greeter = null;
  for (const card of cards) {
    const { characterId } = bringIn(story, {
      ...card,
      user: persona,
      line: `/card ${card.name}`,
      withPictures: pictures.includes(card.id),
    });
    if (!greeter && card.greeting) greeter = { ...card, characterId };
  }
  if (cards.length) story.persona = persona;
  if (greeter) {
    record(story.id, "assistant", greeter.greeting, {
      speaker: greeter.name,
      provider: "card",
      model: greeter.copy.file_name || null,
    });
  }
  return {
    value: storyOf(story),
    notice: named ? `Started a new story: "${cut(named, LABEL_WIDTH)}".` : "Started a new story.",
  };
}

/* The title, the premise and the head, whichever are given — the head
   moved back from the one the write names. */
export function updateStory(id, { title, premise, head }, expected) {
  const story = held(id);
  if (title === undefined && premise === undefined && head === undefined) throw invalid("Nothing to change.");
  if (title !== undefined && !title.trim()) throw invalid("A story needs a title — or leave the one it has.");
  if (head !== undefined) {
    checkHead(id, expected);
    if (!story.messages.some((row) => row.id === head)) throw notFound(OFF_CHAIN);
  }
  const said = [];
  if (title !== undefined) {
    story.title = title.trim();
    said.push(`Story title set to "${title.trim()}".`);
  }
  if (premise !== undefined) {
    story.premise = premise;
    said.push(premise ? `System prompt set (${premise.length} chars).` : "System prompt cleared.");
  }
  if (head !== undefined) {
    story.messages = story.messages.slice(0, story.messages.findIndex((row) => row.id === head) + 1);
    moved(story);
    said.push(landedLine(story, "Truncated"));
  }
  touch(story);
  return { value: storyOf(story), notice: said.join(" ") };
}

export function deleteStory(id) {
  held(id);
  state.stories.delete(id);
  if (state.settings.last_story === id) state.settings.last_story = null;
  return { value: null, notice: "Story deleted." };
}

export function fork(id, { at = null, title = "" } = {}) {
  const story = held(id);
  if (!story.messages.length) throw conflict("Nothing to fork yet — send a message first.");
  let kept = story.messages;
  if (at !== null) {
    const index = story.messages.findIndex((row) => row.id === at);
    if (index < 0) throw notFound(OFF_CHAIN);
    kept = story.messages.slice(0, index + 1);
  }
  const copy = hold(state.nextStory++, {
    title: title.trim() || numbered(story.title),
    premise: story.premise,
    storySoFar: story.storySoFar,
    messages: structuredClone(kept),
    settings: structuredClone(story.settings),
    lore: { ...structuredClone(story.lore), story_id: 0 },
    forkedFrom: story.id,
  });
  copy.lore.story_id = copy.id;
  const label = cut(labelOf(copy), LABEL_WIDTH);
  const lead = label ? `Forked to: ${label}.` : "Forked.";
  return {
    value: storyOf(copy),
    notice: at === null ? lead : `${lead} Continued from message ${copy.messages.length}.`,
  };
}

function numbered(title) {
  if (!title) return "";
  const taken = new Set([...state.stories.values()].map((story) => story.title));
  for (let n = 2; ; n += 1) if (!taken.has(`${title} - ${n}`)) return `${title} - ${n}`;
}

/* The trailing exchange taken back: the reply, and the one line of the
   reader's that prompted it — a continue's reply alone. */
export function undo(id, expected) {
  const story = held(id);
  checkHead(id, expected);
  const popped = [];
  if (story.messages.at(-1)?.role === "assistant") popped.push(story.messages.pop());
  if (story.messages.at(-1)?.role === "user") popped.push(story.messages.pop());
  if (!popped.length) throw conflict("Nothing to undo.");
  touch(story);
  moved(story);
  return { value: storyOf(story), notice: `Took back the last exchange (${popped.length} messages).` };
}

function landedLine(story, verb) {
  const label = cut(labelOf(story), LABEL_WIDTH);
  const head = label ? `Story: ${label}. ` : "";
  const count = story.messages.length;
  return count ? `${head}${verb} at message ${count}.` : `${head}${verb}, nothing played yet.`;
}

// ---------- messages ----------

export function messages(id, last = null) {
  const rows = held(id).messages;
  return structuredClone(last === null ? rows : rows.slice(-last));
}

export function message(id, messageId) {
  const found = held(id).messages.find((row) => row.id === messageId);
  if (!found) throw notFound(OFF_CHAIN);
  return structuredClone(found);
}

export function updateMessage(id, messageId, body) {
  const story = held(id);
  const found = story.messages.find((row) => row.id === messageId);
  if (!found) throw notFound(OFF_CHAIN);
  if (!body.trim()) throw invalid("A message cannot be emptied — undo the exchange instead.");
  found.body = body;
  found.segments = [{ kind: "prose", text: body }];
  touch(story);
  moved(story);
  return { value: structuredClone(found), notice: "Message edited." };
}

/** One row recorded at the story's head, in the shape the product's rows
    take — the demo's model writes prose and no tool call, so a row is
    one prose segment, its own unit. */
export function record(id, role, body, extra = {}) {
  const story = held(id);
  const previous = story.messages.at(-1) ?? null;
  const row = {
    id: state.nextMessage++,
    role,
    body,
    kind: "dialogue",
    template: null,
    speaker: null,
    provider: role === "assistant" ? PROVIDER : null,
    model: role === "assistant" ? state.model : null,
    attachments: [],
    mode: modeOf(layoutOf(story)),
    segments: [{ kind: "prose", text: body }],
    join_type: "paragraph",
    tool_label: null,
    unit: 0,
    ...extra,
  };
  row.join_type = joinOf(previous, row);
  row.unit = row.id;
  story.messages.push(row);
  touch(story);
  moved(story);
  return structuredClone(row);
}

/** How the reply about to stream joins the story: its first piece's
    `join_type`, by the rule the recorded row will carry. */
export function replyJoin(id) {
  const story = held(id);
  const mode = modeOf(layoutOf(story));
  return joinOf(story.messages.at(-1) ?? null, { mode, body: "x", kind: "dialogue", provider: PROVIDER });
}

/** Regenerate's first half: the standing reply set aside. */
export function dropReply(id) {
  const story = held(id);
  if (story.messages.at(-1)?.role !== "assistant") throw conflict("Nothing to regenerate.");
  story.messages.pop();
  touch(story);
  moved(story);
}

export function verbose() {
  return state.settings.verbose;
}

export function recordUsage(id, prompt, completion, seconds) {
  state.usage.push({ story: id, purpose: "chat", prompt, completion, seconds });
}

// ---------- a story's settings ----------

export function storySettings(id) {
  return structuredClone(held(id).settings);
}

export function storySetting(id, name) {
  return structuredClone(knownSetting(held(id), name));
}

function knownSetting(story, name) {
  const found = settingOf(story, name);
  if (!found) {
    const known = story.settings.map((row) => row.name).join(", ");
    throw notFound(`Unknown setting '${name}'. Settings: ${known}.`);
  }
  return found;
}

/* One setting, the fields given laid over it — what it takes decided and
   said as `story.settings.services` decides and says it, copied. */
export function updateStorySetting(id, name, fields) {
  const story = held(id);
  const setting = knownSetting(story, name);
  const holdsLayout = setting.layout !== null;
  if (holdsLayout && fields.enabled !== undefined) {
    throw invalid(`${setting.label} has no switch: a story is always read in one.`);
  }
  if (fields.position !== undefined) {
    const allowed = setting.allowed_positions.map((row) => row.value);
    if (!allowed.length) throw invalid(`${setting.label} has no position: it injects nothing.`);
    if (!allowed.includes(fields.position)) {
      throw invalid(`${setting.label} takes one of these positions: ${allowed.join(", ")}.`);
    }
  }
  if (fields.reminder_text !== undefined && setting.reminder_text === null) {
    throw invalid(`${setting.label} has no reminder of its own.`);
  }
  if (fields.display_notes !== undefined && setting.display_notes === null) {
    throw invalid(`${setting.label} has nothing to display.`);
  }
  if (fields.layout !== undefined && !holdsLayout) throw invalid(`${setting.label} has no layout.`);
  if (holdsLayout) {
    if (fields.layout !== undefined) setting.layout = fields.layout;
    moved(story);
    return { value: structuredClone(setting), notice: `${setting.label}: ${setting.layout}` };
  }
  if (fields.display_notes !== undefined) setting.display_notes = fields.display_notes;
  if (fields.enabled !== undefined) setting.enabled = fields.enabled;
  if (fields.reminder_text !== undefined) setting.reminder_text = fields.reminder_text;
  if (fields.position !== undefined) {
    const found = setting.allowed_positions.find((row) => row.value === fields.position);
    setting.injection_position = { value: found.value, text: found.text };
  }
  moved(story);
  return { value: structuredClone(setting), notice: standing(setting) };
}

// `StorySetting.standing`: where a switched-on setting rides, in words
function standing(setting) {
  if (!setting.enabled) return `${setting.label}: off.`;
  const position = setting.injection_position?.value ?? null;
  if (position === null) return `${setting.label}: on.`;
  if (position === "system") return `${setting.label}: on, in the system message.`;
  // before which of the reader's messages, counted from the end
  if (position === 1) return `${setting.label}: on, before your latest message.`;
  if (position === 2) return `${setting.label}: on, before your previous message.`;
  return `${setting.label}: on, before your ${position}${position === 3 ? "rd" : "th"}-last message.`;
}

// ---------- lore ----------

export function lore(id) {
  const story = held(id);
  return { ...structuredClone(story.lore), message_count: story.messages.length };
}

export function scenes(id) {
  return structuredClone(held(id).lore.scenes);
}

export function scene(id, sceneId) {
  return structuredClone(loreRow(held(id), "scenes", sceneId, "scene"));
}

export function updateScene(id, sceneId, { title, summary }) {
  const row = loreRow(held(id), "scenes", sceneId, "scene");
  if (title === undefined && summary === undefined) throw invalid("Nothing to change.");
  if (summary !== undefined && !summary.trim()) {
    throw invalid("An emptied summary would swallow its scene — not saved.");
  }
  if (title !== undefined) row.title = title;
  if (summary !== undefined) row.summary = summary;
  return { value: structuredClone(row), notice: "Saved." };
}

export function characters(id, name = null) {
  const rows = held(id).lore.characters;
  return structuredClone(name === null ? rows : rows.filter((row) => row.name === name));
}

export function character(id, characterId) {
  return structuredClone(loreRow(held(id), "characters", characterId, "character"));
}

export function updateCharacter(
  id,
  characterId,
  { description, profile, card_id: cardId, profile_fields: changes, portrait_sent: sent, with_copies: withCopies = false },
) {
  const row = loreRow(held(id), "characters", characterId, "character");
  if ([description, profile, cardId, changes, sent].every((part) => part === undefined)) {
    throw invalid("Nothing to change.");
  }
  const said = [];
  if (description !== undefined || profile !== undefined) {
    if (description !== undefined) row.description = description;
    if (profile !== undefined) row.profile = profile;
    said.push("Saved.");
  }
  if (changes !== undefined) said.push(correctCopy(row, changes));
  if (sent !== undefined) said.push(sendPortrait(held(id), row, sent));
  if (cardId !== undefined) said.push(linkCharacter(id, row, cardId, withCopies));
  return { value: structuredClone(row), notice: said.join(" ") };
}

/** A link vetted before it is made, nothing stored (`CharacterLinkCheck`): how
    many copies it takes along where asked. */
export function checkLink(id, characterId, cardId) {
  const row = loreRow(held(id), "characters", characterId, "character");
  const card = linkable(id, row, cardId);
  return { copies: copiesOf(id, row, card.id).length };
}

/* Whether a character's card picture rides its card row
   (`story.lore.services.set_portrait_sent`): refused where no card row
   brought it in, or there is no picture to send. */
function sendPortrait(story, row, sent) {
  if (row.portrait_sent === null || row.portrait_sent === undefined) {
    throw invalid(`${row.name} came into this story with no card.`);
  }
  if (sent && !row.card_portrait) throw invalid(`${row.name}'s card has no picture to send.`);
  for (const typed of story.messages) {
    if (typed.kind === "card" && typed.speaker === row.name) {
      typed.attachments = sent ? [structuredClone(row.card_portrait)] : [];
    }
  }
  row.portrait_sent = sent;
  return "Saved.";
}

/* A story character's profile corrected field by field and its TOML
   written again, the names it binds and its file kept
   (`story.lore.services.update_profile`). */
function correctCopy(row, changes) {
  if (!Object.keys(changes).length) throw invalid("Nothing to change.");
  if (!row.profile_fields) throw invalid(`${row.name} has no card to correct.`);
  const unknown = Object.keys(changes).filter((key) => !CARD_FIELDS.includes(key)).sort();
  if (unknown.length) throw invalid(`A card has no ${unknown.join(", ")}.`);
  checkDepth(changes);
  const { user, file_name: fileName } = boundOf(row.profile);
  row.profile_fields = { ...row.profile_fields, ...structuredClone(changes) };
  row.profile = profileToml({ ...row.profile_fields, file_name: fileName }, user);
  return "Saved.";
}

/* The persona a story character's profile binds and the file it came
   from, read back off its TOML (`CharacterProfile.from_toml`): one line
   each, a literal or a JSON-quoted string as either writer writes it. */
function boundOf(profile) {
  const found = {};
  for (const line of (profile ?? "").split("\n")) {
    const match = /^(user|file_name) = (.*)$/.exec(line);
    if (!match || match[1] in found) continue;
    const raw = match[2];
    found[match[1]] = raw.startsWith("'") ? raw.slice(1, -1) : JSON.parse(raw);
  }
  return { user: found.user ?? "", file_name: found.file_name ?? "" };
}

/* A story's character linked to a codex card — its copies too where
   asked — or unlinked with null, alone, as `story.lore.services.set_card_id`
   links it: nothing else on either changes. */
function linkCharacter(id, row, cardId, withCopies = false) {
  if (cardId === null) {
    if (row.card_id == null) throw invalid("Nothing to change.");
    const was = cardRow(row.card_id);
    row.card_id = null;
    showCard(row, null);
    return `Unlinked ${row.name} from ${was.name}.`;
  }
  const card = linkable(id, row, cardId);
  const found = withCopies ? copiesOf(id, row, card.id) : [];
  linkRows([row, ...found.map(copyRow)], card);
  return `Linked ${row.name} to ${card.name}.${linkedCopies(found.length)}`;
}

/* Story characters linked to a codex card, its name and portrait beside
   each as a character answers it. */
function linkRows(rows, card) {
  for (const each of rows) {
    each.card_id = card.id;
    showCard(each, card);
  }
}

/* What a story character shows of its linked card — its name and its
   first picture (`backend.story.lore._character`) — or of none. */
function showCard(row, card) {
  row.card_name = card?.name ?? null;
  row.card_portrait = portraitOf(card);
}

function portraitOf(card) {
  return card?.pictures.length ? structuredClone(card.pictures[0]) : null;
}

/* The card a link joins, refused as `story.lore.services._linkable` says. */
function linkable(id, row, cardId) {
  if (cardId === row.card_id) throw invalid("Nothing to change.");
  const card = cardRow(cardId);
  const other = held(id).lore.characters.find((each) => each.card_id === card.id);
  if (other) throw conflict(`${other.name} in this story is linked to ${card.name} already.`);
  return card;
}

/* A story character's copies (`codex.cards.services.find_copies`): in every
   story forks join its own to, the one unlinked character of its very
   name — a story holding two passed over, as is one where `cardId`
   links another already. */
function copiesOf(id, row, cardId = null) {
  const found = [];
  for (const other of familyOf(id)) {
    const cast = held(other).lore.characters;
    if (cardId !== null && cast.some((each) => each.card_id === cardId)) continue;
    const named = cast.filter((each) => each.card_id == null && each.name === row.name);
    if (named.length === 1) found.push({ story_id: other, character_id: named[0].id });
  }
  return found;
}

function copyRow({ story_id: story, character_id: character }) {
  return held(story).lore.characters.find((each) => each.id === character);
}

/* What a link says of the copies it took along
   (`codex.cards.services.linked_copies`). */
function linkedCopies(count) {
  return count ? ` Linked in ${count} other ${count === 1 ? "story" : "stories"} too.` : "";
}

export function deleteCharacter(id) {
  held(id);
  throw conflict(NOT_IN_DEMO("Deleting a character"));
}

export function mergeCharacter(id) {
  held(id);
  throw conflict(NOT_IN_DEMO("Merging characters"));
}

export function journals(id) {
  return structuredClone(held(id).lore.journals);
}

export function journal(id, journalId) {
  return structuredClone(loreRow(held(id), "journals", journalId, "journal"));
}

export function updateJournal(id, journalId, { entry }) {
  const row = loreRow(held(id), "journals", journalId, "journal");
  if (entry === undefined) throw invalid("Nothing to change.");
  row.entry = entry;
  return { value: structuredClone(row), notice: "Saved." };
}

function loreRow(story, list, rowId, noun) {
  const found = story.lore[list].find((row) => row.id === rowId);
  if (!found) throw notFound(`That ${noun} is not in this story's memory.`);
  return found;
}

/* The forced pass: started (202, polled), read until done, given up —
   the demo's model closes a canned scene a moment later
   (`script.nextScene`), in the shape the real pass writes. */
export function startExtraction(id) {
  const story = held(id);
  if (story.extraction?.status === "running") {
    return { value: structuredClone(story.extraction), notice: "A pass is already running." };
  }
  story.extraction = { story_id: id, status: "running", report: null };
  state.status = "closing a scene…";
  const run = story.extraction;
  setTimeout(() => {
    if (run.status !== "running") return;
    run.report = closeScene(story);
    run.status = "done";
    state.status = "";
  }, 2500);
  return { value: structuredClone(run), notice: "Extracting lore from the recent messages…" };
}

export function extraction(id) {
  const story = held(id);
  if (!story.extraction) throw notFound("No pass was asked for this story.");
  return structuredClone(story.extraction);
}

export function stopExtraction(id) {
  const story = held(id);
  if (story.extraction?.status !== "running") throw conflict("No pass is running.");
  story.extraction.status = "cancelled";
  story.extraction.report = "Cancelled — nothing half-done commits; already-closed scenes stay.";
  state.status = "";
  return { value: null, notice: story.extraction.report };
}

function closeScene(story) {
  const covered = story.lore.extracted_up_to;
  const last = story.messages.length - 2; // the settle margin: the tail stays open
  if (last - covered < 4) return "Nothing new since the last scene.";
  const canned = script.nextScene();
  const sceneId = state.nextLore++;
  const no = story.lore.scenes.length + 1;
  story.lore.scenes.push({
    id: sceneId,
    no,
    span: `${covered + 1}-${last}`,
    title: canned.title,
    summary: canned.summary,
    history: "",
    start_message_id: story.messages[covered]?.id ?? null,
    end_message_id: story.messages[last - 1]?.id ?? null,
    provider: PROVIDER,
    model: state.pinned ?? state.model,
    updated_at: now(),
  });
  for (const person of story.lore.characters) {
    story.lore.journals.push({
      id: state.nextLore++,
      scene_id: sceneId,
      scene_no: no,
      character_id: person.id,
      entry: canned.entry,
      state: canned.state,
      history: "",
      messages_since: 0,
      provider: PROVIDER,
      model: state.pinned ?? state.model,
      updated_at: now(),
    });
  }
  story.lore.extracted_up_to = last;
  story.storySoFar = canned.summary;
  return `Scene '${canned.title}' closed: ${story.lore.characters.length} journal(s) written.`;
}

// ---------- cards ----------

export function personaOf(id) {
  return held(id).persona;
}

/* The card's three writes, the codex card `card` brought in: the typed
   /card row, speaker-linked; the cast row with the card's profile, made by
   the card and linked to it; the greeting as the character's own first
   words. Answered as the two rows, the codex card and the report. */
export function importCard(id, expected, { card, name, aliases, copy, greeting, line, persona, withPictures, report }) {
  const story = held(id);
  checkHead(id, expected);
  ensureFree(id, name, card);
  const { characterId, typed } = bringIn(story, { id: card, name, aliases, copy, user: persona, line, withPictures });
  story.persona = persona;
  const said = greeting
    ? record(id, "assistant", greeting, { speaker: name, provider: "card", model: copy.file_name || null })
    : null;
  return {
    value: { character_id: characterId, card_id: card, line: typed, greeting: said, report },
    notice: report,
  };
}

/** Refused where the story's cast already answers to the name, or holds
    the codex card already under another (`cards._ensure_free`). */
export function ensureFree(id, name, cardId = null) {
  const story = held(id);
  const existing = story.lore.characters.find((row) => row.name.toLowerCase() === name.toLowerCase());
  if (existing) {
    const source = existing.profile ? "imported earlier" : "extracted from play";
    throw conflict(`${existing.name} already exists in this story (${source}) — import under another name: /card FILE NAME.`);
  }
  const linked = cardId === null ? null : story.lore.characters.find((row) => row.card_id === cardId);
  if (linked) throw conflict(`${cardRow(cardId).name} is in this story already, as ${linked.name}.`);
}

function bringIn(story, { id: cardId, name, aliases = [], copy, user, line, withPictures = false }) {
  const characterId = state.nextLore++;
  const fields = copyFields(copy);
  const card = state.cards.get(cardId) ?? null;
  // the card's portrait rides its row where asked and it has one
  const riding = withPictures ? portraitOf(card) : null;
  story.lore.characters.push({
    id: characterId,
    name,
    aliases: aliases.filter((alias) => alias.toLowerCase() !== name.toLowerCase()),
    description: "",
    profile: profileToml({ ...fields, file_name: copy.file_name }, user),
    profile_fields: fields,
    history: "",
    history_journal_id: null,
    card_id: cardId,
    card_name: card?.name ?? null,
    card_portrait: portraitOf(card),
    portrait_sent: riding !== null,
    // a card made it, and is answered with no model
    // (`backend.story.lore._character`)
    provider: "card",
    model: null,
    updated_at: now(),
  });
  const typed = record(story.id, "user", line, {
    kind: "card",
    speaker: name,
    provider: null,
    model: null,
    attachments: riding ? [riding] : [],
  });
  return { characterId, typed };
}

/* A card's own fields as a story character's profile answers them
   (`ProfileFields`): every one, an empty one empty. */
function copyFields(card) {
  return Object.fromEntries(CARD_FIELDS.map((key) => [key, structuredClone(card[key])]));
}

export function profileToml(card, user) {
  // A story character's profile, its keys in `CharacterProfile.to_toml`'s
  // order — the persona it binds first where there is one, the name its
  // row's; the demo writes plain TOML — the same fields, its own formatting.
  const pair = (key, value) =>
    Array.isArray(value)
      ? `${key} = [${value.map((item) => JSON.stringify(item)).join(", ")}]`
      : `${key} = ${JSON.stringify(value)}`;
  const lines = user ? [pair("user", user)] : [];
  for (const key of [...CARD_FIELDS, "file_name"]) {
    const value = card[key];
    // a number where it is not its default, as the product writes it
    if (key === "reminder_depth" ? value == null || value === 2 : value == null || value.length === 0) continue;
    lines.push(pair(key, value));
  }
  return lines.join("\n\n");
}

// ---------- the codex ----------

/* All characters, as `backend.codex.cards.services` keeps them: the cards,
   the stories each is in read off the casts as the store reads them, the
   digest of each card file a card was made from, and the stories'
   characters no card claims. A picture added to a card is not kept: the
   demo has nowhere to keep one, and the samples' portraits come with the
   fixtures. */

const NO_SUCH_CARD = "No such card.";
// a card's fields as `CardsResource.update` takes them, the name and the
// tagline aside
const CARD_FIELDS = [
  "description", "personality", "scenario", "greeting", "alternate_greetings", "examples",
  "reminder", "reminder_depth", "system_prompt", "post_history_instructions", "creator_notes",
];

function cardRow(id) {
  const found = state.cards.get(id);
  if (!found) throw notFound(NO_SUCH_CARD);
  return found;
}

/** The story characters a card is linked to, by story (`StoryCharacter`). */
function linkedTo(cardId) {
  const rows = [];
  for (const story of [...state.stories.values()].sort((a, b) => a.id - b.id)) {
    for (const row of story.lore.characters) if (row.card_id === cardId) rows.push(storyCharacter(story, row));
  }
  return rows;
}

function storyCharacter(story, row) {
  return {
    story_id: story.id,
    story_label: labelOf(story),
    character_id: row.id,
    name: row.name,
    aliases: structuredClone(row.aliases),
    journal_count: story.lore.journals.filter((record) => record.character_id === row.id).length,
  };
}

const cardOf = (card) => ({ ...structuredClone(card), stories: linkedTo(card.id) });

export function cardSummary(card) {
  return {
    id: card.id,
    name: card.name,
    aliases: structuredClone(card.aliases),
    tagline: card.tagline,
    pictures: structuredClone(card.pictures),
    story_count: linkedTo(card.id).length,
  };
}

/** Every card in the reader's order — a card made goes last — or those
    whose name, an alias or the tagline holds `query`. */
export function cards(query = "") {
  const needle = query.trim().toLowerCase();
  return [...state.cards.values()]
    .filter((card) => !needle || [card.name, ...card.aliases, card.tagline].join(" ").toLowerCase().includes(needle))
    .map(cardSummary);
}

export function card(id) {
  return cardOf(cardRow(id));
}

function cleanName(name) {
  const cleaned = (name ?? "").trim();
  if (!cleaned) throw invalid("A card needs a name.");
  return cleaned;
}

function cleanTagline(tagline) {
  const cleaned = (tagline ?? "").trim();
  if (cleaned.includes("\n")) throw invalid("A tagline is one line.");
  return cleaned;
}

const cleanAliases = (aliases, name) => [
  ...new Set(aliases.map((alias) => alias.trim()).filter((alias) => alias && alias.toLowerCase() !== name.toLowerCase())),
];

/* A reminder goes before one of the reader's messages, the newest 1
   (`backend.codex.cards.services._checked_profile`). */
function checkDepth(fields) {
  const depth = fields.reminder_depth;
  if (depth !== undefined && !(Number.isInteger(depth) && depth >= 1)) {
    throw invalid("A reminder's depth is a whole number from 1.");
  }
}

function makeCard({ name, aliases = [], tagline = "", fields = {}, fileName = "" }) {
  const id = state.nextCard++;
  const made = {
    id,
    name,
    aliases: cleanAliases(aliases, name),
    tagline,
    description: "", personality: "", scenario: "", greeting: "",
    alternate_greetings: [], examples: [],
    reminder: "", reminder_depth: 2, system_prompt: "", post_history_instructions: "", creator_notes: "",
    file_name: fileName,
    pictures: [],
    updated_at: now(),
  };
  for (const key of CARD_FIELDS) if (fields[key] !== undefined) made[key] = structuredClone(fields[key]);
  state.cards.set(id, made);
  return made;
}

// What a card made by hand is called where no name is given
// (`backend.codex.cards.services.NEW_CARD_NAME`).
const NEW_CARD_NAME = "New card";

export function createCard({ name = NEW_CARD_NAME, aliases = [], tagline = "", fields = {} }) {
  checkDepth(fields);
  const made = makeCard({ name: cleanName(name), aliases, tagline: cleanTagline(tagline), fields });
  return { value: cardOf(made), notice: `Added ${made.name} to the codex.` };
}

/* The interface's correction: the parts given, then a move where one is
   asked (`CardsResource.update`), each saying its sentence. */
export function updateCard(id, { before, ...fields }) {
  const card = cardRow(id);
  if (!Object.keys(fields).length && before === undefined) throw invalid("Nothing to change.");
  if (before !== undefined) checkMove(card, before);
  const said = [];
  if (Object.keys(fields).length) said.push(correctCard(card, fields));
  if (before !== undefined) said.push(moveCard(card, before));
  return { value: cardOf(card), notice: said.join(" ") };
}

/* A move refused before anything is written, as
   `backend.codex.cards.services.check_move` refuses it. */
function checkMove(card, before) {
  if (before === card.id) throw invalid("A card cannot go before itself.");
  if (before !== null) cardRow(before);
}

/* A card put just before `before`, or last for null
   (`backend.codex.cards.services.move_card`). */
function moveCard(card, before) {
  const order = [...state.cards.values()].filter((each) => each.id !== card.id);
  const at = before === null ? order.length : order.findIndex((each) => each.id === before);
  order.splice(at, 0, card);
  state.cards = new Map(order.map((each) => [each.id, each]));
  return `Moved ${card.name}.`;
}

function correctCard(card, fields) {
  checkDepth(fields);
  const name = fields.name !== undefined ? cleanName(fields.name) : card.name;
  const tagline = fields.tagline !== undefined ? cleanTagline(fields.tagline) : card.tagline;
  let pictures = card.pictures;
  if (fields.pictures !== undefined) {
    const byName = new Map(card.pictures.map((picture) => [picture.file, picture]));
    const wanted = fields.pictures;
    if ([...wanted].sort().join("\n") !== [...byName.keys()].sort().join("\n")) {
      throw invalid("A new order names each of the card's pictures once.");
    }
    pictures = wanted.map((file) => byName.get(file));
  }
  card.name = name;
  card.tagline = tagline;
  card.pictures = pictures;
  card.aliases = cleanAliases(fields.aliases ?? card.aliases, name);
  for (const key of CARD_FIELDS) if (fields[key] !== undefined) card[key] = structuredClone(fields[key]);
  card.updated_at = now();
  reachCasts(card);
  return "Saved.";
}

export function deleteCard(id) {
  const card = cardRow(id);
  const stories = new Set(linkedTo(id).map((row) => row.story_id)).size;
  for (const story of state.stories.values()) {
    for (const row of story.lore.characters) {
      if (row.card_id === id) {
        row.card_id = null;
        showCard(row, null);
      }
    }
  }
  state.cards.delete(id);
  state.cardDigests.delete(id);
  let said = `Deleted ${card.name} from the codex.`;
  if (stories) said += ` Its characters in ${stories} ${stories === 1 ? "story" : "stories"} stay, unlinked.`;
  return { value: null, notice: said };
}

export function mergeCard(id, into) {
  const source = cardRow(id);
  const target = cardRow(into);
  if (source.id === target.id) throw invalid(`${source.name} and ${target.name} are already the same card.`);
  const stories = new Set(linkedTo(source.id).map((row) => row.story_id));
  const shared = linkedTo(target.id).find((row) => stories.has(row.story_id));
  if (shared) {
    throw conflict(
      `"${shared.story_label}" has characters linked to both ${source.name} and ${target.name}.`,
    );
  }
  for (const story of state.stories.values()) {
    for (const row of story.lore.characters) {
      if (row.card_id === source.id) row.card_id = target.id;
    }
  }
  target.aliases = cleanAliases([...target.aliases, source.name, ...source.aliases], target.name);
  const kept = new Set(target.pictures.map((picture) => picture.file));
  target.pictures = [...target.pictures, ...source.pictures.filter((picture) => !kept.has(picture.file))];
  reachCasts(target);
  state.cardDigests.set(target.id, [...new Set([...(state.cardDigests.get(target.id) ?? []), ...(state.cardDigests.get(source.id) ?? [])])]);
  state.cards.delete(source.id);
  state.cardDigests.delete(source.id);
  target.updated_at = now();
  return { value: cardOf(target), notice: `Merged ${source.name} into ${target.name} ('${source.name}' is now an alias).` };
}

export function addCardPicture(id) {
  cardRow(id);
  throw conflict(NOT_IN_DEMO("A card's picture"));
}

/** A picture taken off a card by its file name (`delete_card_picture`). */
export function removeCardPicture(id, name) {
  const card = cardRow(id);
  const kept = card.pictures.filter((picture) => picture.file !== name);
  if (kept.length === card.pictures.length) throw notFound(`${card.name} has no such picture.`);
  card.pictures = kept;
  card.updated_at = now();
  reachCasts(card);
  return { value: cardOf(card), notice: "Picture removed." };
}

/** Every story's character no card claims — or those whose name, an alias
    or a journal record's entry or state holds the query — the most written
    about first. */
export function unlinked(query = "") {
  const needle = query.trim().toLowerCase();
  const rows = [];
  for (const story of [...state.stories.values()].sort((a, b) => a.id - b.id)) {
    for (const row of story.lore.characters) {
      if (row.card_id != null) continue;
      const journaled = story.lore.journals
        .filter((record) => record.character_id === row.id)
        .flatMap((record) => [record.entry, record.state]);
      const text = [row.name, ...row.aliases, ...journaled].join("\n").toLowerCase();
      if (needle && !text.includes(needle)) continue;
      rows.push(storyCharacter(story, row));
    }
  }
  return rows.sort((a, b) => b.journal_count - a.journal_count);
}

/** A card made from a story's character and linked to it — its copies
    too where asked. */
export function promote(storyId, characterId, { withCopies = false } = {}) {
  const row = promotable(storyId, characterId);
  const found = withCopies ? copiesOf(storyId, row) : [];
  const made = makeCard({ name: row.name, aliases: row.aliases, fields: { description: row.description } });
  linkRows([row, ...found.map(copyRow)], made);
  return {
    value: cardOf(made),
    notice: `Added ${row.name} to the codex, linked to this story's character.${linkedCopies(found.length)}`,
  };
}

/** A promotion vetted before it is made, nothing stored (`CharacterLinkCheck`). */
export function checkPromotion(storyId, characterId) {
  return { copies: copiesOf(storyId, promotable(storyId, characterId)).length };
}

function promotable(storyId, characterId) {
  const row = held(storyId).lore.characters.find((each) => each.id === characterId);
  if (!row) throw notFound("That character is not in this story's memory.");
  if (row.card_id != null) {
    throw conflict(`This character is linked to ${cardRow(row.card_id).name} in the codex already.`);
  }
  return row;
}

/** What tells one card file's card from another's: its fields as read,
    the file's name left out. */
const digestOf = (found) =>
  JSON.stringify(Object.keys(found).filter((key) => key !== "file_name").sort().map((key) => [key, found[key]]));

export function sameCard(found) {
  const digest = digestOf(found);
  const id = [...state.cardDigests.entries()].find(([, digests]) => digests.includes(digest))?.[0];
  return id === undefined ? null : state.cards.get(id);
}

/** A card file read for the codex, nothing stored (`CardCheck`). */
export function checkCardFile(found, notes, hasPicture, rename = "") {
  const same = sameCard(found);
  const named = rename.trim() || found.name;
  const wanted = new Set([named.toLowerCase(), found.name.toLowerCase()]);
  const similar = [...state.cards.values()].filter(
    (card) => card !== same && [card.name, ...card.aliases].some((alias) => wanted.has(alias.toLowerCase())),
  );
  return {
    name: named,
    notes,
    has_picture: hasPicture,
    same_card: same ? cardSummary(same) : null,
    similar_cards: similar.map(cardSummary),
  };
}

/** A card file into the codex (`CardImport`): one the codex holds already
    makes nothing, `reuse` names the card the reader says it is; else a
    card is made — its picture, where the file has one, not in the demo. */
export function importCardFile(found, notes, hasPicture, { rename = "", reuse = null } = {}) {
  const digest = digestOf(found);
  const same = sameCard(found);
  if (same) return { value: { card: cardOf(same), made: false }, notice: `${same.name} is in the codex already.` };
  if (reuse !== null) {
    const kept = cardRow(reuse);
    state.cardDigests.set(kept.id, [...(state.cardDigests.get(kept.id) ?? []), digest]);
    return { value: { card: cardOf(kept), made: false }, notice: `Kept ${kept.name}: the codex holds it already.` };
  }
  const name = rename.trim() ? cleanName(rename) : found.name;
  const made = makeCard({ name, fields: found, fileName: found.file_name ?? "" });
  state.cardDigests.set(made.id, [digest]);
  const said = [`Added ${name} to the codex`, ...notes];
  if (hasPicture) said.push(`its picture was not kept: ${NOT_IN_DEMO("A card's picture")}`);
  const sentence = said.join(" · ");
  return { value: { card: cardOf(made), made: true }, notice: sentence.endsWith(".") ? sentence : `${sentence}.` };
}

/** The stories' casts show the cards they are linked to (`showCard`): a
    rename or a new first picture reaches them. */
function reachCasts(card) {
  for (const story of state.stories.values()) {
    for (const row of story.lore.characters) if (row.card_id === card.id) showCard(row, card);
  }
}

// ---------- the context ----------

/* The captured preview is the real assembler's work and is served while
   it is true; a story that moved gets an honest recompute in its shape —
   the demo's model counts nothing, so a token is four characters. */
export function context(id) {
  const story = held(id);
  if (story.context) return structuredClone(story.context);
  const systemTokens = estimate(story.premise);
  const transcript = story.messages.reduce((n, row) => n + estimate(row.body), 0);
  const total = systemTokens + transcript;
  const limit = limitOf();
  const used = Math.round((100 * total) / limit);
  const lede = [
    "Context preview — the exact request to be sent. Context summary:",
    "",
    `  ~${total.toLocaleString("en-US")} tokens projected · ${used}% of the ${limit.toLocaleString("en-US")} limit`,
    ...(systemTokens ? [`  system ${systemTokens.toLocaleString("en-US")} · transcript ${transcript.toLocaleString("en-US")}`] : []),
    `  ${story.messages.length} messages verbatim`,
  ].join("\n");
  return {
    shape: {
      head: story.messages.length,
      middle: 0,
      history: false,
      rolled_up: 0,
      summaries: 0,
      tail: 0,
      tail_target: 150,
      tail_setting: 150,
      system_tokens: systemTokens,
      transcript_tokens: transcript,
      limit,
      pictures_sent: 0,
      pictures_omitted: 0,
      pictures_held: 0,
      kept: story.messages.length,
      total_tokens: total,
      used,
    },
    lede,
    note: "",
    // the card rows as the product lists them, each costed by the profile
    // its character holds — what the product sends in the row's place
    cards: story.messages
      .filter((row) => row.kind === "card")
      .map((row) => {
        const cast = story.lore.characters.find((character) => character.name === row.speaker);
        return { name: row.speaker ?? "", tokens: estimate(cast?.profile ?? row.body) };
      }),
    // the recompute assembles no injection, so it reports none, honestly
    injections: [],
    parts: [
      ...(story.premise ? [{ role: "system", body: story.premise, pictures: 0 }] : []),
      ...story.messages.map((row) => ({ role: row.role === "tool" ? "user" : row.role, body: row.body, pictures: 0 })),
    ],
  };
}

export function fill(id) {
  const story = held(id);
  if (story.fill) return structuredClone(story.fill);
  const { shape } = context(id);
  const window = modelRow(state.model).window;
  const size = window >= 1024 ? `${Math.round(window / 1024)}K` : String(window);
  return { percent: shape.used, context_in_force: window, text: `${shape.used}% of ${size}` };
}

export function contextTokens(id) {
  return context(id).shape.total_tokens;
}

const limitOf = () => {
  const window = modelRow(state.model).window;
  const cap = state.settings.context_limit;
  return (cap && cap < window ? cap : window) - RESERVE;
};

// ---------- transfer ----------

export function exportDocument(id) {
  const story = held(id);
  if (!story.messages.length) throw conflict("Nothing to export yet.");
  const label = labelOf(story);
  return {
    name: `${slug(label) || "story"}.md`,
    text: [
      `# ${label}`,
      "",
      "*Played in the otaku demo — a real otaku exports the full document, memory included.*",
      "",
      ...story.messages.map((row) => (row.role === "user" ? `> ${row.body}` : row.body)),
    ].join("\n\n"),
  };
}

/* A document landed as a story: plain text alone here — a paragraph a
   message, the reader's and the model's in turn — and a pass started over
   it, as the product starts one. */
export function importDocument(text, fileName) {
  if (!fileName.toLowerCase().endsWith(".txt")) {
    throw conflict(
      `Only plain-text imports work in the demo — ${INSTALL} (SillyTavern chats and otaku exports included).`,
    );
  }
  const paragraphs = text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  if (!paragraphs.length) throw invalid("The file contains no text to import.");
  const story = hold(state.nextStory++);
  paragraphs.forEach((body, i) => record(story.id, i % 2 ? "assistant" : "user", body));
  const notice = `Imported ${paragraphs.length} message(s) → story ${story.id}.`;
  const started = startExtraction(story.id);
  return { value: { story_id: story.id, notices: [notice], extraction: started.value }, notice };
}

// ---------- the pictures ----------

/** The file names a sample's messages carry — what the demo can serve. */
/** Every stored picture's name: the messages' and the cards'. */
export function pictureNames() {
  const names = new Set();
  for (const story of state.stories.values()) {
    for (const row of story.messages) for (const picture of row.attachments) names.add(picture.file);
  }
  for (const card of state.cards.values()) for (const picture of card.pictures) names.add(picture.file);
  return names;
}

// ---------- internals ----------

function touch(story) {
  story.updatedAt = now();
}

function moved(story) {
  // The captured preview stops being true the moment its story moves; the
  // recompute takes over for that story alone.
  story.context = null;
  story.fill = null;
}

function now() {
  // the product's spelling: local time, its offset, whole seconds
  const date = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
    `${sign}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`
  );
}

const estimate = (text) => Math.ceil(text.length / 4);
const flatten = (text) => text.replace(/\s+/g, " ").trim();
const cut = (text, limit) => (text.length > limit ? `${text.slice(0, limit - 1)}…` : text);

function slug(text) {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/[\s_-]+/g, "-")
    .replace(/^-|-$/g, "");
}

/* The demo's session: the state a real otaku keeps below both
   frontends, held in this tab's memory instead. Seeded from the
   captured fixtures (the shipped sample story, read off a real
   session), mutated by the same operations the page performs, and
   forgotten on reload — which makes reload the demo's reset button.

   Sentences mirror the backend's wording (`otaku/backend/api/*`)
   so the demo answers exactly as the product would; anything the demo
   deliberately cannot do says so honestly and points at the install. */

// What the fake provider claims to be. One provider with two models, so
// the picker's switch, load and unload have something real to do.
export const PROVIDER = "demo";
// What a local engine states of its models, in the product's shape.
const MODEL_CAPABILITIES = {
  vision: false,
  audio: false,
  reasoning: [],
  text_completion: true,
  structured_output: true,
};
const MODELS = [
  { name: "demo-model", loaded: true, size: "4.7 GB", max_context_catalogue: "32K", max_context_loaded: "", capabilities: { ...MODEL_CAPABILITIES } },
  { name: "demo-model-mini", loaded: false, size: "1.9 GB", max_context_catalogue: "8K", max_context_loaded: "", capabilities: { ...MODEL_CAPABILITIES } },
];
// What the demo's provider can do: it manages models, counts nothing
// exactly, marks no cache, and reads the protocol's own parameters and
// no sampler beyond them — the fixtures are captured over a scripted
// Ollama, whose wire reads exactly those, and the settings fixture lists
// the same seven.
const CAPABILITIES = {
  tokenizer: false,
  prompt_cache: false,
  model_management: true,
  supported_params: [
    "temperature", "top_p", "max_tokens", "presence_penalty", "frequency_penalty", "seed", "stop",
  ],
};
// The window the fixtures' context previews were captured under
// (scripts/demo_fixtures.py) — the two must agree, or the demo's own
// numbers argue with the captured ledes.
const WINDOW = 32768;
const LIMIT = WINDOW - 1024; // approximates the real assembler's adaptive reply reserve

export const INSTALL = "install otaku from otaku.sh to do this for real";

const state = {
  stories: new Map(), // id → {title, system, turns:[{id,role,body}], updatedAt}
  lore: new Map(), // id → {scenes:[…], cast:[…]} as the lore read shapes them
  open: null, // the open story's id
  nextStory: 1,
  nextMessage: 1,
  model: "demo-model",
  settings: null, // seeded from the settings fixture; values live here
  history: [], // the composer's ↑/↓ lines, most recent first
  usage: [], // one row per completed reply: {story, prompt, completion, seconds}
  syntax: null, // the story's typed language, for the menu and the sheet
  contextFixtures: new Map(), // id → captured preview, served until that story moves
  status: "", // what /api/status reports the worker doing
};

export function seed(fixtures) {
  const { river, tour, settings, syntax } = fixtures;
  state.syntax = syntax;
  state.settings = structuredClone(settings);
  // Both shipped samples, the way a fresh install seeds them; the row's
  // own `open` flag says which one the demo lands in.
  for (const sample of [river, tour].filter(Boolean)) {
    const id = state.nextStory++;
    state.stories.set(id, {
      title: sample.story.title || "",
      system: sample.opened.premise || "",
      turns: sample.opened.messages.map((t) => ({ ...t })),
      updatedAt: sample.story.updated_at,
    });
    state.lore.set(id, {
      scenes: structuredClone(sample.opened.scenes),
      characters: structuredClone(sample.opened.characters),
    });
    if (sample.context) state.contextFixtures.set(id, sample.context);
    if (sample.story.open) state.open = id;
    state.nextMessage = Math.max(state.nextMessage, ...sample.opened.messages.map((t) => t.id)) + 1;
  }
}

// ---------- reads ----------

export function facts(version) {
  const story = state.stories.get(state.open);
  const model = MODELS.find((m) => m.name === state.model);
  return {
    version,
    model: state.model,
    provider: "demo",
    max_context: model ? model.max_context_catalogue : "",
    story: story ? label(story) : "",
    story_id: state.open,
    turns: story ? story.turns.length : 0,
  };
}

export function turns() {
  const story = state.stories.get(state.open);
  return story ? story.turns.map((t) => ({ ...t })) : [];
}

export function history() {
  return [...state.history];
}

export function syntax() {
  return state.syntax;
}

export function stories(q = "") {
  const needle = q.trim().toLowerCase();
  return [...state.stories.entries()]
    .filter(([id, s]) => {
      if (!needle) return true;
      const face = `${label(s)} ${storySoFar(id)} ${firstUser(s)} ${PROVIDER}/${state.model}`;
      return `${face} ${s.turns.map((t) => t.body).join(" ")}`.toLowerCase().includes(needle);
    })
    .sort((a, b) => (a[1].updatedAt < b[1].updatedAt ? 1 : -1))
    .map(([id, s]) => ({
      id,
      label: label(s),
      title: s.title,
      story_so_far: storySoFar(id),
      first_user: firstUser(s),
      model: `${PROVIDER}/${state.model}`,
      updated_at: s.updatedAt,
      turns: s.turns.length,
      open: id === state.open,
    }));
}

export function story(id) {
  /* One story WHOLE — the one read the dossier makes. Three would let a
     pass land between two of them, which is the product's reason too. */
  const held = state.stories.get(id);
  if (!held) return null;
  const memory = state.lore.get(id) ?? { scenes: [], characters: [] };
  const copy = structuredClone(memory);
  const total = held.turns.length;
  const read = copy.scenes.reduce((far, scene) => {
    const end = Number(String(scene.span ?? "").split("-")[1] ?? 0);
    return end > far ? end : far;
  }, 0);
  return {
    id,
    label: label(held),
    title: held.title,
    updated_at: held.updatedAt,
    premise: held.system || "",
    messages: held.turns.map((t) => ({ ...t })),
    read_through: read,
    unread: Math.max(0, total - read),
    unread_span: total > read ? `${read + 1}-${total}` : "",
    scenes: copy.scenes,
    characters: copy.characters,
  };
}

/* The machine's gauge, which the picker re-reads every second. There is
   no machine here, so it drifts a little rather than standing still —
   a figure that never moves would say the poll was not running. */
export function memory() {
  const used = 23.4 + (Math.round(Date.now() / 1000) % 7) / 10;
  return { memory: `RAM: ${used.toFixed(1)} / 64.0 GB (${Math.round((100 * used) / 64)}%)` };
}

export function providers(scope = "") {
  // The product answers in two phases — the providers on this machine
  // now, the cloud catalogs after. The demo has no cloud half, and says
  // so with an empty second phase rather than doubled providers.
  if (scope === "cloud") {
    return { current: `${PROVIDER}/${state.model}`, memory: memory().memory, providers: [] };
  }
  const all = allProviders();
  if (scope && scope !== "local") {
    const named = all.providers.filter((provider) => provider.id === scope);
    // a name nothing is configured under answers 404, as the product does
    if (!named.length) return null;
    return { ...all, providers: named };
  }
  return all;
}

function allProviders() {
  return {
    current: `${PROVIDER}/${state.model}`,
    memory: memory().memory,
    providers: [
      {
        id: PROVIDER,
        label: "Demo",
        order: 8, // a hand-written section sorts after the eight supported providers, as in the product
        locality: "unknown", // the product cannot say where such a section runs
        connected: true,
        url: "in this browser tab",
        key_source: null,
        capabilities: { ...CAPABILITIES, supported_params: [...CAPABILITIES.supported_params] },
        models: MODELS.map((m) => ({ ...m, capabilities: { ...m.capabilities } })),
      },
      ...[
        ["generic", "Generic OpenAI provider", "", "unknown"],
        ["llamacpp", "llama.cpp", "http://localhost:8080/v1", "local"],
        ["koboldcpp", "KoboldCpp", "http://localhost:5001/v1", "local"],
        ["ollama", "Ollama", "http://localhost:11434/v1", "local"],
        ["omlx", "oMLX", "http://localhost:8100/v1", "local"],
        ["lmstudio", "LM Studio", "http://localhost:1234/v1", "local"],
        ["openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "remote"],
        ["nanogpt", "NanoGPT", "https://nano-gpt.com/api/v1", "remote"],
      ].map(([id, labelled, url, locality], order) => ({
        id,
        label: labelled,
        order,
        locality,
        connected: false,
        url,
        key_source: null,
        capabilities: null, // a provider that did not answer states nothing
        models: [],
      })),
    ],
  };
}

export function settings() {
  const s = state.settings;
  return {
    think: s.think,
    think_levels: s.think_levels,
    verbose: s.verbose,
    autocorrect: s.autocorrect,
    notification: s.notification,
    max_context: s.max_context ?? 65536,
    model: state.model,
    parameters: s.parameters.map((p) => ({ ...p })),
  };
}

export function context() {
  // The captured preview is the real assembler's work and is served as
  // long as it is true; a story that moved gets an honest recompute.
  const fixture = state.contextFixtures.get(state.open);
  if (fixture) return fixture;
  const story = state.stories.get(state.open);
  const bodies = story ? story.turns : [];
  const system = story ? story.system : "";
  const systemTokens = estimate(system);
  const transcript = bodies.reduce((n, t) => n + estimate(t.body), 0);
  const total = systemTokens + transcript;
  const used = Math.round((100 * total) / LIMIT);
  const lede =
    "Context preview — the exact request to be sent. Context summary:\n\n" +
    `  ~${total.toLocaleString("en-US")} tokens · ${used}% of the ${LIMIT.toLocaleString("en-US")} limit\n` +
    (systemTokens ? `  system ${systemTokens.toLocaleString("en-US")} · transcript ${transcript.toLocaleString("en-US")}\n` : "") +
    `  ${bodies.length} messages verbatim`;
  return {
    shape: {
      head: 0,
      tail: 0,
      middle: 0,
      kept: bodies.length,
      summaries: 0,
      history: false,
      rolled_up: 0,
      tail_target: 150,
      tail_setting: 150,
      total_tokens: total,
      system_tokens: systemTokens,
      transcript_tokens: transcript,
      limit: LIMIT,
      used,
    },
    lede,
    parts: [
      ...(system ? [{ role: "system", body: system }] : []),
      ...bodies.map((t) => ({ role: t.role, body: t.body })),
    ],
  };
}

export function usage(scope) {
  const scopes = [
    { key: "", label: "this story" },
    { key: "all", label: "all stories" },
  ];
  if (scope !== "" && scope !== "all") return { notice: "Usage: /usage [all]", refused: true, scopes };
  const rows = scope === "all" ? state.usage : state.usage.filter((r) => r.story === state.open);
  if (!rows.length) {
    return {
      notice: scope === "all" ? "No recorded usage yet." : "No recorded usage for this story.",
      refused: true,
      scopes,
    };
  }
  const prompt = rows.reduce((n, r) => n + r.prompt, 0);
  const completion = rows.reduce((n, r) => n + r.completion, 0);
  const seconds = rows.reduce((n, r) => n + r.seconds, 0);
  return {
    scope: scope === "all" ? "all stories" : "this story",
    scopes,
    rows: [
      {
        purpose: "chat",
        label: "Played turns", // `reports.USAGE_PURPOSES`, whose spelling this is
        provider: PROVIDER,
        model: state.model,
        requests: rows.length,
        prompt_tokens: prompt,
        completion_tokens: completion,
        cached_tokens: 0, // the demo model keeps no cache to read from
        rate: seconds > 0 ? completion / seconds : 0,
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

export function cast() {
  /* The open story's characters, shaped as the product's `/api/cast`
     answers — the composer's name menu reads it in the demo too. */
  const memory = state.lore.get(state.open);
  return {
    characters: (memory?.characters ?? []).map((person) => ({
      id: person.id,
      name: person.name,
      description: person.description ?? "",
    })),
  };
}

export function balance() {
  // Nobody has a key in a browser tab, so every account says the same
  // thing — and the total is null, as it is whenever nothing answered.
  const nothing = (provider, label) => ({
    provider,
    label,
    money: null,
    note: "no key set",
    value: "no key set",
  });
  // `?probe=none` changes nothing here: no key is ever probed.
  return {
    rows: [nothing("openrouter", "OpenRouter"), nothing("nanogpt", "NanoGPT")],
    total: null,
    note: `This story runs on ${PROVIDER}, which spends nothing. Paid providers are charged only when you switch to one.`,
  };
}

export function info(version) {
  const story = state.stories.get(state.open);
  const session = [["Messages", String(story ? story.turns.length : 0)]];
  if (story) session.unshift(["Story", cut(label(story), 50)]);
  // No premise row — the product's report stopped carrying one, and this
  // answers in the product's shape or it answers wrongly.
  const set = state.settings.parameters.filter((p) => p.value);
  if (set.length) session.push(["Parameters", set.map((p) => `${p.name} = ${p.value}`).join(", ")]);
  return {
    sections: [
      { rows: [["State dir", "(this browser tab — nothing leaves it)"]], note: "" },
      {
        rows: [
          ["Backend", "demo (a model that lives in the page)"],
          ["Model", state.model],
          // The model's own size; the product adds a "Served max context"
          // row only where the loaded instance's differs, and here it never does.
          ["Max context", modelRow().max_context_catalogue],
          // The product's capability rows, as the page's own model
          // honestly answers them: no effort reaches it, and it takes no
          // images and completes no raw text.
          ["Reasoning efforts", "not supported"],
          ["Capabilities", "none"],
        ],
        note: "",
      },
      { rows: session, note: `otaku ${version} — this is the scripted demo; ${INSTALL}.` },
    ],
  };
}

export function exportDocument(storyId) {
  const story = state.stories.get(storyId);
  if (!story || !story.turns.length) return { notice: "Nothing to export yet.", refused: true };
  const name = `${slug(label(story)) || "story"}.md`;
  const text = [
    `# ${label(story)}`,
    "",
    "*Played in the otaku demo — a real otaku exports the full document, memory included.*",
    "",
    ...story.turns.map((t) => (t.role === "user" ? `> ${t.body}` : t.body)),
  ].join("\n\n");
  return { name, text };
}

// ---------- the writes a screen performs ----------

export function land(storyId, messageId, action) {
  const story = state.stories.get(storyId);
  if (!story) return refuse("That message is not on the story's current chain.");
  if (messageId == null) {
    // A resume never used the pick, so it may come without one, as the
    // product's `api.stories.land` says — a story with nothing played
    // has none to give. The routes hand a null here for a resume alone.
    state.open = storyId;
    return say(landed("Resumed"));
  }
  const at = story.turns.findIndex((t) => t.id === messageId);
  if (at < 0) return refuse("That message is not on the story's current chain.");
  if (action === "fork") {
    const id = addStory(numberedTitle(story.title), story.system, story.turns.slice(0, at + 1));
    state.open = id;
    const named = cut(label(state.stories.get(id)), 50);
    const lead = named ? `Forked to: ${named}. ` : "Forked. ";
    return { notice: `${lead}Continued from message ${at + 1}.`, story: id };
  }
  if (action === "truncate") {
    story.turns = story.turns.slice(0, at + 1);
    touch(storyId);
    state.open = storyId;
    return say(landed("Truncated"));
  }
  state.open = storyId;
  return say(landed("Resumed"));
}

export function setSystem(storyId, text) {
  // The story the PATH named, which is not always the open one: the
  // dossier edits any story from the outside.
  const story = state.stories.get(storyId);
  if (!text) {
    const current = story && story.system;
    return say(current ? `System: "${current}"` : "System: (none)");
  }
  if (story) {
    story.system = text;
    touch(storyId);
  }
  return say(`System prompt set (${text.length} chars).`);
}

export function renameStory(storyId, title) {
  if (!title.trim()) return refuse("A story needs a title — or leave the one it has.");
  const story = state.stories.get(storyId);
  if (story) {
    story.title = title.trim();
    touch(storyId);
  }
  return say(`Story title set to "${title.trim()}".`);
}

export function deleteStory(storyId) {
  // a row already gone answers 404, as the product does
  if (!state.stories.has(storyId)) return null;
  state.stories.delete(storyId);
  state.lore.delete(storyId);
  if (state.open === storyId) state.open = null;
  return say("Story deleted.");
}

export function editMessage(messageId, body) {
  if (!body.trim()) return refuse("A message cannot be emptied — undo the exchange instead.");
  for (const [id, story] of state.stories) {
    const turn = story.turns.find((t) => t.id === messageId);
    if (turn) {
      turn.body = body;
      touch(id);
      moved(id);
      return say("Message edited.");
    }
  }
  return say("Message edited.");
}

export function editLore(storyId, kind, target, text) {
  /* One corrected row, addressed the way the product addresses it: the
     PATH said which story and which row, the body said which of its
     fields, and all three arrive here. The row must be that story's
     own — the product checks the address and refuses first (sentence
     copied from backend.api.lore.edit). */
  const memory = state.lore.get(storyId);
  if (!memory) return refuse("No story yet — send a message first.");
  const rows =
    kind === "entry"
      ? memory.scenes.flatMap((scene) => scene.journals)
      : kind.startsWith("scene-")
        ? memory.scenes
        : memory.characters;
  if (!rows.some((row) => row.id === target)) {
    const noun = kind === "entry" ? "journal" : kind.startsWith("scene-") ? "scene" : "character";
    return refuse(`That ${noun} is not in this story's memory.`);
  }
  if (kind === "scene-summary" && !text.trim()) {
    return refuse("An emptied summary would swallow its scene — not saved.");
  }
  for (const scene of memory.scenes) {
    if (kind === "scene-title" && scene.id === target) scene.title = text;
    if (kind === "scene-summary" && scene.id === target) scene.summary = text;
  }
  for (const person of memory.characters) {
    if (kind === "description" && person.id === target) person.description = text;
    if (kind === "card" && person.id === target) person.card = text;
  }
  // A journal record is one thing read from two sides, so both sides
  // carry it and both must be corrected.
  for (const group of [memory.scenes, memory.characters]) {
    for (const item of group) {
      for (const record of item.journals) {
        if (record.id === target) record[kind] = text;
      }
    }
  }
  return say("Saved.");
}

export function switchModel(provider, model) {
  if (provider !== PROVIDER) return refuse(`Unknown provider '${provider}'.`);
  if (model === state.model) return refuse(`Already using ${PROVIDER}/${state.model}.`);
  if (!MODELS.some((m) => m.name === model)) {
    return say(`Switched to ${PROVIDER}/${model}.`); // the real backend trusts the name too
  }
  state.model = model;
  return say(`Switched to ${PROVIDER}/${state.model}.`);
}

export function loadModel(model, wanted) {
  const row = MODELS.find((m) => m.name === model);
  if (!row || !CAPABILITIES.model_management) return refuse(`${PROVIDER} cannot load or unload models.`);
  row.loaded = wanted;
  return say(wanted ? `Loaded ${model}.` : `Unloaded ${model}.`);
}

export function recordHistory(line) {
  const said = line.trim();
  if (!said || state.history[0] === said) return say("");
  state.history.unshift(said);
  state.history.length = Math.min(state.history.length, 100);
  return say("");
}

// ---------- the story-level writes ----------

export function newStory(title) {
  const id = addStory(title.trim(), "", []);
  state.open = id;
  // The id it made, as the product's `Created` carries it: a page with
  // no story addresses this one next.
  const said = title.trim()
    ? say(`Started a new story: "${cut(title.trim(), 50)}".`)
    : say("Started a new story.");
  return { ...said, story: id };
}

export function forkStory(storyId, title) {
  // The story the PATH named — a copy made from a browser row must not
  // silently copy whatever happens to be open instead.
  const story = state.stories.get(storyId);
  if (!story || !story.turns.length) return refuse("Nothing to fork yet — send a message first.");
  const id = addStory(title.trim() || numberedTitle(story.title), story.system, story.turns);
  state.open = id;
  const named = cut(label(state.stories.get(id)), 50);
  return { notice: named ? `Forked to: ${named}.` : "Forked.", story: id };
}

export function undo() {
  const story = state.stories.get(state.open);
  const popped = [];
  if (story && story.turns.at(-1)?.role === "assistant") popped.push(story.turns.pop());
  if (story && story.turns.at(-1)?.role === "user") popped.push(story.turns.pop());
  if (!popped.length) return refuse("Nothing to undo.");
  touch(state.open);
  moved(state.open);
  return say(`Took back the last exchange (${popped.length} messages).`);
}

export function setKnob(name, value) {
  /* One session-wide knob, dispatched by name — the shape the server
     answers `PUT /api/settings/{setting}` with, down to a raw string
     each setter parses for itself. A knob the demo has never heard of
     is refused here rather than silently kept. */
  const raw = value === true ? "on" : value === false ? "off" : String(value);
  const setter = _KNOBS[name];
  if (!setter) return refuse(`Unknown setting '${name}'.`);
  return setter(raw);
}

// The product's bounds (`backend.session.PARAMETERS`): [low, high],
// high null for no ceiling; a name absent takes any value of its type.
const _RANGES = {
  temperature: [0, 2],
  top_p: [0, 1],
  top_k: [0, null],
  min_p: [0, 1],
  max_tokens: [1, null],
  presence_penalty: [-2, 2],
  frequency_penalty: [-2, 2],
  repetition_penalty: [0, 2],
};

export function setParameter(name, value) {
  // One per-model parameter; "reset" puts it back to the model's own
  // default, which is what a DELETE on it means.
  const row = state.settings.parameters.find((p) => p.name === name);
  if (!row) return refuse(`Unknown parameter '${name}'.`);
  const text = String(value).trim();
  if (text.toLowerCase() === "reset") {
    if (!row.value) return say(`Parameter ${name} is already at its default.`);
    row.value = "";
    return say(`Parameter ${name} reset to default.`);
  }
  if (row.type === "float" && Number.isNaN(Number.parseFloat(text))) {
    return refuse(`Could not parse '${text}' as float.`);
  }
  if (row.type === "int" && !/^-?\d+$/.test(text)) return refuse(`Could not parse '${text}' as int.`);
  const range = _RANGES[name];
  if (range) {
    const [low, high] = range;
    const number = Number(text);
    if (number < low || (high !== null && number > high)) {
      return refuse(`${name} must be ${high === null ? `at least ${low}` : `between ${low} and ${high}`}.`);
    }
  }
  row.value = text;
  return say(`${name} = ${text}.`);
}

const _KNOBS = {
  think: (raw) => {
    const s = state.settings;
    const value = raw.trim().toLowerCase();
    if (value === "unset") {
      s.think = "unset";
      return say("Think: unset.");
    }
    if (!s.think_levels.includes(value)) {
      return refuse(`Usage: /set think ${s.think_levels.join("|")}`);
    }
    s.think = value;
    return say(`Think: ${value}.`);
  },
  verbose: (raw) => _toggle("verbose", "Verbose", raw),
  autocorrect: (raw) => _toggle("autocorrect", "Autocorrect", raw),
  notification: (raw) => _toggle("notification", "Notification", raw),
  max_context: (raw) => {
    const value = raw.trim().toLowerCase();
    if (["off", "none", "0"].includes(value)) {
      state.settings.max_context = 0;
      return say("Max context: 0 (the model's own max context).");
    }
    if (!/^\d+$/.test(value)) return refuse("Usage: /set max_context TOKENS|off");
    state.settings.max_context = Number.parseInt(value, 10);
    return say(`Max context: ${state.settings.max_context} tokens.`);
  },
};

function _toggle(name, label, raw) {
  const value = raw.trim().toLowerCase();
  if (["on", "true", "yes"].includes(value)) state.settings[name] = true;
  else if (["off", "false", "no"].includes(value)) state.settings[name] = false;
  else return refuse(`Usage: /set ${name} on|off`);
  return say(`${label}: ${state.settings[name] ? "on" : "off"}.`);
}

// ---------- what the reply stream needs ----------

export function recordTurn(role, body) {
  let story = state.stories.get(state.open);
  if (!story) {
    // The first real turn creates the story, exactly as the backend does.
    const id = addStory("", "", []);
    state.open = id;
    story = state.stories.get(id);
  }
  const turn = {
    id: state.nextMessage++,
    role,
    body,
    kind: "dialogue",
    // Absent is NULL here as it is in `web.api._turn`: a speaker the
    // extractor has not named yet is a state the reader pane draws.
    speaker: null,
    provider: role === "assistant" ? PROVIDER : null,
    model: role === "assistant" ? state.model : null,
    template: null,
  };
  story.turns.push(turn);
  touch(state.open);
  moved(state.open);
  return { ...turn };
}

export function importCard(landed) {
  /* The three writes `backend.api.cards.add` makes, on this store: the
     typed /card row (speaker-linked), the cast row with the card
     archive, and the greeting as the character's own first words —
     provider "card", the file for a model, exactly as the product
     spells authored rows. `persona` is remembered per story, the way
     the real import reads it back from the archive. */
  let story = state.stories.get(state.open);
  if (!story) {
    const id = addStory("", "", []);
    state.open = id;
    story = state.stories.get(id);
  }
  story.turns.push({
    id: state.nextMessage++,
    role: "user",
    body: landed.line,
    kind: "card",
    speaker: landed.name,
    provider: null,
    model: null,
    template: null,
  });
  const memory = memoryOf(state.open);
  const characterId = memory.characters.reduce((top, c) => Math.max(top, c.id), 0) + 1;
  memory.characters.push({
    id: characterId,
    name: landed.name,
    aliases: [],
    description: landed.description,
    card: landed.card,
    history: "",
    updated_at: new Date().toISOString(),
    journals: [],
  });
  story.persona = landed.persona;
  if (landed.greeting) {
    story.turns.push({
      id: state.nextMessage++,
      role: "assistant",
      body: landed.greeting,
      kind: "dialogue",
      speaker: landed.name,
      provider: "card",
      model: landed.fileName,
      template: null,
    });
  }
  touch(state.open);
  moved(state.open);
}

export function personaOf() {
  // The story's memory of who the player is — asked once, then the
  // default every later import offers (`api.cards.remembered_persona`).
  return state.stories.get(state.open)?.persona ?? "";
}

export function findCharacter(name) {
  const memory = state.lore.get(state.open);
  return (memory?.characters ?? []).find((person) => person.name === name) ?? null;
}

export function dropLastReply() {
  const story = state.stories.get(state.open);
  if (!story || story.turns.at(-1)?.role !== "assistant") return null;
  const popped = story.turns.pop();
  touch(state.open);
  moved(state.open);
  return popped;
}

export function hasTurns() {
  const story = state.stories.get(state.open);
  return Boolean(story && story.turns.length);
}

export function openTurns() {
  const story = state.stories.get(state.open);
  return story ? story.turns : [];
}

export function verbose() {
  return state.settings.verbose;
}

export function recordUsage(prompt, completion, seconds) {
  state.usage.push({ story: state.open, prompt, completion, seconds });
}

export function contextTokens() {
  return context().shape.total_tokens;
}

// ---------- the lore pass ----------

export function memoryOf(storyId) {
  if (!state.lore.has(storyId)) state.lore.set(storyId, { scenes: [], characters: [] });
  return state.lore.get(storyId);
}

export function openId() {
  return state.open;
}

export function setStatus(line) {
  state.status = line;
}

export function status() {
  return state.status;
}

// ---------- internals ----------

function addStory(title, system, turns) {
  const id = state.nextStory++;
  state.stories.set(id, {
    title,
    system,
    turns: turns.map((t) => ({ ...t })),
    updatedAt: new Date().toISOString(),
  });
  return id;
}

export function importStory(paragraphs) {
  const turnsIn = paragraphs.map((body, i) => ({
    id: state.nextMessage++,
    role: i % 2 ? "assistant" : "user",
    body,
  }));
  const id = addStory("", "", []);
  state.stories.get(id).turns = turnsIn;
  state.open = id;
  moved(id);
  return { id, count: turnsIn.length };
}

function landed(verb) {
  const story = state.stories.get(state.open);
  const named = story ? cut(label(story), 50) : "";
  const head = named ? `Story: ${named}. ` : "";
  const count = story ? story.turns.length : 0;
  return count ? `${head}${verb} at message ${count}.` : `${head}${verb}, nothing played yet.`;
}

function numberedTitle(title) {
  return title ? `${title} - 2` : "";
}

function label(story) {
  if (story.title) return flatten(story.title);
  const id = [...state.stories.entries()].find(([, s]) => s === story)?.[0];
  const arc = id ? storySoFar(id) : "";
  if (arc) return flatten(arc);
  return flatten(firstUser(story));
}

function storySoFar(id) {
  // The arc a listing row shows: the newest scene's own rollup, and the
  // newest summary where no rollup was composed here.
  const scenes = state.lore.get(id)?.scenes ?? [];
  return scenes.at(-1)?.history || scenes.at(-1)?.summary || "";
}

function firstUser(story) {
  return story.turns.find((t) => t.role === "user")?.body || "";
}

function touch(id) {
  const story = state.stories.get(id);
  if (story) story.updatedAt = new Date().toISOString();
}

function moved(id) {
  // The captured context preview stops being true the moment its story
  // moves; the recompute takes over for that story alone.
  state.contextFixtures.delete(id);
}

function modelRow() {
  return MODELS.find((m) => m.name === state.model) ?? MODELS[0];
}

function estimate(text) {
  return Math.ceil(text.length / 4);
}

function flatten(text) {
  return text.replace(/\s+/g, " ").trim();
}

function cut(text, limit) {
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function slug(text) {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/[\s_-]+/g, "-")
    .replace(/^-|-$/g, "");
}

function say(notice) {
  return { notice };
}

function refuse(notice) {
  return { notice, refused: true };
}

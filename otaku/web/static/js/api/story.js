/* A STORY as the page reaches it — the twin of `web.api.story`, a section
   for each of its modules under the module's name, in the mirror's order.
   A section holds its resource's verbs; the facade (`index.js`) hangs each
   on its call path, every resource inside a story under `stories`. */

import { get, items, patch, post, query, remove, segment, stream } from "./_http.js";

// ---------- stories ----------

/* The stories: the collection, one story, and the actions on one — a copy
   (`forks`), a fresh take on its last reply (`regens`, a stream), its
   trailing exchange taken back (`undos`). A write that moves the head — a
   head given, an undo, a fresh take — names the one this page last read
   (`If-Match`). */
export const stories = {
  list: ({ query: needle = "" } = {}) => get(`/api/stories${query({ query: needle })}`).then(items),
  get: (story) => get(`/api/stories/${story}`),
  create: (fields = {}) => post("/api/stories", fields),
  update: (story, fields) =>
    patch(`/api/stories/${story}`, fields, { moves: "head" in fields ? story : null }),
  delete: (story) => remove(`/api/stories/${story}`),
  forks: { create: (story, fields = {}) => post(`/api/stories/${story}/forks`, fields) },
  regens: {
    create: (story, { signal } = {}) => stream(`/api/stories/${story}/regens`, {}, { story, signal }),
  },
  undos: { create: (story) => post(`/api/stories/${story}/undos`, {}, { moves: story }) },
};

// ---------- settings ----------

/* A story's settings: each switch and value by the name the story stores
   it under, the fields given laid over it. */
const setting = (story, name) => `/api/stories/${story}/settings/${segment(name)}`;

export const settings = {
  list: (story) => get(`/api/stories/${story}/settings`).then(items),
  get: (story, name) => get(setting(story, name)),
  update: (story, name, fields) => patch(setting(story, name), fields),
};

// ---------- messages ----------

/* A story's messages: the chain — its `last` messages alone where a count
   is given — one message and its correction, and a line played
   (`{line, files}`, the pictures as `encodeFile` makes them): a stream,
   from the head this page last read. */
export const messages = {
  list: (story, { last = null } = {}) =>
    get(`/api/stories/${story}/messages${query({ last })}`).then(items),
  get: (story, message) => get(`/api/stories/${story}/messages/${message}`),
  update: (story, message, fields) => patch(`/api/stories/${story}/messages/${message}`, fields),
  create: (story, fields, { signal } = {}) =>
    stream(`/api/stories/${story}/messages`, fields, { story, signal }),
};

// ---------- lore ----------

/* A story's memory: loaded whole, its scenes, characters and journal
   records each read and corrected, a character folded into another
   (`merges`) or deleted, and the forced extraction pass — started (202:
   polled, not streamed), read until done, given up. */
const inside = (story, rest) => `/api/stories/${story}/${rest}`;

export const lore = { get: (story) => get(inside(story, "lore")) };

export const scenes = {
  list: (story) => get(inside(story, "scenes")).then(items),
  get: (story, scene) => get(inside(story, `scenes/${scene}`)),
  update: (story, scene, fields) => patch(inside(story, `scenes/${scene}`), fields),
};

export const characters = {
  list: (story, { name = null } = {}) =>
    get(inside(story, `characters${query({ name })}`)).then(items),
  get: (story, character) => get(inside(story, `characters/${character}`)),
  update: (story, character, fields) => patch(inside(story, `characters/${character}`), fields),
  delete: (story, character) => remove(inside(story, `characters/${character}`)),
  merges: {
    create: (story, character, fields) =>
      post(inside(story, `characters/${character}/merges`), fields),
  },
};

export const journals = {
  list: (story) => get(inside(story, "journals")).then(items),
  get: (story, journal) => get(inside(story, `journals/${journal}`)),
  update: (story, journal, fields) => patch(inside(story, `journals/${journal}`), fields),
};

export const extraction = {
  create: (story) => post(inside(story, "extraction")),
  get: (story) => get(inside(story, "extraction")),
  delete: (story) => remove(inside(story, "extraction")),
};

// ---------- cards ----------

/* Character cards brought into a story, always through the codex: a card's
   file (`{data, file_name, rename, reuse}`, the bytes as base64) or a codex
   card (`{card_id, rename}`), vetted for the story and nothing stored —
   `validateOnly` — or brought in with the `persona` it speaks to and, where
   `with_pictures` asks, its portrait, played on from the head this page last
   read. */
export const cards = {
  create: (story, fields, { validateOnly = false } = {}) =>
    validateOnly
      ? post(`/api/stories/${story}/cards?validate_only=true`, fields)
      : post(`/api/stories/${story}/cards`, fields, { moves: story }),
};

// ---------- context ----------

/* A story's next request: previewed whole, and how full it is (`fill`),
   read again with every landing. */
export const context = {
  get: (story) => get(`/api/stories/${story}/context`),
  fill: { get: (story) => get(`/api/stories/${story}/context/fill`) },
};

// ---------- transfer ----------

/* Stories crossing otaku's edge: a document landed as a story (`imports`:
   its `text` and the `file_name` it was picked as), and a story exported
   as one document (`storyExport`, the stories' `export`). */
export const imports = { create: (fields) => post("/api/imports", fields) };

export const storyExport = { get: (story) => get(`/api/stories/${story}/export`) };

// ---------- prompts ----------

/* The texts a story's settings send, shared by every story: each tool's
   prompt, by the tool's name (`StorySetting.tool`); each prompt no tool
   owns, by its prompts.toml key (`StorySetting.prompt_name`) — "" or a
   delete restores the built-in — and the shared reminder, one text for
   every story that switched it on; "" clears it. */
export const tools = {
  prompt: {
    get: (tool) => get(`/api/tools/${segment(tool)}/prompt`),
    update: (tool, fields) => patch(`/api/tools/${segment(tool)}/prompt`, fields),
    delete: (tool) => remove(`/api/tools/${segment(tool)}/prompt`),
  },
};

export const prompts = {
  get: (name) => get(`/api/prompts/${segment(name)}`),
  update: (name, fields) => patch(`/api/prompts/${segment(name)}`, fields),
  delete: (name) => remove(`/api/prompts/${segment(name)}`),
};

export const sharedReminder = {
  get: () => get("/api/shared-reminder"),
  update: (fields) => patch("/api/shared-reminder", fields),
};

// ---------- language ----------

/* The story's typed language: the openers a line may start with and the
   inliners it may carry, tokens and argument shapes, and what a line with
   none of them does. */
export const language = { get: () => get("/api/language") };

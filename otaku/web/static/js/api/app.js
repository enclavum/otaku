/* The installation as the page reaches it — the twin of `web.api.app`, a
   section for each of its modules under the module's name, in the
   mirror's order. A section holds its resource's verbs; the facade
   (`index.js`) hangs each on its call path. */

import { get, items, patch, post, query, segment } from "./_http.js";

// ---------- core ----------

/* The app itself — its version, what its launch had to say, the model in
   use and the one each role asks — switched or pinned by an update
   (`models` keyed by role — `writing`, the model played with; `extraction`;
   `dictation` — each `{provider, name}`, a role's null unpinning it,
   extraction following the writing one again); what the background worker is doing
   (`status`), answered by the server itself; and a recording written down
   by the model saved for dictation (`{audio, audio_format}`, the bytes as
   base64) — or, with `validateOnly`, what would write it down and the
   recordings it takes. */
export const app = {
  get: () => get("/api/app"),
  update: (fields) => patch("/api/app", fields),
};

export const status = { get: () => get("/api/app/status") };

export const dictation = {
  create: (fields = {}, { validateOnly = false } = {}) =>
    post(validateOnly ? "/api/app/dictation?validate_only=true" : "/api/app/dictation", fields),
};

// ---------- machine ----------

/* The machine a model fills: its memory, read again while a model loads. */
export const machine = { get: () => get("/api/app/machine") };

// ---------- settings ----------

/* The installation's settings: the story a launch resumes (`last_story`),
   the switches, the context limit. */
export const settings = {
  get: () => get("/api/settings"),
  update: (fields) => patch("/api/settings", fields),
};

// ---------- providers ----------

/* The providers, as the picker lists them: a slice at a time — `scope` ""
   every provider, "local" all but the hosted catalogs, "cloud" the
   catalogs and the generic provider, a provider's name that one alone (a
   Test connection), and `probe: false` asking nobody — and a provider's
   own section, its url and key saved or forgotten. */
export const providers = {
  list: ({ scope = "", probe = true } = {}) =>
    get(`/api/providers${query({ scope, probe: probe ? "" : "false" })}`),
  get: (provider) => get(`/api/providers/${segment(provider)}`),
  update: (provider, fields) => patch(`/api/providers/${segment(provider)}`, fields),
};

// ---------- models ----------

/* A provider's models: the models it serves, one loaded or unloaded
   (`loaded`), and a model's own settings — its thinking level and its
   parameters, a parameter's null unsetting it. */
const model = (provider, name) => `/api/providers/${segment(provider)}/models/${segment(name)}`;

export const models = {
  list: (provider) => get(`/api/providers/${segment(provider)}/models`).then(items),
  update: (provider, name, fields) => patch(model(provider, name), fields),
  settings: {
    get: (provider, name) => get(`${model(provider, name)}/settings`),
    update: (provider, name, fields) => patch(`${model(provider, name)}/settings`, fields),
  },
};

// ---------- integrations ----------

/* The integrations: the sites a card is fetched from by a link; web
   search — whether it holds a key, and its key saved ("" forgets it) — and
   a support request sent to otaku's author. */
export const integrations = {
  cards: {
    list: () => get("/api/integrations/cards").then(items),
  },
  webSearch: {
    get: () => get("/api/integrations/web-search"),
    update: (fields) => patch("/api/integrations/web-search", fields),
  },
  supportRequests: {
    create: (fields) => post("/api/integrations/support-requests", fields),
  },
};

// ---------- history ----------

/* The lines submitted before, as the composer walks them: most recent
   first, and one more kept. */
export const history = {
  list: () => get("/api/history").then(items),
  create: (fields) => post("/api/history", fields),
};

// ---------- pictures ----------

/* The stored pictures, a message's and a card's alike: where each is
   served, and its thumbnail, by the name its row carries — an address, not
   a request: an `img` asks for it, and the answer is immutable. */
export const pictures = {
  url: (name) => `/api/pictures/${segment(name)}`,
  thumbnail: { url: (name) => `/api/pictures/${segment(name)}/thumbnail` },
};

// ---------- reports ----------

/* The installation's reports, the page drawing them from their facts: what
   the tokens were spent on — on the story named, on every story where none
   is — what otaku knows of this run, and what each cloud account has left,
   `probe: false` the roster alone, asking no network. */
export const usage = { get: ({ story = null } = {}) => get(`/api/usage${query({ story })}`) };

export const info = { get: ({ story = null } = {}) => get(`/api/info${query({ story })}`) };

export const balance = {
  get: ({ probe = true } = {}) => get(`/api/balance${query({ probe: probe ? "" : "false" })}`),
};

/* THE CODEX as the page reaches it — the twin of `web.api.codex`, a section
   for each of its modules under the module's name, in the mirror's order.
   A section holds its resource's verbs; the facade (`index.js`) hangs each
   on its call path. */

import { get, items, patch, post, query, remove } from "./_http.js";

// ---------- cards ----------

/* The codex's character cards: the list (`query` finds a name, an alias or
   a tagline) and one card whole; a card made — `{name, …its fields}` by
   hand, `{data, file_name, rename, reuse}` from a card file (the bytes as
   base64; vetted alone with `validateOnly`, nothing stored), `{story_id,
   character_id}` from a story's character — its parts corrected
   (`pictures` each of its own by name, in their new order), a card
   deleted; a picture added (`{data, name}`) or removed by its file name,
   a card folded `into` another, and every story's character no card
   claims (`unlinked`). */
export const cards = {
  list: ({ query: needle = "" } = {}) => get(`/api/cards${query({ query: needle })}`).then(items),
  get: (card) => get(`/api/cards/${card}`),
  create: (fields, { validateOnly = false } = {}) =>
    post(validateOnly ? "/api/cards?validate_only=true" : "/api/cards", fields),
  update: (card, fields) => patch(`/api/cards/${card}`, fields),
  delete: (card) => remove(`/api/cards/${card}`),
  pictures: {
    create: (card, fields) => post(`/api/cards/${card}/pictures`, fields),
    delete: (card, name) => remove(`/api/cards/${card}/pictures/${encodeURIComponent(name)}`),
  },
  merges: { create: (card, fields) => post(`/api/cards/${card}/merges`, fields) },
  unlinked: {
    list: ({ query: needle = "" } = {}) =>
      get(`/api/cards/unlinked${query({ query: needle })}`).then(items),
  },
};

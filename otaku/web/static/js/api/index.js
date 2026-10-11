/* THE PAGE'S DOOR TO OTAKU — its client of `web.api`, laid out as the
   interface is: its three packages of twins as three modules, `app.js`,
   `codex.js` and `story.js`, a section in each for every twin under the
   twin's name, and the HTTP itself beside them, this layer's machinery
   (`_http.js`).

   The facade, `api`: a resource per top-level path, a sub-resource an
   attribute of its parent — the interface's own call paths
   (`backend.api`), so a call spells the route it asks:
   `api.stories.messages.create(5, …)` is POST /api/stories/5/messages.
   Its methods are the interface's verbs, the ids first in path order,
   the fields of a body as the wire spells them. A section holds its own
   verbs alone; this facade is where each is hung on its call path.
   Re-exported beside it: what a screen needs of the machinery — the reach
   hooks and the sign-in. */

import * as app from "./app.js";
import * as story from "./story.js";

export { login, whenLost, whenReached, whenUnauthorized } from "./_http.js";

export const api = {
  app: { ...app.app, machine: app.machine, status: app.status },
  settings: app.settings,
  tools: story.tools,
  prompts: story.prompts,
  sharedReminder: story.sharedReminder,
  providers: { ...app.providers, models: app.models },
  stories: {
    ...story.stories,
    settings: story.settings,
    messages: story.messages,
    lore: story.lore,
    scenes: story.scenes,
    characters: story.characters,
    journals: story.journals,
    extraction: story.extraction,
    cards: story.cards,
    export: story.storyExport,
    context: story.context,
  },
  imports: story.imports,
  usage: app.usage,
  info: app.info,
  balance: app.balance,
  pictures: app.pictures,
  history: app.history,
  integrations: app.integrations,
  language: story.language,
};

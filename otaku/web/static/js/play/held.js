/* The story this page holds — the one it plays and every screen acts on:
   the one a launch resumes (`last_story`), until the reader lands in
   another, and every landing writes it back, so a reload, another tab
   and the terminal resume it too. Held here, below every screen that
   reads it, and drawn by none.

   A reader with no story yet has a story made by the first write that
   needs one — a line played, a card brought in — and taken back where
   that write is refused or given up (`writing`, `makeFor`), so none is
   left behind empty: the terminal's `Chat.writing`, the same rule. */

import { api } from "../api/index.js";

let held = null;

/** The story this page plays; null while there is none. */
export const story = () => held;

/** What a launch resumes — read at boot while this page holds no story:
    one it holds is kept across otaku going away and coming back. */
export async function resume() {
  if (held === null) ({ last_story: held } = await api.settings.get());
  return held;
}

/** Land in `id` — this page plays it from now on, and so does the next
    launch; null holds none. The pointer is written as the backend's own
    is, best effort, and never waited on: a write queues behind a reply
    still streaming, and a first line's reply is read meanwhile. */
export function hold(id) {
  held = id;
  if (id !== null) api.settings.update({ last_story: id }).catch(() => {});
}

/** A write that needs a story: `write(id)` on the story held, or on one
    made for it — taken back where the write is refused or throws. A held
    story deleted elsewhere (the terminal, another tab) is let go, and the
    write goes into one made for it, as a reader with none has. */
export async function writing(write) {
  if (held !== null) {
    const answer = await write(held);
    if (!(answer?.refused && answer.status === 404 && (await lost(held)))) return answer;
  }
  const made = await makeFor();
  if (made.refused) return made;
  let answer;
  try {
    answer = await write(made.id);
  } catch (e) {
    await takeBack(made.id);
    throw e;
  }
  if (answer?.refused) await takeBack(made.id);
  else hold(made.id);
  return answer;
}

/** A story made for a write that needs one, held at once — `{id}`, or
    the refusal. The caller lands it (`hold`) once the write is in, or
    takes it back (`takeBack`) when it is refused or abandoned. */
export async function makeFor() {
  const made = await api.stories.create();
  if (made.refused) return made;
  held = made.value.id;
  return { id: held };
}

/** A story `makeFor` made, deleted again: its write never landed. */
export async function takeBack(id) {
  await api.stories.delete(id).catch(() => {});
  if (held === id) held = null;
}

/** Whether story `id` is gone — deleted elsewhere, the terminal or
    another tab — and so let go where this page held it. */
export async function lost(id) {
  const found = await api.stories.get(id);
  if (!(found.refused === true && found.status === 404)) return false;
  if (held === id) held = null;
  return true;
}

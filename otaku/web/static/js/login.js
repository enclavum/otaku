/* The one screen that stands in FRONT of the page rather than over it.

   Everything else here is a door the reader stepped through and can step
   back out of. This one is not: behind it is a shell that cannot answer
   anything yet, so it carries no Cancel, neither Esc nor the scrim closes
   it (`data-fixed`), and its backdrop hides the page instead of dimming it.

   A refusal is said INSIDE the dialog for the same reason — the status
   line is behind it, where nobody would read it.

   The rail's Sign out row is the other half, drawn only when this otaku
   asks for a password at all. */

import * as api from "./api.js";
import { $, $$ } from "./dom.js";

let pending = null;

/** Signed in, however many asked. The first caller opens the dialog and
    every later one — a boot's four reads, a heartbeat — waits on that
    same sign-in instead of stacking a dialog of its own. */
export function signIn() {
  pending ??= prompt().finally(() => {
    pending = null;
  });
  return pending;
}

/** The cookie taken away, then the page started over — which finds a
    password asked for and this browser not through it, and asks. A
    sign-out that did not reach the server leaves everything as it was:
    the cookie is still there, so the button still means something. */
export async function signOut() {
  try {
    await api.signOut();
  } catch {
    return;
  }
  location.reload();
}

/** Whether the rail offers a way out. Only where a password is asked
    for: an otaku with none has no session to leave, and says nothing
    about one. */
export function showSignOut(on) {
  for (const part of $$("[data-sign-out], [data-signed-in]")) part.hidden = !on;
}

function prompt() {
  const dialog = $('dialog[data-dialog="login"]');
  const field = $("#otk-login-password", dialog);
  const remember = $("[data-remember]", dialog);
  const label = $("[data-remember-label]", dialog);
  const button = $('[data-choice="sign-in"]', dialog);
  refused(dialog, "");
  field.value = "";
  // The label says how long the sign-in lasts, whichever way the box is.
  const describe = () => {
    label.textContent = remember.checked ? label.dataset.on : label.dataset.off;
  };
  describe();
  // Continue waits for a password, and for the answer to the last one.
  let asking = false;
  const ready = () => {
    button.disabled = asking || !field.value;
  };
  ready();

  return new Promise((resolve) => {
    const wired = new AbortController();
    const attempt = async () => {
      if (button.disabled) return;
      asking = true;
      ready();
      try {
        const answer = await api.signIn(field.value, remember.checked);
        if (answer.refused) {
          // Said here, the field emptied for the next try; the tick is
          // the reader's and stays as they left it.
          refused(dialog, answer.notice);
          field.value = "";
          field.focus();
          return;
        }
        wired.abort();
        dialog.close();
        resolve();
      } catch {
        // No answer at all: the page's offline state is `api.whenLost`'s
        // to draw, and the dialog stays up for when otaku is back.
      } finally {
        asking = false;
        ready();
      }
    };
    button.addEventListener("click", attempt, { signal: wired.signal });
    field.addEventListener("input", ready, { signal: wired.signal });
    // Ticking the box, or its label, sends the caret back to the password:
    // the password is still what the dialog is waiting for.
    remember.addEventListener(
      "change",
      () => {
        describe();
        field.focus();
      },
      { signal: wired.signal },
    );
    dialog.addEventListener(
      "keydown",
      (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        attempt();
      },
      { signal: wired.signal },
    );
    if (!dialog.open) dialog.showModal();
  });
}

/** The refusal, under the field: the backend's own sentence, and
    nothing of this page's beside it. */
function refused(dialog, notice) {
  const line = $("[data-error]", dialog);
  line.hidden = !notice;
  line.textContent = notice;
}

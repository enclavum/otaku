/* The support docket: a message to otaku's author, and the address to
   answer at where the reader gives one, sent through the site
   (`backend.api.support`) — nothing of the story attached. A refusal (no
   message, an address that is none, a send that failed) is the backend's
   sentence in the footer, the docket left open with what was typed. A
   request sent stays on screen, the confirmation in the footer and Send
   become Close; opened again, the docket starts a new one. The address
   is kept for it. */

import * as api from "./api.js";
import { footnote, popups, wiring } from "./browser.js";
import { $ } from "./dom.js";

export function openSupport() {
  const popup = popups.get("/support");
  const signal = wiring(popup);
  const email = $("[data-support-email]", popup);
  const message = $("[data-support-message]", popup);
  const cancel = $(".otk-close", popup);
  const send = $("[data-send]", popup);
  // a request sent last time is done; one cancelled waits as it was typed
  if (popup.dataset.sent !== undefined) {
    message.value = "";
    delete popup.dataset.sent;
  }
  send.textContent = "Send";
  cancel.style.visibility = "";
  footnote(popup, "");
  send.addEventListener(
    "click",
    async () => {
      if (popup.dataset.sent !== undefined) {
        popup.close();
        return;
      }
      send.disabled = true;
      try {
        const answer = await api.sendSupport(message.value, email.value);
        footnote(popup, answer.notice);
        if (answer.refused) return;
        popup.dataset.sent = "";
        send.textContent = "Close";
        // out of sight but not of the row: Close stands where Send stood
        cancel.style.visibility = "hidden";
      } catch (e) {
        // a fault is a bug, not an answer: the console gets it, the reader a sentence
        console.error(e);
        footnote(popup, String(e.message ?? e));
      } finally {
        send.disabled = false;
      }
    },
    { signal },
  );
  if (!popup.open) popup.showModal();
  // the body holds the focus (`autofocus`), so a phone's keyboard waits for
  // a tap; with a mouse, the caret is put in the first field at once
  if (!window.matchMedia("(pointer: coarse)").matches) email.focus();
}

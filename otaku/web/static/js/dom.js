/* Making and finding elements. Nothing here knows what otaku is. */

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

export function span(className, text) {
  return element("span", className, text);
}

export function row(...parts) {
  const button = element("button", "otk-row");
  button.type = "button";
  button.setAttribute("role", "option");
  button.append(...parts);
  return button;
}

/** One verb of an action row. `onclick` arrives already answered-for:
    this module knows nothing about screens, guards included. */
export function actionButton(label, { kind = "", off = false, onclick } = {}) {
  const button = element("button", kind ? `otk-btn ${kind}` : "otk-btn", label);
  button.type = "button";
  button.disabled = off;
  if (onclick) button.onclick = onclick;
  return button;
}

// ---------- textareas that size to their text ----------

// Safari has no `field-sizing: content` yet; where it exists this stays
// out of the way entirely.
const NEEDS_AUTOSIZE = !CSS.supports("field-sizing", "content");

export function autosize(field) {
  if (!NEEDS_AUTOSIZE) return;
  field.style.height = "auto";
  field.style.height = `${field.scrollHeight}px`;
}

/* Setting .value fires no input event, so every programmatic write says
   so itself — a command prefilling the composer, an editor opening on
   existing text. Without this the box opens one line tall with the text
   scrolled out of sight. */
export function setValue(field, text) {
  field.value = text;
  autosize(field);
}

export function watchTextareas() {
  if (!NEEDS_AUTOSIZE) return;
  document.addEventListener("input", (event) => {
    if (event.target.matches("textarea")) autosize(event.target);
  });
  $$("textarea").forEach(autosize);
}

/** The machine's own file dialog, as a promise: the picked file, or null
    if the reader closed it. Cancelling fires `cancel`, not `change` —
    without that arm the promise never settles and its caller waits
    forever. Two screens open it (a story to import, a premise to read),
    which is why it lives here rather than in either of them. */
export function pickFile(accept) {
  return pickFiles(accept, { several: false }).then((files) => files[0] ?? null);
}

/** The same dialog for several files at once — the pictures a line
    attaches — as an array, empty if the reader closed it. */
export function pickFiles(accept, { several = true } = {}) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = several;
    /* In the document while it asks, though never seen: iOS Safari
       answers a click on an input that is not in the document with
       nothing at all, and the promise would never settle. */
    input.hidden = true;
    const settle = (files) => {
      input.remove();
      resolve(files);
    };
    input.onchange = () => settle([...input.files]);
    input.oncancel = () => settle([]);
    document.body.append(input);
    input.click();
  });
}

/** One picked file as the play body carries it: the name, the type the
    browser reported, and the bytes as base64 — the data URL's payload,
    which is how a browser encodes a file without touching its bytes. */
export function encodeFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () =>
      resolve({
        name: file.name,
        media_type: file.type,
        data: String(reader.result).split(",", 2)[1] ?? "",
      });
    reader.readAsDataURL(file);
  });
}

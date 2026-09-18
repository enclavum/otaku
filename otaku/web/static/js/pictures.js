/* A turn's pictures, wherever the page shows a turn: the tiles under it,
   and the picture at full size that a tile opens. The transcript and the
   dossier's reader draw the same tiles from the same row facts, so the
   drawing lives once, below both. Nothing here knows which screen asked. */

import * as api from "./api.js";
import { $, element } from "./dom.js";

export function thumbs(attachments) {
  /* One tile per picture, the thumbnail the store cut for it; a tile is
     a way in to the picture at full size. The alt text is the measure —
     no name is stored, and the reader of a screen reader gets what the
     row knows. */
  const strip = element("div", "otk-thumbs");
  attachments.forEach((picture, at) => {
    const tile = element("button", "otk-thumb");
    tile.type = "button";
    tile.setAttribute("aria-label", `picture, ${picture.width} × ${picture.height}, open`);
    const image = element("img");
    image.alt = "";
    image.loading = "lazy";
    image.src = api.thumbnailUrl(picture.file);
    tile.append(image);
    tile.onclick = () => openPicture(attachments, at);
    strip.append(tile);
  });
  return strip;
}

export function openPicture(attachments, at) {
  /* The file as the model saw it, on the desk: esc closes it as any
     dialog, Close and a click on the desk do the same. The measure
     under it is the row's — width by height, and the bytes stored. A
     turn with several pictures is walked from here, by the arrows on
     the picture's sides or the arrow keys, clamped at the ends. */
  const dialog = $('dialog[data-dialog="picture"]');
  const image = $("img", dialog);
  const prev = $("[data-prev]", dialog);
  const next = $("[data-next]", dialog);
  const several = attachments.length > 1;
  const show = (index) => {
    at = index;
    const picture = attachments[at];
    image.src = api.pictureUrl(picture.file);
    $(".otk-lightbox__meta", dialog).textContent = `${picture.width} × ${picture.height} · ${sized(picture.size)}`;
    $(".otk-lightbox__count", dialog).textContent = several ? `${at + 1} of ${attachments.length}` : "";
    prev.hidden = next.hidden = !several;
    prev.setAttribute("aria-disabled", String(at === 0));
    next.setAttribute("aria-disabled", String(at === attachments.length - 1));
  };
  const step = (by) => {
    const to = at + by;
    if (to >= 0 && to < attachments.length) show(to);
  };
  const wired = new AbortController();
  const down = () => {
    wired.abort();
    if (dialog.open) dialog.close();
    image.removeAttribute("src");
  };
  $("[data-close]", dialog).addEventListener("click", down, { signal: wired.signal });
  prev.addEventListener("click", () => step(-1), { signal: wired.signal });
  next.addEventListener("click", () => step(1), { signal: wired.signal });
  dialog.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "ArrowLeft") step(-1);
      else if (event.key === "ArrowRight") step(1);
      else return;
      event.preventDefault();
    },
    { signal: wired.signal },
  );
  dialog.addEventListener("click", (event) => event.target === dialog && down(), { signal: wired.signal });
  dialog.addEventListener("close", down, { signal: wired.signal });
  show(at);
  dialog.showModal();
}

function sized(bytes) {
  // Decimal, as a phone and the intake's own limit count it.
  return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.round(bytes / 1000)} KB`;
}

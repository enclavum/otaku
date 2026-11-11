/* A card's text read as Markdown, into nodes: the blocks a card's author
   writes — headings, lists, quotes, rules, fenced code, paragraphs — and
   inside them the inline marks a reply's are read with (`prose.runs`:
   `*em*`, `**strong**`, `` `code` ``). Built as elements and text, never
   as HTML, so a card from anywhere draws nothing but its own characters:
   a link, a picture or a tag stays as typed.

   Every character drawn is one of the stored text, in order — markers
   taken out, nothing added: a bullet or a number is the list's own
   drawing — so a place in what is read maps back to the text
   (`kit/browser.js`, `storedOffset`). Unlike a reply, nothing here is
   speech: a card is a document, and its `- ` a list. */

import { element } from "./dom.js";
import { runs } from "./prose.js";

const _FENCE = /^ {0,3}(`{3,}|~{3,})/;
const _RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const _HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/;
const _QUOTE = /^ {0,3}> ?(.*)$/;
const _ITEM = /^( *)([-*+]|\d{1,9}[.)])[ \t]+(.*)$/;

/** The nodes `text` reads as. */
export function markdown(text) {
  return blocks(text.split("\n"));
}

function blocks(lines) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = line.match(_FENCE);
    if (fence) {
      // up to the fence that closes it, or the end of the text
      const body = [];
      for (i++; i < lines.length && !lines[i].trimStart().startsWith(fence[1]); i++) {
        body.push(lines[i]);
      }
      i++;
      const pre = element("pre", "otk-md__code");
      pre.append(element("code", "", body.join("\n")));
      out.push(pre);
      continue;
    }
    if (_RULE.test(line)) {
      out.push(element("hr", "otk-md__rule"));
      i++;
      continue;
    }
    const heading = line.match(_HEADING);
    if (heading) {
      const head = inline(element("p", "otk-md__heading"), heading[2]);
      head.dataset.level = String(heading[1].length);
      out.push(head);
      i++;
      continue;
    }
    if (_QUOTE.test(line)) {
      const inner = [];
      for (; i < lines.length && _QUOTE.test(lines[i]); i++) inner.push(lines[i].match(_QUOTE)[1]);
      const quote = element("blockquote", "otk-md__quote");
      quote.append(...blocks(inner));
      out.push(quote);
      continue;
    }
    if (_ITEM.test(line)) {
      i = list(lines, i, out);
      continue;
    }
    // a paragraph: up to a blank line or the next block, its lines kept
    const own = [];
    for (; i < lines.length && lines[i].trim() && !starts(lines[i]); i++) own.push(lines[i]);
    out.push(inline(element("p", ""), own.join("\n")));
  }
  return out;
}

function starts(line) {
  // whether a line opens a block of its own, ending a paragraph
  return [_FENCE, _RULE, _HEADING, _QUOTE, _ITEM].some((pattern) => pattern.test(line));
}

function list(lines, start, out) {
  /* The list from `start`, nested by indent, into `out`: a line that is
     no item continues the item before it, and a blank line ends the list
     unless an item follows. Answers where reading goes on. */
  // the lists standing, innermost last: { indent, box, ordered, container }
  const open = [];
  let item = null;
  let i = start;
  for (; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) {
      if (!lines.slice(i + 1).find((next) => next.trim())?.match(_ITEM)) break;
      continue;
    }
    const found = line.match(_ITEM);
    if (!found) {
      if (_RULE.test(line) || _HEADING.test(line) || _FENCE.test(line) || _QUOTE.test(line)) break;
      item.append("\n");
      inline(item, line.trim());
      continue;
    }
    const indent = found[1].length;
    const ordered = /\d/.test(found[2]);
    while (open.length && open.at(-1).indent > indent) open.pop();
    const top = open.at(-1);
    if (!top || indent > top.indent || top.ordered !== ordered) {
      // deeper: a list inside the last item; level, of the other kind: one beside it
      const beside = top && indent === top.indent;
      const container = beside ? top.container : (top?.box.lastElementChild ?? null);
      if (beside) open.pop();
      const box = element(ordered ? "ol" : "ul", "otk-md__list");
      const number = Number.parseInt(found[2], 10);
      if (ordered && number !== 1) box.start = number;
      if (container) container.append(box);
      else out.push(box);
      open.push({ indent, box, ordered, container });
    }
    item = inline(element("li", ""), found[3]);
    open.at(-1).box.append(item);
  }
  return i;
}

function inline(box, text) {
  // the inline marks, as a reply's are read, none of it speech
  for (const run of runs(text)) {
    let node = document.createTextNode(run.text);
    if (run.code) node = wrapped("code", "otk-code", node);
    if (run.em) node = wrapped("em", "", node);
    if (run.strong) node = wrapped("strong", "", node);
    box.append(node);
  }
  return box;
}

function wrapped(tag, className, inner) {
  const box = element(tag, className);
  box.append(inner);
  return box;
}

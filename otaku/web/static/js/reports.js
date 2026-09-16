/* The reports: the context preview as a reading panel — the request as
   a document, its stages as live figures over the wire itself — and
   usage, balance and info as torn docket slips. Every number and every
   sentence in them is the backend's (`api.reports`); which shape
   carries it is the design's.

   A slip is figures on dotted leaders, ruled where the reader should
   stop: a double rule under what is totalled, a hairline between
   blocks. Nothing here composes prose about the product. */

import * as api from "./api.js";
import { footnote, guard, popups } from "./browser.js";
import { $, element, span } from "./dom.js";
import { count } from "./format.js";

// ---------- context: the request as a document ----------

export async function openContext() {
  const popup = popups.get("/context");
  const body = $("[data-context]", popup);
  // The panel is up before its data: a click must answer NOW, and the
  // modal keeps further clicks from queueing screens behind it.
  if (!popup.open) {
    body.replaceChildren();
    footnote(popup, "");
    popup.showModal();
  }
  const preview = await api.context();
  /* The one report whose refusal carries the FIX: a context that
     cannot fit says what to shrink. Shown IN the panel the reader
     opened — behind the modal is where a sentence goes unread — and
     this refusal fires exactly when the reader most needs the panel. */
  if (preview.refused) {
    body.replaceChildren(element("p", "otk-note", preview.notice));
    footnote(popup, "");
    body.focus();
    return;
  }
  const shape = preview.shape;

  const summary = element("div", "otk-context__summary");
  const lead = element("span", "otk-context__lead");
  lead.append(
    span("otk-context__figure", `~${count(shape.total_tokens)}`),
    span("otk-label", "tokens"),
  );
  summary.append(lead, span("otk-meta", `${shape.used}% of ${count(shape.limit)}`));
  summary.append(
    span("otk-meta otk-push", `${shape.kept} ${shape.kept === 1 ? "message" : "messages"} verbatim`),
  );

  const head = element("div", "otk-v otk-v--lg");
  head.append(summary, stages(shape));

  const wire = element("div", "otk-context__wire");
  for (const part of preview.parts) {
    const passage = element("div", "otk-passage");
    passage.append(
      span("otk-passage__rubric", part.role),
      element(
        "p",
        part.role === "user" ? "otk-prose otk-prose--said" : "otk-prose otk-prose--wire",
        part.body,
      ),
    );
    wire.append(passage);
  }

  body.replaceChildren(head, wire);
  footnote(popup, `~${count(shape.total_tokens)} of ${count(shape.limit)} tokens`);
  body.focus();
}

function stages(shape) {
  /* What the request is MADE of, at a glance: always the same four
     stages, the empty ones drawn idle — an absent stage is a fact of
     this request, not a missing drawing. */
  const strip = element("div", "otk-context__stages");
  strip.setAttribute("aria-label", "Context window shape");
  const cell = (idle, label, figure, tok) => {
    const box = element("div", idle ? "otk-context__cell otk-context__cell--idle" : "otk-context__cell");
    box.append(
      span(idle ? "otk-label" : "otk-label otk-label--ink", label),
      span("otk-context__figure", figure),
      span("otk-context__tok", tok),
    );
    return box;
  };
  strip.append(cell(!shape.head, "Head", String(shape.head), "verbatim"));
  if (shape.history) {
    const scenes = shape.rolled_up === 1 ? "scene" : "scenes";
    strip.append(cell(false, "History recap", "1", `${shape.rolled_up} ${scenes} rolled up`));
  } else {
    strip.append(cell(true, "History recap", "—", "nothing to recap"));
  }
  if (shape.summaries) {
    strip.append(
      cell(false, "Scene summaries", String(shape.summaries), `${count(shape.middle)} messages in between`),
    );
  } else {
    strip.append(cell(true, "Scene summaries", "0", "nothing summarized"));
  }
  const tailCaption =
    shape.tail_target < shape.tail_setting
      ? `verbatim · reduced from ${shape.tail_setting} to fit`
      : "verbatim";
  strip.append(cell(!shape.tail, "Tail", String(shape.tail), tailCaption));
  return strip;
}

// ---------- the dockets: usage, balance, info ----------

export async function openUsage(scope = "") {
  openSlip("usage", "Usage");
  const report = await api.usage(scope);
  /* A refusal is the whole answer, shown IN the slip rather than behind
     it: this report has two scopes, and the tab beside the empty one is
     the way to the other. */
  if (report.notice) {
    showDocket("usage", "Usage", [element("p", "otk-note", report.notice)]);
    scopeTabs(report.scopes, scope);
    return;
  }
  /* Spend is grouped by what ASKED for it: a pass the extractor ran is
     not a turn the reader played, and a paid provider bills both. The
     purposes are the store's own words. */
  const blocks = [];
  let purpose = null;
  for (const row of report.rows) {
    if (row.purpose !== purpose) {
      purpose = row.purpose;
      // What a purpose is CALLED comes with the row (`reports.USAGE_PURPOSES`),
      // and the count beside it is every request under it.
      const asked = report.rows
        .filter((other) => other.purpose === purpose)
        .reduce((sum, other) => sum + other.requests, 0);
      blocks.push(section(row.label, `${count(asked)} ${asked === 1 ? "request" : "requests"}`));
    }
    blocks.push(usageGroup(row));
  }
  blocks.push(element("div", "otk-rule--double"));
  const total = element("div", "otk-total");
  total.append(span("otk-label", "total"), span("otk-total__figure", count(report.total_tokens)));
  blocks.push(total);
  // The closing sentence is the report's own — the page spells no figure
  // it was not given.
  blocks.push(element("p", "otk-note", report.note));
  showDocket("usage", "Usage", blocks, `${report.scope} · ${count(report.requests)} requests`);
  scopeTabs(report.scopes, scope);
}

function section(name, note) {
  /* A section head outranks the names beneath it: the display face over
     a rule, not a smaller caption. */
  const head = element("div", "otk-section", name);
  head.append(span("otk-section__count", note));
  return head;
}

function usageGroup(row) {
  /* One model's spend as the slip prints it: the name, the request
     count, and a dotted leader per figure. */
  const group = element("div", "otk-v otk-v--sm");
  const head = element("div", "otk-docket__row");
  head.append(
    span("otk-docket__name", `${row.provider} · ${row.model}`),
    span("otk-label", `${count(row.requests)} ${row.requests === 1 ? "req" : "reqs"}`),
  );
  group.append(
    head,
    leader("prompt", count(row.prompt_tokens)),
    leader("cached", count(row.cached_tokens)),
    leader("reply", count(row.completion_tokens)),
    leader("rate", `${row.rate.toFixed(1)} tok/s`, "otk-accent-ink"),
  );
  return group;
}

function scopeTabs(scopes, current) {
  /* The scopes a report can be asked for, as tabs over its figures.
     Their words are the BACKEND's — the same two the report says about
     itself — so a tab and the slip under it can never disagree. */
  const strip = $("[data-scopes]", popups.get("/report"));
  if (!strip) return;
  strip.replaceChildren(
    ...(scopes ?? []).map(({ key, label }) => {
      const tab = element("button", "otk-tab", label.charAt(0).toUpperCase() + label.slice(1));
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(key === current));
      tab.onclick = guard(() => openUsage(key));
      return tab;
    }),
  );
  strip.hidden = !strip.childElementCount;
}

export async function openBalance() {
  openSlip("balance", "Balance");
  /* Everything but the figures shows at once: the ROSTER costs no
     network — every row, its note, the spending sentence — and ONE
     plain read then fills the numbers and the total in place. */
  const roster = await api.balanceRoster();
  // A refusal is the whole answer, shown IN the slip the reader opened
  // — behind the modal is where a sentence goes unread.
  if (roster.notice) {
    showDocket("balance", "Balance", [element("p", "otk-note", roster.notice)]);
    return;
  }
  /* Every provider with an account to bill. A row with no figure says
     which KIND of nothing it is: no key set, an account that would not
     answer — or, on the roster, not asked yet. */
  const drawRow = (row) =>
    leader(row.label, row.money ? row.value : row.note || "…", row.money ? "" : "otk-absent");
  const rows = element("div", "otk-v otk-v--sm");
  const lines = new Map();
  for (const row of roster.rows) {
    /* Named by its CAPTION, as the terminal names it: what a provider is
       called is decided below both frontends (`reports.balances`). */
    const line = drawRow(row);
    lines.set(row.provider, line);
    rows.append(line);
  }
  /* The total's box stands from the first paint — the rule, the label,
     and a figure-sized blank — so nothing below it moves when the
     number lands. Emptied only for the rare report with no
     single-currency total to put in it. */
  // A blank with the FIGURE's own metrics — `otk-absent` swaps face
  // and would move the line 3px when the number lands.
  const totalFigure = span("otk-total__figure", "\u00a0");
  const total = element("div", "otk-total");
  total.append(span("otk-label", "on account"), totalFigure);
  const tail = element("div", "otk-v otk-v--sm");
  tail.append(element("div", "otk-rule--double"), total);
  const blocks = [element("div", "otk-hr"), rows, tail];
  // What the story on screen spends — the report's own sentence, which
  // is the half of "what have I got left" a list of accounts cannot say.
  if (roster.note) blocks.push(element("p", "otk-note", roster.note));
  showDocket("balance", "Balance", blocks);

  const report = await api.balance().catch(() => null);
  if (!report?.rows) return;
  for (const row of report.rows) lines.get(row.provider)?.replaceWith(drawRow(row));
  // The total is the backend's arithmetic, not the page's: it is there
  // only when one currency covers every account that answered.
  if (report.total) {
    totalFigure.textContent = report.total.text;
  } else {
    tail.replaceChildren();
  }
}

export async function openInfo() {
  openSlip("info", "Info");
  const report = await api.info();
  /* The report's blocks: the model's name is the subject and takes the
     title, the story on the page closes the slip, and every other fact
     is a leader. Chosen by LABEL, not by position — one this does not
     know stays a leader, so a renamed row degrades to a line. */
  const blocks = [];
  const closing = [];
  const facts = element("div", "otk-v otk-v--sm");
  let title = null;
  for (const section of report.sections) {
    if (section.note) blocks.push(element("p", "otk-note", section.note));
    for (const [label, value] of section.rows) {
      const name = label.toLowerCase();
      // the subject takes the title, the open story closes the slip
      if (name === "model" && !title) title = value;
      else if (name === "story") closing.push(element("span", "otk-docket__story", value));
      else if (name === "messages") closing.push(span("otk-index__sub", `${value} messages`));
      else facts.append(leader(name, value));
    }
  }
  // The rule goes under the SUBJECT — what the slip is about — not
  // between two blocks of the same kind of fact.
  if (facts.childElementCount) blocks.push(element("div", "otk-hr"), facts);
  if (closing.length) blocks.push(element("div", "otk-rule--double"));
  if (closing.length) {
    const block = element("div", "otk-v otk-v--sm");
    block.append(span("otk-label", "on the page"), ...closing);
    blocks.push(block);
  }
  showDocket("info", "Info", blocks, "", title);
}

/* A value long enough to wrap is not a figure and cannot ride a leader:
   the dots would run into a paragraph. Past this many characters the row
   stacks instead — the name above, the value under it. */
const _FIGURE = 42;

function leader(label, value, kind = "") {
  const text = String(value);
  if (text.length <= _FIGURE) {
    const line = element("div", "otk-leader");
    line.append(span("", label), span(kind, text));
    return line;
  }
  const block = element("div", "otk-v otk-v--xs");
  block.append(span("otk-margin__key", label), element("p", `otk-derived ${kind}`.trim(), text));
  return block;
}

/** The slip up before its data — a click must answer NOW, and the modal
    keeps further clicks from queueing screens behind it. */
function openSlip(kind, title) {
  const popup = popups.get("/report");
  if (popup.open && popup.dataset.report === kind) return;
  showDocket(kind, title, []);
}

/* The BOX each report gets, decided by kind and not by what arrived:
   balance is a column of figures on a narrow slip, usage and info take
   the default width. Every slip is torn to what it says — the balance
   included, whose whole shape stands from the roster with only the
   figures to land, so nothing about its height ever changes. */
const _SIZE = {
  balance: ["otk-docket--narrow"],
  usage: [],
  info: [],
};
// A slip that cannot change size may open before it has anything to say.
const _settles = (kind) => (_SIZE[kind] ?? []).includes("otk-docket--fixed");
const _SIZES = [...new Set(Object.values(_SIZE).flat())];

function showDocket(kind, title, blocks, note = "", subject = "") {
  const popup = popups.get("/report");
  // One slip, three reports: the kind is on the popup so a screen can
  // tell them apart; what the slip SAYS is entirely the report's.
  popup.dataset.report = kind;
  const slip = $(".otk-docket", popup);
  slip.classList.remove(..._SIZES);
  slip.classList.add(...(_SIZE[kind] ?? []));
  $("[data-title]", popup).textContent = title;
  // A report with scopes fills this in after; one without shows none,
  // and never the last report's.
  const strip = $("[data-scopes]", popup);
  if (strip) {
    strip.replaceChildren();
    strip.hidden = true;
  }
  // The subject a report is ABOUT, where a slip prints one.
  const body = $("[data-report-body]", popup);
  body.replaceChildren(...(subject ? [element("span", "otk-docket__title", subject)] : []), ...blocks);
  // Nothing to say yet means the read is still out. A slip torn to its
  // content waits out of sight rather than resizing under the pointer
  // when the answer lands; one with a settled box opens straight away.
  slip.classList.toggle("is-waiting", !blocks.length && !subject && !_settles(kind));
  // The foot carries what the report says about ITSELF — its scope, its
  // count — and nothing when it has nothing: a line naming the command
  // that opened the screen tells a reader what they just did.
  footnote(popup, note);
  if (!popup.open) popup.showModal();
  $(".otk-docket__body", popup).focus();
}

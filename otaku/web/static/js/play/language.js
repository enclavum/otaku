/* The story's typed LANGUAGE, as the page holds it — the framing words
   a line may open with and the ones it may carry inside. Not commands:
   a command is a button here, and what a button calls is an endpoint.

   A leaf on purpose: the composer's menu, the transcript's highlighting
   and the help sheet read the language without depending on any screen.
   Loaded at boot from /api/language, and again whenever a restarted
   otaku comes back — the language is the backend's (`context.syntax`,
   reaching here through `backend.api.story.language`), and the page
   never keeps a stale copy of one. */

// The whole payload once, and the lookups built from it.
let language = null;
let tokens = [];

export function load(loaded) {
  language = loaded;
  // Every framing word, so what the transcript highlights is the
  // language's own answer and never a guess from the slash: `/me` is
  // one, `and/or` is not.
  tokens = rows()
    .flatMap((row) => row.token.split(" "))
    .filter((word) => /^\/[a-z]+$/.test(word));
  // Plus the ONE command a transcript can carry: importing a character
  // records its `/card FILE` line (`backend.api.story.cards`), and that
  // row must read as a command here the way the terminal's block reads.
  tokens.push("/card");
}

/** The openers a line may begin with, then the inliners it may carry —
    the composer menu's order and the help sheet's. */
export function rows() {
  return [...(language?.openers ?? []), ...(language?.inliners ?? [])];
}

/** Whether a word is an OPENER: the menu offers those where a line is
    still nothing but its slash word, and the inliners mid-sentence. */
export function opens(token) {
  return (language?.openers ?? []).some((row) => row.token === token);
}

/** What a line with no framing does — the sentence the help sheet opens
    its playing group with. Said by the backend, laid out by the page. */
export function prose() {
  return language?.prose ?? "";
}

/** Whether one word of a played line is the language's own — the
    transcript's highlighting asks per word. */
export function isToken(word) {
  return tokens.includes(word);
}

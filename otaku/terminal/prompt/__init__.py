"""One submission in: everything between the keyboard and one submitted
line — the prompt session (`session`: the store-backed history, the
slash menu from `completion`, the lexer, the toolbar), its keys
(`keys`), its caret (`caret`), what a line is assembled from (`lines`:
`Carry`, what a shortcut hands across the prompt's exit, and
`LineAssembler`, the `\"\"\"` multiline convention), the answers to a
question the story stands on, drawn ABOVE the prompt line (`answers`),
and the pictures a played line attaches (`pictures`). The run loop only
prompts, feeds and submits; and nothing here needs `Chat` — the prompt
sits below the chat surface.

A shortcut key exits the prompt with its command as the result, stashing
the in-progress text in `Carry` so the next prompt restores it; the keys
arrive as DATA (`build_prompt`'s `shortcuts` — `commands.SHORTCUTS`
passed as the mapping it already is, both spellings riding each entry,
so no seam translates by hand).
"""

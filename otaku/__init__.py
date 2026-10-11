"""The package root: its version, and nothing else.

The layout and the import rules are `docs/code_structure.md`'s: the
frontends (terminal, web) reach the app through `backend.api` alone;
`agents` is the third actor, scheduled by the backend and driven by
idleness; `context` composes what the model sees.
"""

__version__ = "0.8.0"

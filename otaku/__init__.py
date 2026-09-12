"""otaku — the restructured otaku, built beside the old package.

The layout, the import rules, and the control-flow diagram are CLAUDE.md's
"Architecture" section: the frontends (terminal, web) call only `backend`;
`worker` is the third actor, scheduled by backend and driven by idleness;
`context` composes what the model sees. This package replaces `otaku`
wholesale at the end of the restructure (the Phase 3 rename).
"""

__version__ = "0.5.0"

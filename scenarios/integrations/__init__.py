"""The integrations against the real services they ask, one module per
integration: every card site, each web search engine whose key is set,
the support desk. Marked `integration` — and `live`, so every offline
run leaves them out; `make integrations` runs them alone, and a workflow
of their own runs them twice a week (`.github/workflows/integrations.yml`),
since a service changes on its own schedule, never on a push. They ask through
the integration, as the app does, and hold what the app reads of the
answer — so a site that moves or reshapes its card fails here, not in a
reader's import."""

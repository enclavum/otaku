"""THE DOMAIN — what otaku does, below the interface: the installation
(`app`: the App, its settings files, its providers, models and
integrations, its reports), a story (`story`: its chain, its messages and
the play, its settings, tools, pictures and lore, its next request), and
documents in and out (`formats`).

The domain owns every store access and every rule; it returns its own
values (store rows, small records, its own events) and raises the
refusals (`backend.errors`), and a write answers the sentence that says
what it did. It never imports `backend.api`, which calls it and builds
what a client is answered with.
"""

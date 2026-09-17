"""The OpenAI protocol as a client: `auth` (the key in force), `models`
(what a provider offers and what each model can do), `completion` (the
wire: streams, counts) and `client` (who the engine is, the parts it
owns, the account's balance). Every engine in `clients/` subclasses
`client.OpenAIClient` and, where it has something of its own to say,
the parts.
"""

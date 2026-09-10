"""What can go wrong at a provider, named — the one family a caller
catches. `str(e)` is a sentence a frontend may show as it is: it names
the provider and, where the server explained itself, carries its words.

Four kinds, by where the failure came from:

- `UnreachableError` — no server answered: a refused connection, a
  timeout, a network that is not there, a connection lost mid-stream.
- `UnauthorizedError` — the server answered that the key is missing or
  wrong (401, 403), or a catalog that needs a key was asked without one.
- `StatusError` — the server answered with any other error status;
  `status` says which, the sentence carries its explanation.
- `DeclinedError` — the model itself answered with a refusal or an
  in-stream error frame instead of content.

Nothing of the transport crosses this line: `http` maps every failure
onto these, so a caller never imports httpx.
"""


class ProviderError(Exception):
    """The base every provider failure is an instance of."""


class UnreachableError(ProviderError):
    pass


class UnauthorizedError(ProviderError):
    pass


class StatusError(ProviderError):
    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class DeclinedError(ProviderError):
    pass

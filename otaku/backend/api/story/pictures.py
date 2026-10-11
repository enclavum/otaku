"""The pictures a turn carries, by the name its attachments give them
(`PicturesResource`), each with its thumbnail. A file never changes once
written."""

from __future__ import annotations

from dataclasses import dataclass

from otaku.backend.domain.app import App
from otaku.backend.domain.story import pictures as domain_pictures


class PicturesResource:
    """/pictures/{name} — a stored picture."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.thumbnail = ThumbnailResource(app)

    def get(self, name: str) -> Picture:
        """A stored picture as the model saw it, with its media type,
        under the name a turn's attachments carry. Raises NotFound when
        the folder has no such file."""
        data, media_type = domain_pictures.get_picture(self._app, name)
        return Picture(data, media_type)


class ThumbnailResource:
    """/pictures/{name}/thumbnail — a stored picture's thumbnail."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self, name: str) -> Picture:
        """Its thumbnail, a JPEG, under the same rule."""
        return Picture(domain_pictures.get_thumbnail(self._app, name), "image/jpeg")


@dataclass(frozen=True)
class Picture:
    """A picture's bytes as stored, and its media type."""

    data: bytes
    media_type: str

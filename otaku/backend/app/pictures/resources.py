"""The installation's stored pictures, a turn's and a card's alike, by
the name their rows give them (`PicturesResource`), each with its
thumbnail. A file never changes once written."""

from __future__ import annotations

from otaku.backend.app.core.services import App
from otaku.backend.app.pictures import services as domain_pictures
from otaku.backend.app.pictures.schemas import Picture
from otaku.backend.base import Resource


class PicturesResource(Resource):
    """/pictures/{name} — a stored picture."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.thumbnail = ThumbnailResource(app)

    def get(self, name: str) -> Picture:
        """A stored picture as the model saw it, with its media type,
        under the name its row carries. Raises NotFound when the folder
        has no such file."""
        data, media_type = domain_pictures.get_picture(self._app, name)
        return Picture(data, media_type)


class ThumbnailResource(Resource):
    """/pictures/{name}/thumbnail — a stored picture's thumbnail."""

    def get(self, name: str) -> Picture:
        """Its thumbnail, a JPEG, under the same rule."""
        return Picture(domain_pictures.get_thumbnail(self._app, name), "image/jpeg")

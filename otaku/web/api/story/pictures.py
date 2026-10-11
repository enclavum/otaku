"""A turn's pictures, as the page draws them: the file and its
thumbnail, by the name the turn's attachments carry — bytes, which a
browser may keep, since a file never changes once written."""

from otaku.backend.api import Api
from otaku.backend.api.story.pictures import Picture
from otaku.web.api._request import Blob, Route


def _blob(picture: Picture) -> Blob:
    return Blob(picture.data, picture.media_type)


def _picture(api: Api, name: str) -> Blob:
    return _blob(api.pictures.get(name))


# ---------- the rows ----------


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/pictures/{name}"): lambda api, ask: _picture(api, ask.params["name"]),
    ("GET", "/api/pictures/{name}/thumbnail"): lambda api, ask: _blob(
        api.pictures.thumbnail.get(ask.params["name"])
    ),
}

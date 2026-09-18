"""Pictures coming in.

`backend.files.prepare` promises the wire copy of a picture: the type
read from the bytes, the camera's orientation applied, the long edge
downsized to the wire edge and never upsized, every metadata block gone
but the colour profile, PNG kept as PNG and everything else made JPEG,
a thumbnail cut from the same image — and a refusal, with a sentence,
for a file too big, a type not taken, a picture over the pixel limit,
or bytes that are not a picture at all.
"""

import io

import pytest
from PIL import Image

from otaku.backend import files
from otaku.backend.files import RawFile
from otaku.backend.session import Refused

ICC = b"not a real profile, but bytes the container carries as one"


class TestPrepare:
    def test_a_large_photo_is_downsized_to_the_wire_edge(self) -> None:
        picture = files.read_picture(RawFile(_jpeg(3000, 2000)))
        assert (picture.width, picture.height) == (files.WIRE_EDGE, 1045)
        assert picture.media_type == "image/jpeg"
        assert _decode(picture.data).size == (files.WIRE_EDGE, 1045)

    def test_a_tall_photo_is_downsized_by_its_long_edge(self) -> None:
        picture = files.read_picture(RawFile(_jpeg(2000, 3000)))
        assert (picture.width, picture.height) == (1045, files.WIRE_EDGE)

    def test_a_small_picture_is_never_upsized(self) -> None:
        picture = files.read_picture(RawFile(_png(300, 200)))
        assert (picture.width, picture.height) == (300, 200)

    def test_png_stays_png_and_keeps_its_alpha(self) -> None:
        picture = files.read_picture(RawFile(_png(300, 200, mode="RGBA")))
        assert picture.media_type == "image/png"
        decoded = _decode(picture.data)
        assert (decoded.format, decoded.mode) == ("PNG", "RGBA")

    def test_an_iphone_multi_picture_jpeg_is_taken_as_its_first_picture(self) -> None:
        # A JPEG carrying an HDR gain map or a depth map is an MPO to
        # Pillow: the photograph is the first picture in it.
        first = Image.new("RGB", (300, 200), "orange")
        depth = Image.new("RGB", (150, 100), "teal")
        source = _bytes(first, "MPO", append_images=[depth])
        picture = files.read_picture(RawFile(source, "IMG_0001.jpeg"))
        assert picture.media_type == "image/jpeg"
        assert (picture.width, picture.height) == (300, 200)
        decoded = _decode(picture.data)
        assert (decoded.format, getattr(decoded, "n_frames", 1)) == ("JPEG", 1)

    def test_everything_else_becomes_jpeg(self) -> None:
        picture = files.read_picture(RawFile(_bytes(Image.new("RGB", (300, 200), "teal"), "WEBP")))
        assert picture.media_type == "image/jpeg"
        assert _decode(picture.data).format == "JPEG"

    def test_transparency_lands_on_white_in_a_jpeg(self) -> None:
        clear = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        picture = files.read_picture(RawFile(_bytes(clear, "WEBP", lossless=True)))
        assert _decode(picture.data).getpixel((5, 5)) == (255, 255, 255)

    def test_the_camera_orientation_is_applied(self) -> None:
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90° clockwise to view
        picture = files.read_picture(RawFile(_jpeg(400, 200, exif=exif.tobytes())))
        assert (picture.width, picture.height) == (200, 400)

    def test_metadata_is_dropped(self) -> None:
        exif = Image.Exif()
        exif[0x010F] = "Cam"  # Make
        source = _jpeg(400, 200, exif=exif.tobytes(), comment=b"taken at home")
        assert Image.open(io.BytesIO(source)).getexif()[0x010F] == "Cam"
        decoded = _decode(files.read_picture(RawFile(source)).data)
        assert dict(decoded.getexif()) == {}
        assert "comment" not in decoded.info

    def test_the_colour_profile_is_kept(self) -> None:
        for source in (_jpeg(400, 200, icc_profile=ICC), _png(400, 200, icc_profile=ICC)):
            picture = files.read_picture(RawFile(source))
            assert _decode(picture.data).info["icc_profile"] == ICC
            assert _decode(picture.thumb).info["icc_profile"] == ICC

    def test_the_thumbnail_is_a_jpeg_cut_to_the_thumb_edge(self) -> None:
        picture = files.read_picture(RawFile(_png(3000, 2000, mode="RGBA")))
        thumb = _decode(picture.thumb)
        assert (thumb.format, thumb.mode) == ("JPEG", "RGB")
        assert thumb.size == (files.THUMB_EDGE, 341)

    def test_a_small_picture_gets_a_thumbnail_no_bigger_than_itself(self) -> None:
        picture = files.read_picture(RawFile(_png(300, 200)))
        assert _decode(picture.thumb).size == (300, 200)

    def test_a_file_over_the_byte_limit_is_refused(self) -> None:
        with pytest.raises(Refused, match="10 MB") as refusal:
            files.read_picture(RawFile(b"x" * (files.MAX_BYTES + 1), "photo.jpg"))
        assert "photo.jpg" in str(refusal.value)

    def test_a_type_not_taken_is_refused(self) -> None:
        for source in (
            _bytes(Image.new("RGB", (10, 10)), "GIF"),
            _bytes(Image.new("RGB", (10, 10)), "BMP"),
        ):
            with pytest.raises(Refused, match="cannot accept"):
                files.read_picture(RawFile(source))

    def test_bytes_that_are_no_picture_are_refused(self) -> None:
        with pytest.raises(Refused, match="cannot accept"):
            files.read_picture(RawFile(b"<html>not a picture</html>", "page.html"))

    def test_a_picture_cut_off_past_its_header_is_refused(self) -> None:
        whole = _jpeg(1200, 800)
        with pytest.raises(Refused):
            files.read_picture(RawFile(whole[: len(whole) // 3]))

    def test_a_decompression_bomb_is_refused_on_its_header(self) -> None:
        # 105 megapixels of one bit each: a tiny file that would decode to
        # far more than the limit — refused before a pixel is.
        with pytest.raises(Refused, match="100 megapixels"):
            files.read_picture(RawFile(_bytes(Image.new("1", (10500, 10000)), "PNG")))

    def test_a_48_megapixel_phone_picture_is_taken(self) -> None:
        # What a current phone shoots at full resolution, as one bit a
        # pixel so the fixture stays small: under the guard, downsized.
        picture = files.read_picture(RawFile(_bytes(Image.new("1", (8064, 6048)), "PNG")))
        assert (picture.width, picture.height) == (files.WIRE_EDGE, 1176)

    def test_the_sentence_names_the_file_only_when_it_has_a_name(self) -> None:
        with pytest.raises(Refused, match="the picture"):
            files.read_picture(RawFile(b"nothing"))

    def test_heic_decodes_when_the_opener_is_present(self) -> None:
        pytest.importorskip("pillow_heif")
        picture = files.read_picture(
            RawFile(_bytes(Image.new("RGB", (640, 480), "teal"), "HEIF"), "shot.heic")
        )
        assert picture.media_type == "image/jpeg"
        assert (picture.width, picture.height) == (640, 480)


def _bytes(image: Image.Image, fmt: str, **save: object) -> bytes:
    out = io.BytesIO()
    image.save(out, fmt, **save)
    return out.getvalue()


def _jpeg(width: int, height: int, **save: object) -> bytes:
    return _bytes(Image.new("RGB", (width, height), "orange"), "JPEG", **save)


def _png(width: int, height: int, mode: str = "RGB", **save: object) -> bytes:
    return _bytes(Image.new(mode, (width, height), "orange"), "PNG", **save)


def _decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image

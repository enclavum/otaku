"""Character-card FILES: the reader's contract, from bytes to the one
normalized `Card` plus the notes an import should say out loud. A PNG
holds a LIST of embedded cards; selection is explicit — `ccv3` first
(the V3 spec's own instruction), else the FIRST `chara` — and copies
that genuinely disagree are reported. Normalization is an allowlist;
macros stay unbound; line endings normalize; `mes_example` splits on
`<START>`. Every test builds its bytes in memory — no fixtures, no
disk."""

import base64
import json
import struct
import tomllib
import zlib

import pytest

from otaku.backend.formats.cards import CardError, has_embedded_cards, read_card


class TestReadCard:
    def test_a_v2_json_reads_through_its_envelope(self) -> None:
        card, notes = read_card(_json(_v2(name="Elara", description="warden")))
        assert (card.name, card.description) == ("Elara", "warden")
        assert notes == []

    def test_a_v1_flat_json_reads_the_same_fields(self) -> None:
        card, _ = read_card(_json({"name": "Elara", "description": "warden"}))
        assert (card.name, card.description) == ("Elara", "warden")

    def test_a_png_text_chunk_reads(self) -> None:
        card, _ = read_card(_png(_text_chunk(b"chara", _v2(name="Elara"))))
        assert card.name == "Elara"

    def test_a_compressed_chunk_reads(self) -> None:
        card, _ = read_card(_png(_ztxt_chunk(b"chara", _v2(name="Elara"))))
        assert card.name == "Elara"

    def test_an_international_chunk_reads(self) -> None:
        card, _ = read_card(_png(_itxt_chunk(b"ccv3", _v2(name="Elara"))))
        assert card.name == "Elara"

    def test_the_keyword_matches_case_insensitively(self) -> None:
        card, _ = read_card(_png(_text_chunk(b"Chara", _v2(name="Elara"))))
        assert card.name == "Elara"

    def test_ccv3_wins_over_chara(self) -> None:
        # The V3 spec instructs a reader that understands V3 to prefer it.
        chara = _text_chunk(b"chara", _v2(name="Old"))
        ccv3 = _text_chunk(b"ccv3", _v2(name="New"))
        card, _ = read_card(_png(chara, ccv3))
        assert card.name == "New"

    def test_duplicate_charas_take_the_first(self) -> None:
        # The first copy is the one that matches the site's own JSON
        # export — and "first" must be explicit: a dict-keyed parse keeps
        # the LAST and misreports the file.
        first = _text_chunk(b"chara", _v2(name="First"))
        second = _text_chunk(b"chara", _v2(name="Second"))
        card, notes = read_card(_png(first, second))
        assert card.name == "First"
        assert any("disagree" in note for note in notes)

    def test_equivalent_copies_import_silently(self) -> None:
        # The real-world case: the same card twice; nothing worth a note.
        copy = _v2(name="Elara", description="warden")
        card, notes = read_card(_png(_text_chunk(b"chara", copy), _text_chunk(b"ccv3", copy)))
        assert card.name == "Elara"
        assert notes == []

    def test_the_file_name_never_makes_the_copies_disagree(self) -> None:
        # The name is stamped on the chosen card AFTER the comparison —
        # stamped first, it made every import "disagree" with its own
        # identical candidates.
        copy = _v2(name="Elara", description="warden")
        png = _png(_text_chunk(b"chara", copy), _text_chunk(b"ccv3", copy))
        _, notes = read_card(png, file_name="elara.png")
        assert notes == []

    def test_a_single_card_never_disagrees_with_itself(self) -> None:
        _, notes = read_card(_json(_v2(name="Elara")), file_name="elara.json")
        assert notes == []

    def test_an_unreadable_chunk_is_skipped_not_fatal(self) -> None:
        broken = _chunk(b"tEXt", b"chara\x00!!not-base64-json!!")
        card, _ = read_card(_png(broken, _text_chunk(b"chara", _v2(name="Elara"))))
        assert card.name == "Elara"

    def test_a_png_with_no_card_is_refused(self) -> None:
        with pytest.raises(CardError, match="no character card"):
            read_card(_png())

    def test_a_card_without_a_name_is_refused(self) -> None:
        with pytest.raises(CardError, match="no character name"):
            read_card(_json(_v2(name="  ")))

    def test_bytes_that_are_neither_format_are_refused(self) -> None:
        with pytest.raises(CardError, match="not a PNG and not readable as JSON"):
            read_card(b"hello there")

    def test_a_json_list_is_refused(self) -> None:
        with pytest.raises(CardError, match="no card object"):
            read_card(b"[1, 2]")

    def test_a_lorebook_left_behind_is_said_out_loud(self) -> None:
        _, bare = read_card(_json(_v2(name="Elara")))
        _, with_book = read_card(_json(_v2(name="Elara", character_book={"entries": [{}, {}]})))
        _, empty_book = read_card(_json(_v2(name="Elara", character_book={"entries": []})))
        assert (len(bare), len(with_book), len(empty_book)) == (0, 1, 0)

    def test_the_file_name_rides_as_reference(self) -> None:
        card, _ = read_card(_json(_v2(name="E")), file_name="seraphina.png")
        assert card.file_name == "seraphina.png"
        data = tomllib.loads(card.to_toml())
        assert data["file_name"] == "seraphina.png"
        # And absent when the caller had none — never an empty key.
        bare, _ = read_card(_json(_v2(name="E")))
        assert "file_name" not in bare.to_toml()

    def test_macros_nothing_binds_are_reported(self) -> None:
        payload = _v2(name="Elara", description="roll {{roll:d20}} for {{char}}")
        _, notes = read_card(_json(payload))
        assert any("{{roll:d20}}" in note for note in notes)

    def test_original_is_bound_so_never_reported(self) -> None:
        payload = _v2(name="Elara", system_prompt="{{original}} Stay in {{char}}'s voice.")
        _, notes = read_card(_json(payload))
        assert notes == []


class TestHasEmbeddedCards:
    """Whether a PNG carries a card at all — one a chunk could be read
    from, under either keyword — or is a picture alone."""

    def test_a_png_with_a_card_has_one(self) -> None:
        assert has_embedded_cards(_png(_text_chunk(b"chara", _v2(name="Elara"))))
        assert has_embedded_cards(_png(_itxt_chunk(b"ccv3", _v2(name="Elara"))))

    def test_a_png_without_one_is_a_picture_alone(self) -> None:
        assert not has_embedded_cards(_png())


class TestNormalization:
    def test_line_endings_normalize(self) -> None:
        card, _ = read_card(_json(_v2(name="E", first_mes="one\r\ntwo\rthree")))
        assert card.greeting == "one\ntwo\nthree"

    def test_examples_split_on_their_delimiter(self) -> None:
        # <START> separates example conversations; passed through whole
        # the model reads it as literal text.
        payload = _v2(name="E", mes_example="<START>\nfirst block\n<start>\nsecond block\n<START>")
        card, _ = read_card(_json(payload))
        assert card.examples == ("first block", "second block")

    def test_a_personality_only_card_is_valid(self) -> None:
        # mango_Iblisse: no description at all — everything in personality.
        card, _ = read_card(_json(_v2(name="Iblisse", personality="chaos itself")))
        assert (card.description, card.personality) == ("", "chaos itself")

    def test_the_reminder_needs_text_not_just_the_key(self) -> None:
        with_key = _v2(name="E", extensions={"depth_prompt": {"depth": 0, "prompt": "  "}})
        card, _ = read_card(_json(with_key))
        assert card.reminder == ""
        with_text = _v2(name="E", extensions={"depth_prompt": {"prompt": "always hits"}})
        card, _ = read_card(_json(with_text))
        assert card.reminder == "always hits"

    def test_the_reminders_depth_counts_the_readers_messages(self) -> None:
        # SillyTavern counts every message, 0 after the last; otaku the
        # reader's alone, 1 before the newest: half, rounded up, from 1.
        for st_depth, depth in ((0, 1), (1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (9, 5)):
            prompt = {"depth_prompt": {"depth": st_depth, "prompt": "p"}}
            card, _ = read_card(_json(_v2(name="E", extensions=prompt)))
            assert card.reminder_depth == depth, st_depth

    def test_a_reminder_without_a_depth_takes_sillytaverns_default(self) -> None:
        for prompt in ({"prompt": "p"}, {"prompt": "p", "depth": "deep"}):
            card, _ = read_card(_json(_v2(name="E", extensions={"depth_prompt": prompt})))
            assert card.reminder_depth == 2  # SillyTavern's 4, counted otaku's way

    def test_blank_alternate_greetings_drop(self) -> None:
        card, _ = read_card(_json(_v2(name="E", alternate_greetings=["hi", "  ", "yo"])))
        assert card.alternate_greetings == ("hi", "yo")

    def test_the_fields_beside_the_block_survive(self) -> None:
        # Storage is generous: the system prompt rides on its own, the
        # post-history instructions and the notes are kept, sent nowhere.
        payload = _v2(
            name="E", system_prompt="sys", post_history_instructions="ph", creator_notes="read me"
        )
        card, _ = read_card(_json(payload))
        assert (card.system_prompt, card.post_history_instructions, card.creator_notes) == (
            "sys",
            "ph",
            "read me",
        )

    def test_runtime_junk_never_reaches_the_card(self) -> None:
        # The allowlist: `chat` (the ST leak) and friends drop on the floor.
        payload = _v2(name="E", chat="private-chat-name", char_persona="undefined undefined")
        card, _ = read_card(_json(payload))
        assert not hasattr(card, "chat")
        assert "private-chat-name" not in card.to_toml()


def _v2(**data: object) -> dict:
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}


def _json(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def _b64(payload: dict) -> bytes:
    return base64.b64encode(json.dumps(payload).encode())


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + b"\x00\x00\x00\x00"


def _text_chunk(keyword: bytes, payload: dict) -> bytes:
    return _chunk(b"tEXt", keyword + b"\x00" + _b64(payload))


def _ztxt_chunk(keyword: bytes, payload: dict) -> bytes:
    return _chunk(b"zTXt", keyword + b"\x00\x00" + zlib.compress(_b64(payload)))


def _itxt_chunk(keyword: bytes, payload: dict) -> bytes:
    head = keyword + b"\x00\x00\x00" + b"\x00" + b"\x00"  # uncompressed, no lang, no translation
    return _chunk(b"iTXt", head + _b64(payload))


def _png(*chunks: bytes) -> bytes:
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"".join(chunks) + _chunk(b"IEND", b"")

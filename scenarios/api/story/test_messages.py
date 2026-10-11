"""Playing a story: turns, the wire promise, the roleplay commands /me,
/you, /ooc, the inline pair typed inside a line, the tools a reply
calls and the answers to a question, the novel and the canvas — and a
message corrected.
"""

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.shortcuts import PromptSession

from otaku.backend.api import (
    CompletionType,
    HeadMoved,
    InjectionPosition,
    Invalid,
    Layout,
    Refused,
    StoryMode,
)
from otaku.backend.api.app import ModelInUse
from otaku.backend.api.story.messages import (
    AssistantMessage,
    AssistantText,
    AssistantToolCall,
    Done,
    RawFile,
    ToolMessage,
    UserMessage,
)
from otaku.backend.api.story.stories import Story
from otaku.backend.domain._formats import EXPORT_MARKER
from otaku.backend.domain.app._paths import Paths
from otaku.backend.domain.story import _chain as chain
from otaku.backend.domain.story._play import NO_PICTURE_ON_TEXT, NOTHING_TO_SEND
from otaku.context.assembler import JoinType, estimate_tokens
from otaku.settings import models as models_file
from otaku.store.schema import Message
from otaku.terminal.commands import SHORTCUTS
from otaku.terminal.prompt.lines import Carry, LineAssembler
from otaku.terminal.prompt.session import build_prompt
from otaku.terminal.tty import render
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config, set_config_provider
from scenarios.support.screens import (
    BACKSPACE,
    CTRL_C,
    DOWN,
    ENTER,
    ESC,
    RIGHT,
)


class TestCorrection:
    """A correction changes the words, never how the row joins: the
    whitespace at a stored row's edges stays, whatever the edit's."""

    def test_a_canvas_rows_paragraph_break_stays_through_an_edit(self, app: App) -> None:
        app.play("I enter the hall.")
        app.api.stories.settings.update(app.ensure_story(), "layout", layout=Layout.CANVAS)
        app.play("\n\nIt rained.")
        line = next(m for m in app.messages if "rained" in m.body)
        assert line.body == "\n\nIt rained."
        app.api.stories.messages.update(app.story_id, line.id, body="  It poured. \n")
        stored = [m.body for m in app.api.stories.messages.list(app.story_id)]
        assert "\n\nIt poured." in stored

    def test_a_chat_rows_correction_is_stored_trimmed(self, app: App) -> None:
        app.play("I enter the hall.")
        first = app.messages[0]
        app.api.stories.messages.update(
            app.story_id, first.id, body="  I enter the throne hall. \n"
        )
        assert app.messages[0].body == "I enter the throne hall."


class TestTheHeadAWriteNames:
    """A write that moves a story's head names the head its caller last
    saw; the store moves it by compare-and-set, and a head moved
    meanwhile is refused with where it stands — nothing written."""

    def test_a_head_move_that_names_no_head_is_refused(self, app: App) -> None:
        app.play("I climb the stairs.")
        story, first = app.ensure_story(), app.messages[0].id
        with pytest.raises(Invalid):
            app.api.stories.update(story, head=first)
        assert len(app.messages) == 2

    def test_a_line_played_from_a_head_that_moved_is_refused_and_nothing_lands(
        self, app: App
    ) -> None:
        app.play("I climb the stairs.")
        story, stale = app.ensure_story(), app.messages[0].id
        with pytest.raises(HeadMoved) as caught:
            list(
                app.api.stories.messages.create(story, line="I trim the wick.", expected_head=stale)
            )
        assert caught.value.head == app.messages[-1].id
        assert len(app.messages) == 2

    def test_an_undo_and_a_rewind_from_a_stale_head_are_refused(self, app: App) -> None:
        app.play("I climb the stairs.")
        story, first = app.ensure_story(), app.messages[0].id
        with pytest.raises(HeadMoved):
            app.api.stories.undos.create(story, expected_head=first)
        with pytest.raises(HeadMoved):
            app.api.stories.update(story, head=first, expected_head=first)
        assert len(app.messages) == 2

    def test_the_head_last_seen_moves_it(self, app: App) -> None:
        app.play("I climb the stairs.")
        story = app.ensure_story()
        first, head = app.messages[0].id, app.messages[-1].id
        assert app.api.stories.update(story, head=first, expected_head=head).value.head == first
        assert [m.id for m in app.messages] == [first]


class TestTurns:
    def test_a_message_gets_a_reply_and_both_persist(self, app: App) -> None:
        app.play("I open the door.")
        chain = app.store.stories.get_messages(app.story_id)
        assert [(m.role, m.body) for m in chain] == [
            ("user", "I open the door."),
            ("assistant", scripted.CHAT_REPLY),
        ]

    def test_the_wire_carries_the_message_verbatim(self, app: App) -> None:
        app.play("/system You are the narrator.")
        app.play("I wait.   With   spaces?")
        sent = app.server.requests[-1]["messages"]
        assert sent[0] == {"role": "system", "content": "You are the narrator."}
        assert sent[1] == {"role": "user", "content": "I wait.   With   spaces?"}

    def test_me_frames_the_line_at_wire_time_only(self, app: App) -> None:
        app.play("/me Elara: I step into the light.")
        sent = app.server.requests[-1]["messages"][-1]["content"]
        assert sent == "((OOC: The user writes as Elara.))\nI step into the light."
        stored = app.store.stories.get_messages(app.story_id)[0]
        assert stored.body == "/me Elara: I step into the light."  # the line as typed

    def test_the_framing_templates_come_from_the_prompts_file(self, server, tmp_path) -> None:
        # The file IS the injection, not a copy of it. Asserting the
        # built-in wording would pass even if the load path were dropped
        # and the defaults hardcoded, so the templates here are edited.
        root = tmp_path / "state"
        paths = Paths.resolve(root)
        paths.ensure_tree()
        paths.prompts_file.write_text(
            'me_framing = "<<{name} speaks>>\\n{body}"\n'
            'you_framing = "<<now play {name}>>"\n'
            'ooc_framing = "<<aside: {body}>>"\n'
        )
        app = launch(root, server)
        try:
            app.play("/me Elara: I step into the light.")
            assert sent(app) == "<<Elara speaks>>\nI step into the light."
            app.play("/you Ryn")
            assert sent(app) == "<<now play Ryn>>"
            app.play("/ooc What genre is this?")
            assert sent(app) == "<<aside: What genre is this?>>"
        finally:
            app.close()

    def test_a_turn_keeps_the_wording_it_was_played_with(self, server, tmp_path) -> None:
        # The template is snapshotted onto the row as it plays, so editing
        # prompts.toml afterwards cannot rewrite what a past turn said to
        # the model — only what the next one will.
        root = tmp_path / "state"
        paths = Paths.resolve(root)
        paths.ensure_tree()
        paths.prompts_file.write_text('me_framing = "<<first {name}>>\\n{body}"\n')
        app = launch(root, server)
        try:
            app.play("/me Elara: one.")
        finally:
            app.close()
        paths.prompts_file.write_text('me_framing = "<<second {name}>>\\n{body}"\n')
        app = launch(root, server)
        try:
            app.play("/me Elara: two.")
            wire = app.server.requests[-1]["messages"]
            assert wire[0]["content"] == "<<first Elara>>\none."
            assert wire[-1]["content"] == "<<second Elara>>\ntwo."
        finally:
            app.close()

    def test_a_stream_error_keeps_what_already_arrived(self, app: App, capsys) -> None:
        # The prose the user watched stream is in the story, error or not —
        # the screen and the story must never diverge.
        app.server.fail_after = 1
        app.play("I enter the hall.")
        assert "[ error:" in capsys.readouterr().out
        chain = app.store.stories.get_messages(app.story_id)
        first_chunk = scripted.CHAT_REPLY[: max(1, len(scripted.CHAT_REPLY) // 3)]
        assert [m.body for m in chain] == ["I enter the hall.", first_chunk]

    def test_model_control_bytes_never_reach_the_screen(self, app: App, capsys) -> None:
        # A hostile or glitchy stream cannot move the cursor, retitle the
        # window, or desync the screen ledger — the story keeps the bytes.
        app.server.script = lambda body: "safe\x1b[2Atext\x07"
        app.play("I enter the hall.")
        out = capsys.readouterr().out
        assert "\x1b[2A" not in out
        assert "\x07" not in out
        assert app.messages[-1].body == "safe\x1b[2Atext\x07"

    def test_a_replys_padding_blank_lines_never_print(self, app: App, capsys) -> None:
        # Some cloud models wrap the reply in blank lines; the screen and
        # the record both start at the first real character and end at
        # the last.
        app.server.script = lambda body: "\n\nThe hall glows.\n\n"
        app.play("I enter the hall.")
        assert "\n\n\n" not in capsys.readouterr().out
        assert app.messages[-1].body == "The hall glows."


class TestReplyReport:
    def test_a_reply_cut_at_the_limit_is_said_so(self, app: App) -> None:
        # The wire's "length" is a reply the model did not finish: the
        # turn's end carries the fact and the sentence for it, and the
        # reply is recorded as it came.
        app.server.finish = "length"
        events = list(
            app.api.stories.messages.create(
                app.ensure_story(), line="I enter the hall.", expected_head=app.head()
            )
        )
        done = events[-1]
        assert isinstance(done, Done)
        assert done.report is not None and done.report.stats.finish_reason == "length"
        assert done.report.truncated and done.report.notice
        # the reply landed before it, told by its own event, as recorded
        landed = _landed(events)
        assert landed.body and landed == app.messages[-1]

    def test_a_finished_reply_has_nothing_to_notice(self, app: App) -> None:
        events = list(
            app.api.stories.messages.create(
                app.ensure_story(), line="I enter the hall.", expected_head=app.head()
            )
        )
        done = events[-1]
        assert isinstance(done, Done)
        assert done.report is not None and not done.report.truncated
        assert done.report.notice == "" and done.stats == ""  # verbose is off


class TestProviderFailures:
    """What the reader is told when a turn cannot be played: the
    provider package's own sentence, which names the provider and
    carries the server's explanation — never a transport type."""

    def test_an_error_status_names_the_provider_and_the_status(self, app: App, capsys) -> None:
        app.server.refuse = lambda body: 503
        app.play("I enter the hall.")
        out = capsys.readouterr().out
        assert "[ error: Refused by generic with HTTP 503: " in out
        assert "refused by the script" in out

    def test_a_rate_limited_turn_says_when_to_try_again(self, app: App, capsys) -> None:
        app.server.refuse = lambda body: 429
        app.server.retry_after = "30"
        app.play("I enter the hall.")
        out = capsys.readouterr().out
        assert "[ error: Refused by generic with HTTP 429: " in out
        assert "Try again in 30 seconds." in out

    def test_a_declining_model_says_so_in_its_words(self, app: App, capsys) -> None:
        app.server.decline = "content filtered"
        app.play("I enter the hall.")
        assert "[ error: The reply broke off: content filtered ]" in capsys.readouterr().out

    def test_a_dead_provider_cannot_be_reached(self, server, tmp_path, capsys) -> None:
        dead = scripted.ModelServer()
        dead.close()  # the port is known, and nothing answers on it
        set_config_provider(tmp_path / "state", dead)
        app = launch(tmp_path / "state", dead)
        try:
            app.play("I enter the hall.")
            assert "[ error: Could not reach generic. ]" in capsys.readouterr().out
        finally:
            app.close()


class TestAttribution:
    def test_a_turn_to_openrouter_names_the_app(self, server, tmp_path) -> None:
        root = tmp_path / "state"
        set_config_provider(root, server, name="openrouter")
        app = launch(root, server, spec="openrouter/test-model")
        try:
            app.play("I open the door.")
            headers = sent_headers(app)
        finally:
            app.close()
        assert headers["http-referer"] == "https://otaku.sh"
        assert headers["x-openrouter-title"] == "otaku"
        assert headers["x-openrouter-categories"] == "roleplay,creative-writing"
        # Added to the auth, never in place of it.
        assert headers["authorization"] == "Bearer scenario-key"

    def test_no_other_provider_is_told_anything(self, app: App) -> None:
        # The hook is per client: a generic OpenAI endpoint — and every
        # local engine — sends what it always sent.
        app.play("I open the door.")
        headers = sent_headers(app)
        assert [name for name in headers if name.startswith("x-openrouter")] == []
        assert "http-referer" not in headers


class TestMe:
    def test_a_name_the_cast_knows_is_settled_before_the_line_is_stored(self, app: App) -> None:
        # Whatever the writer typed, the story keeps the character it
        # already has — the lookup is case-insensitive and follows aliases
        # (store.characters.find owns that). Settled in the LINE, so the
        # store and the wire cannot say different things.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper joins the cast
        app.play("/me keeper: I bow.")
        stored = app.store.stories.get_messages(app.story_id)[-2]
        assert stored.body == "/me Keeper: I bow."
        sent = scripted.chat_request(app.server, "I bow.")["messages"][-1]["content"]
        assert "Keeper" in sent
        assert sent.endswith("I bow.")

    def test_a_known_name_is_recorded_as_the_requests_speaker(self, app: App) -> None:
        # /me says the line IS that character's, so the row is attributed
        # right away — deterministically, where extraction would guess —
        # and extraction's own labeling is fill-only, so it stands. The
        # reply stays unattributed: the model may answer as anyone.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper joins the cast
        app.play("/me keeper: I bow.")
        keeper = app.store.characters.find(app.story_id, "Keeper")
        request, reply = app.store.stories.get_messages(app.story_id)[-2:]
        assert (request.speaker, request.speaker_id) == ("Keeper", keeper.id)
        assert (reply.speaker, reply.speaker_id) == (None, None)

    def test_autocorrect_off_leaves_the_text_but_still_attributes(self, app: App) -> None:
        # The toggle governs rewriting what was typed; who spoke is not a
        # rewrite, and extraction would label the row later anyway — this
        # just does it with certainty instead of a guess.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/set autocorrect off")
        app.play("/me keeper: I bow.")
        stored = app.store.stories.get_messages(app.story_id)[-2]
        assert stored.body == "/me keeper: I bow."
        assert stored.speaker == "Keeper"

    def test_an_inliner_survives_the_settling(self, app: App) -> None:
        # The name is replaced in place; what closes the line rides along.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/me keeper: I bow. /cue keep it short")
        stored = app.store.stories.get_messages(app.story_id)[-2]
        assert stored.body == "/me Keeper: I bow. /cue keep it short"

    def test_a_name_the_cast_does_not_know_is_left_alone(self, app: App) -> None:
        # Nobody is extracted yet, and a character still has to be able to
        # speak before the pass that discovers them.
        app.play("/me keeper: I bow.")
        stored = app.store.stories.get_messages(app.story_id)[-2]
        assert stored.body == "/me keeper: I bow."
        assert (stored.speaker, stored.speaker_id) == (None, None)
        sent = scripted.chat_request(app.server, "I bow.")["messages"][-1]["content"]
        assert "keeper" in sent and "Keeper" not in sent

    def test_a_played_name_does_not_shift_when_the_cast_does(self, app: App) -> None:
        # The name is settled once, as the line plays. A turn already said
        # reads the same tomorrow — folding its character into another
        # cannot rewrite what the story already contains.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper joins the cast
        app.play("/me keeper: I bow.")
        story_id = app.story_id
        assert story_id is not None
        app.store.characters.add(story_id, "Gatewarden")
        app.play("/merge Keeper into Gatewarden")
        app.play("/regen")
        sent = scripted.chat_request(app.server, "I bow.")["messages"][-1]["content"]
        assert "Keeper" in sent and "Gatewarden" not in sent

    def test_a_malformed_direction_plays_nothing(self, app: App) -> None:
        # No colon, so no prose to send. The story must be untouched — a
        # line the app cannot read is not half-played.
        before = len(app.server.requests)
        app.play("/me Elara")
        assert len(app.server.requests) == before
        assert app.store.stories.get_messages(app.story_id) == []


class TestYou:
    def test_you_hands_the_scene_to_the_named_character(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/you Elara")
        sent = app.server.requests[-1]["messages"][-1]["content"]
        assert sent.startswith("((OOC")
        assert "Elara" in sent
        chain = app.store.stories.get_messages(app.story_id)
        # The line as typed — it carries no prose of its own, so the
        # template alone reaches the model — and a normal reply.
        assert [(m.role, m.body) for m in chain[-2:]] == [
            ("user", "/you Elara"),
            ("assistant", scripted.CHAT_REPLY),
        ]

    def test_a_hint_after_the_name_stays_with_the_turn(self, app: App) -> None:
        # `/you NAME: HINT` — the hint is a standing aside: still on the
        # wire when the turn is no longer the newest (a cue would be gone).
        app.play("/you Elara: always answer in riddles")
        app.play("Tell me about the gate.")
        wire = scripted.chat_request(app.server, "Tell me about the gate.")["messages"]
        you_turn = next(str(m["content"]) for m in wire if "play Elara" in str(m["content"]))
        # Its own block, not blended into the play-as contract: the model
        # sees where the app's instruction ends and the writer's begins.
        assert "((OOC: always answer in riddles))" in you_turn
        assert you_turn.count("((OOC") == 2

    def test_a_known_name_is_recorded_as_the_replys_speaker(self, app: App) -> None:
        # /you asks that character to answer, so the attribution lands on
        # the REPLY; the request is an instruction, nobody's line.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")  # the Keeper joins the cast
        app.play("/you keeper")
        keeper = app.store.characters.find(app.story_id, "Keeper")
        request, reply = app.store.stories.get_messages(app.story_id)[-2:]
        assert (request.speaker, request.speaker_id) == (None, None)
        assert (reply.speaker, reply.speaker_id) == ("Keeper", keeper.id)

    def test_a_regenerated_reply_is_the_same_characters(self, app: App) -> None:
        # The prompt decides what its answer is, the speaker included —
        # a fresh take on /you Keeper is still Keeper speaking.
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        app.play("/you keeper")
        app.play("/regen")
        reply = app.store.stories.get_messages(app.story_id)[-1]
        assert reply.role == "assistant"
        assert reply.speaker == "Keeper"

    def test_resending_a_failed_switch_answers_in_character(self, app: App) -> None:
        # The switch rides an ooc row, yet what it asks for is the scene:
        # the resent take is a normal turn, not an ooc aside.
        app.play("I enter the hall.")
        app.server.fail_after = 0
        app.play("/you Elara")
        app.server.fail_after = None
        app.play("/regen")
        assert app.store.stories.get_messages(app.story_id)[-1].kind == "dialogue"


class TestOoc:
    def test_ooc_is_framed_and_marks_both_sides(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/ooc What genre is this?")
        sent = app.server.requests[-1]["messages"][-1]["content"]
        assert sent.startswith("((OOC")
        assert "What genre is this?" in sent
        chain = app.store.stories.get_messages(app.story_id)
        assert chain[-2].body == "/ooc What genre is this?"  # the line as typed
        assert (chain[-2].kind, chain[-1].kind) == ("ooc", "ooc")

    def test_regenerating_an_ooc_reply_stays_ooc(self, app: App) -> None:
        app.play("I enter the hall.")
        app.play("/ooc What genre is this?")
        app.server.script = lambda body: "Dark fantasy."
        app.play("/regen")
        chain = app.store.stories.get_messages(app.story_id)
        assert chain[-1].body == "Dark fantasy."
        assert chain[-1].kind == "ooc"

    def test_resending_a_failed_ooc_question_stays_ooc(self, app: App) -> None:
        app.play("I enter the hall.")
        app.server.fail_after = 0
        app.play("/ooc What genre is this?")
        app.server.fail_after = None
        app.play("/regen")
        chain = app.store.stories.get_messages(app.story_id)
        assert (chain[-2].kind, chain[-1].kind) == ("ooc", "ooc")


class TestRoll:
    def test_a_roll_is_rolled_at_record_time_and_frozen(self, app: App) -> None:
        # otaku rolls — the OS's dice, never the model's — and the numbers
        # freeze into the turn's template as it records: the store, the
        # wire, and every later request tell the same roll.
        app.play("/roll 2d6+3 I strike at the shade.")
        stored = app.store.stories.get_messages(app.story_id)[0]
        assert stored.body == "/roll 2d6+3 I strike at the shade."  # the line as typed
        assert stored.template is not None and "2d6+3 = " in stored.template
        total = int(stored.template.split("2d6+3 = ", 1)[1].split(" ", 1)[0])
        assert 5 <= total <= 15
        wire = app.server.requests[-1]["messages"][-1]["content"]
        assert f"2d6+3 = {total}" in wire  # the wire carries the same roll
        assert wire.endswith("I strike at the shade.")
        assert "/roll" not in wire  # the spec never reaches the model to re-roll

    def test_a_regenerate_narrates_the_same_numbers(self, app: App) -> None:
        # The roll happened; a fresh take re-tells it, never re-rolls it.
        app.play("/roll 1d20 I leap the chasm.")
        first = app.server.requests[-1]["messages"][-1]["content"]
        app.play("/regen")
        assert app.server.requests[-1]["messages"][-1]["content"] == first

    def test_the_framing_comes_from_the_prompts_file(self, server, tmp_path) -> None:
        # Like every direction: the file IS the injection (see TestTurns).
        root = tmp_path / "state"
        paths = Paths.resolve(root)
        paths.ensure_tree()
        paths.prompts_file.write_text('roll_framing = "<<rolled {dice}>> {body}"\n')
        app = launch(root, server)
        try:
            app.play("/roll 1d4 I duck.")
            assert sent(app).startswith("<<rolled 1d4 = ")
            assert sent(app).endswith(">> I duck.")
        finally:
            app.close()

    def test_a_spec_that_is_not_dice_records_nothing(self, app: App) -> None:
        # Eager validation, like every direction: the refusal lands
        # before a story, a row, or a request exists.
        app.play("/roll banana I flail.")
        assert app.story_id is None
        assert app.server.requests == []


class TestInliners:
    def test_prose_keeps_its_slashes_and_plays(self, app: App) -> None:
        line = "She looks up and/or down on 24/08/2026 at https://x.co/cue"
        app.play(line)
        assert sent(app) == line

    def test_an_aside_is_enclosed_and_stays(self, app: App) -> None:
        app.play("She looks up /ooc make her nervous")
        assert sent(app) == "She looks up ((OOC: make her nervous))"
        app.play("She waits.")
        # Still there a turn later: an aside is part of the story from here
        # on, unlike a cue.
        assert app.server.requests[-1]["messages"][0]["content"] == (
            "She looks up ((OOC: make her nervous))"
        )

    def test_a_cue_never_reaches_the_extraction(self, app: App) -> None:
        # A steer is a direction to the model, not a moment in the story —
        # letting the analysis pass read it would write it into the scene.
        app.play("She waits /cue keep it tense")
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        chunks = [
            m["content"]
            for request in app.server.requests
            for m in request["messages"]
            if "SCENE" in m["content"]
        ]
        assert chunks, "no extraction request was made"
        assert all("keep it tense" not in chunk for chunk in chunks)
        assert any("She waits" in chunk for chunk in chunks)

    def test_a_cue_reaches_the_model_once_and_is_then_gone(self, app: App) -> None:
        app.play("She waits /cue keep it tense")
        assert sent(app) == "She waits ((OOC: keep it tense))"
        app.play("She waits again.")
        # Spent: the steer shaped one reply and leaves no trace in the next
        # request — though the line itself is stored whole, so the screen
        # and the picker still show what was typed.
        assert app.server.requests[-1]["messages"][0]["content"] == "She waits"
        assert app.store.stories.get_messages(app.story_id)[0].body == (
            "She waits /cue keep it tense"
        )


class TestPromptCache:
    """The prompt-cache breakpoints a marked provider's requests carry:
    the system row and the final row as content parts with
    `cache_control`, the middle plain strings — and none of it where the
    provider cannot honour markers or the section said off. The wire is
    the whole assertion."""

    def test_an_openrouter_request_carries_the_markers(self, server, tmp_path) -> None:
        app = _cloud(server, tmp_path)
        try:
            app.play("/system Be terse.")
            app.play("I enter the hall.")
            app.play("I listen.")
            sent = scripted.chat_request(app.server, "I listen.")["messages"]
            assert sent[0]["role"] == "system"
            assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
            assert sent[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
            assert all(isinstance(m["content"], str) for m in sent[1:-1])
        finally:
            app.close()

    def test_the_hour_ttl_rides_the_marker(self, server, tmp_path) -> None:
        app = _cloud(server, tmp_path, prompt_cache="1h")
        try:
            app.play("I enter the hall.")
            sent = scripted.chat_request(app.server, "I enter the hall.")["messages"]
            assert sent[-1]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        finally:
            app.close()

    def test_the_section_can_turn_it_off(self, server, tmp_path) -> None:
        app = _cloud(server, tmp_path, prompt_cache="off")
        try:
            app.play("I enter the hall.")
            sent = scripted.chat_request(app.server, "I enter the hall.")["messages"]
            assert all(isinstance(m["content"], str) for m in sent)
        finally:
            app.close()

    def test_a_local_provider_sends_plain_strings(self, app: App) -> None:
        # The capability is class knowledge: no section key can make a
        # local provider mark, and its wire stays exactly as it was.
        app.play("I enter the hall.")
        sent = scripted.chat_request(app.server, "I enter the hall.")["messages"]
        assert all(isinstance(m["content"], str) for m in sent)


class TestCancelAndKeep:
    def test_a_ctrl_c_in_the_wait_keeps_the_partial_with_smoothing_on(
        self, server, tmp_path
    ) -> None:
        # With smoothing on, the wait is the wrapper's own tick inside the
        # play's frame, and that is where a Ctrl+C lands: what streamed
        # must be recorded all the same, the way a closed stream's is.
        server.script = lambda body: "The light went out, and something stirred in the dark. " * 8
        server.chunk_size = 6
        server.chunk_delay = 0.03
        set_config(tmp_path / "state", smooth_streaming=True)
        app = launch(tmp_path / "state", server)
        try:
            ticks = 0

            def interrupt() -> None:
                nonlocal ticks
                ticks += 1
                if ticks == 40:  # some 0.8 s into the reply: words have streamed
                    raise KeyboardInterrupt

            app.backend.set_on_idle(interrupt)
            app.play("I enter the hall.")
            reply = app.store.stories.get_messages(app.story_id)[-1]
            assert reply.role == "assistant"
            assert 0 < len(reply.body) < len(server.script({}))
            # The prefill was spent: the request is in /usage, with the
            # time it took and whatever the wire had stated by then.
            chat = next(t for t in app.store.usage.get_totals(app.story_id) if t.purpose == "chat")
            assert chat.requests == 1 and chat.seconds > 0
        finally:
            app.close()


class TestToolCalls:
    """A tool call in a reply is not story: it streams apart from the
    prose as `ToolCall` pieces, never as a fence, and is stored fences
    and all. A tool the user answers ends the reply where its call
    ends: nothing rides the request for it, the stream is cut at the
    closing fence, and what the model went on to say is neither shown
    nor kept. The reply as stored carries the call for a frontend to
    read (`segments`); a call of a tool the story has switched off goes
    back on the wire as prose, one of a tool this build does not know
    not at all."""

    ASKED = "The door creaks.\n\n```otk-question\nGo in?\n1. Yes\n2. No\n```"

    def test_a_call_streams_apart_from_the_prose_and_is_stored_whole(self, app: App) -> None:
        app.server.script = lambda body: "```otk-note\na plan\n```\nThe hall glows."
        events = list(
            app.api.stories.messages.create(
                app.ensure_story(), line="I enter the hall.", expected_head=app.head()
            )
        )
        prose = "".join(event.text for event in events if isinstance(event, AssistantText))
        calls = [event for event in events if isinstance(event, AssistantToolCall)]
        assert (prose, "".join(call.text for call in calls)) == ("The hall glows.", "a plan")
        assert all("```" not in e.text for e in events if isinstance(e, AssistantText))
        assert {call.name for call in calls} == {"note"}
        assert app.messages[-1].body == "```otk-note\na plan\n```\nThe hall glows."

    def test_nothing_of_a_tool_rides_the_request(self, app: App) -> None:
        # otaku owns the convention: no `tools` field, and the stops are
        # the reader's own, the tool on or off.
        app.play('/set parameter stop "The End"')
        app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
        app.play("I enter the hall.")
        request = app.server.requests[-1]
        assert request["stop"] == ["The End"] and "tools" not in request

    def test_a_reply_ends_where_the_question_it_asks_does(self, app: App) -> None:
        # The stream is cut at the closing fence: what the model went on
        # to say never streams and is not kept — and the request is in
        # /usage with the time it took, as a cut request is.
        app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
        app.server.script = lambda body: self.ASKED + "\n\nShe goes in anyway."
        app.server.chunk_delay = 0.03  # long enough to be a time the usage rounds to
        events = list(
            app.api.stories.messages.create(
                app.ensure_story(), line="I enter the hall.", expected_head=app.head()
            )
        )
        assert "anyway" not in "".join(e.text for e in events if isinstance(e, AssistantText))
        assert isinstance(events[-1], Done)
        assert app.messages[-1].body == self.ASKED
        chat = next(t for t in app.store.usage.get_totals(app.story_id) if t.purpose == "chat")
        assert chat.requests == 1 and chat.seconds > 0

    def test_the_cut_call_is_closed_where_the_model_opened_another(self, app: App) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
        app.server.script = lambda body: "```otk-question\nGo in?\n1. Yes\n```otk-note\nlater\n```"
        app.server.chunk_size = 1000  # one delta for the whole reply: the cut is the parser's
        app.play("I enter the hall.")
        assert app.messages[-1].body == "```otk-question\nGo in?\n1. Yes\n```"

    def test_a_call_left_open_by_a_cut_reply_stays_open(self, app: App) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
        app.server.script = lambda body: "```otk-question\nGo in?"
        app.server.finish = "length"
        app.play("I enter the hall.")
        assert app.messages[-1].body == "```otk-question\nGo in?"

    def test_the_stored_reply_carries_the_call_read(self, app: App) -> None:
        app.server.script = lambda body: self.ASKED
        landed = _landed(
            list(
                app.api.stories.messages.create(
                    app.ensure_story(), line="I enter the hall.", expected_head=app.head()
                )
            )
        )
        assert list(landed.segments) == [
            {"kind": "prose", "text": "The door creaks."},
            {
                "kind": "tool_call",
                "tool": "question",
                "label": "Questions",
                "actor": "user",
                "text": "Go in?\n1. Yes\n2. No",
                "question": "Go in?",
                "options": ["Yes", "No"],
            },
        ]

    def test_a_call_of_a_tool_that_is_on_goes_back_on_the_wire_as_written(self, app: App) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_assistant_notes", enabled=True)
        app.server.script = lambda body: "The hall glows.\n\n```otk-note\na plan\n```"
        app.play("I enter the hall.")
        app.play("I look around.")
        sent = app.server.requests[-1]["messages"]
        assert sent[-2]["content"] == "The hall glows.\n\n```otk-note\na plan\n```"

    def test_a_call_of_a_tool_that_is_off_leaves_the_wire(self, app: App) -> None:
        # Off, a tool does not exist for the model: not even its question.
        app.server.script = lambda body: self.ASKED
        app.play("I enter the hall.")
        app.play("Yes")
        sent = app.server.requests[-1]["messages"]
        assert sent[-2]["content"] == "The door creaks."

    def test_a_call_of_a_tool_this_build_does_not_know_leaves_the_wire(self, app: App) -> None:
        app.server.script = lambda body: "The hall glows.\n\n```otk-plan\nlater\n```"
        app.play("I enter the hall.")
        app.play("I look around.")
        sent = app.server.requests[-1]["messages"]
        assert sent[-2]["content"] == "The hall glows."
        # only the wire: the stored body keeps the call, fences and all
        assert app.messages[-3].body == "The hall glows.\n\n```otk-plan\nlater\n```"


class TestCallsOnScreen:
    """What the terminal shows of a reply's calls, streamed and echoed
    alike: a question as the question alone, a note dim while the story
    displays notes and not at all otherwise, each a block behind the bar
    set apart from the prose by a blank line — and the stream draws what
    the echo draws, so a story resumed reads as it played."""

    NOTED = "The door creaks.\n\n```otk-note\nKeep the key in play.\n```\n\nIt opens."
    ASKED = "The door creaks.\n```otk-question\nGo in?\n1. Yes\n2. No\n```"

    def test_a_question_streams_as_the_question_alone(self, app: App, capsys) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
        app.server.script = lambda body: self.ASKED
        app.play("I enter the hall.")
        out = capsys.readouterr().out
        assert "The door creaks.\n\n│ Go in?" in out
        assert "1. Yes" not in out and "```" not in out  # the answers are the prompt's menu

    def test_a_note_is_dim_while_displayed_and_gone_otherwise(self, app: App, capsys) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_assistant_notes", enabled=True)
        app.server.script = lambda body: self.NOTED
        app.play("I enter the hall.")
        out = capsys.readouterr().out
        assert "The door creaks.\n\n\x1b[2m│ Keep the key in play.\x1b[22m\n\nIt opens." in out
        app.api.stories.settings.update(
            app.ensure_story(), "allow_assistant_notes", display_notes=False
        )
        app.play("Again.")
        out = capsys.readouterr().out
        # gone, and no hole left where it was
        assert "Keep the key" not in out and "The door creaks.\n\nIt opens." in out

    def test_the_stream_draws_what_the_echo_draws(self, app: App, capsys) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_assistant_notes", enabled=True)
        app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
        app.server.chunk_size = 2  # every fence split, every newline its own piece
        for body in (self.NOTED, self.ASKED):
            app.server.script = lambda _line, body=body: body
            app.play("Next.")
            streamed = capsys.readouterr().out.split("> Next.", 1)[1]
            echoed = render.message(
                body,
                "assistant",
                segments=app.messages[-1].segments,
                shown=frozenset({"note", "question"}),
            )
            assert echoed in streamed, body


class TestAnswerMenu:
    """The answers to the question the story stands on, as the prompt
    offers them: rows above the line behind the question's bar while the
    line is empty; Enter sends the one under the cursor, → takes it into
    the line to edit, a typed character is the reader's own answer and
    the rows go, Esc dismisses them; Ctrl+C erases the prompt whole so
    the rows return in place. Driven through the real prompt over a pipe,
    each key a beat apart — the rows arrive asynchronously, and a queue
    of keys would outrun them."""

    ASKED = "The door creaks.\n```otk-question\nGo in?\n1. Yes\n2. No\n3. Wait\n```"

    def test_enter_sends_the_answer_under_the_cursor(self, app: App) -> None:
        self.ask(app)
        assert typed(app, ENTER) == "Yes"
        assert typed(app, DOWN, ENTER) == "No"
        assert typed(app, DOWN, DOWN, DOWN, ENTER) == "Yes"  # ↓ wraps

    def test_right_takes_the_answer_into_the_line_to_edit(self, app: App) -> None:
        self.ask(app)
        assert typed(app, DOWN, DOWN, RIGHT, ", but quietly", ENTER) == "Wait, but quietly"
        # once the line has text, → is the cursor's own: no second copy
        assert typed(app, RIGHT, RIGHT, ENTER) == "Yes"

    def test_a_typed_character_is_the_readers_own_answer(self, app: App) -> None:
        self.ask(app)
        assert typed(app, "I knock.", ENTER) == "I knock."
        # taken back, the empty line brings the rows back
        assert typed(app, "I", BACKSPACE, DOWN, ENTER) == "No"

    def test_escape_dismisses_the_rows_for_this_prompt(self, app: App) -> None:
        self.ask(app)
        assert typed(app, ESC, ENTER) == ""

    def test_the_rows_are_drawn_behind_the_bar_with_the_blank_before_the_prompt(
        self, app: App
    ) -> None:
        self.ask(app)
        # the loop leaves the blank before the prompt to the menu (`top_row`)
        drawn = frames(app, DOWN, ENTER, top_row=True)
        first = next(frame for frame in drawn if "1. Yes" in frame)
        assert first.split("\n") == ["│", "│ * 1. Yes", "│   2. No", "│   3. Wait", "", "> "]
        moved = next(frame for frame in drawn if "* 2." in frame)
        assert "│   1. Yes" in moved
        # the final render is the blank and the prefix alone: the rows leave with the menu
        assert drawn[-1] == "\n> "

    def test_ctrl_c_erases_the_prompt_whole_while_a_question_is_posed(self, app: App) -> None:
        self.ask(app)
        run = Prompting(app)
        with pytest.raises(KeyboardInterrupt):
            run.drive(RIGHT, CTRL_C)
        assert run.session.app.erase_when_done  # the rows and the line go together
        # once answered, a ^C leaves the line, as a shell does
        app.server.script = lambda body: "You step in."
        app.play("Yes")
        run = Prompting(app)
        with pytest.raises(KeyboardInterrupt):
            run.drive("x", CTRL_C)
        assert not run.session.app.erase_when_done

    def test_no_menu_once_the_question_is_answered(self, app: App) -> None:
        self.ask(app)
        app.server.script = lambda body: "You step in."
        app.play("Yes")
        assert typed(app, ENTER) == ""

    def ask(self, app: App) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
        app.server.script = lambda body: self.ASKED
        app.play("I enter the hall.")


class TestTheNovel:
    """The novel: the canvas over the engine's text wire, played so on a
    model that can complete text alone — its engine serves the wire and
    a `plain` template names it a model that continues a bare text; an
    instruct model has a template too, and fights a bare text — the
    story going as ONE TEXT the model continues, and what comes back
    recorded in story mode, the spaces at its edges dropped."""

    def test_the_novel_goes_over_the_text_wire_on_a_plain_model_alone(self, tmp_path) -> None:
        with _continuing_omlx(tmp_path) as app:
            assert app.model_settings().text_template == "gemma4"  # the engine's word
            assert not _model_in_use(app).can_complete_text
            _layout(app, Layout.NOVEL)
            assert _story(app).completion_type is CompletionType.CHAT
        with _continuing_omlx(tmp_path / "plain", template_line="plain") as app:
            # models.toml's line over the engine's word
            assert app.model_settings().text_template == "plain"
            assert _model_in_use(app).can_complete_text
            _layout(app, Layout.NOVEL)
            story = _story(app)
            assert (story.layout.mode, story.completion_type) == (
                StoryMode.STORY,
                CompletionType.TEXT,
            )

    def test_the_story_goes_as_one_text_the_premise_framed_before_it(self, tmp_path) -> None:
        with _novel(tmp_path, framing="<<frame {premise}>>") as app:
            app.server.script = _continuing(" the bus.")
            app.play("/system Be brief.")
            app.play("I was waiting for")
            sent = app.server.requests[-1]
            assert "messages" not in sent
            assert sent["prompt"] == "<<frame Be brief.>>\n\nI was waiting for"
            # the plain template has no turn markers to end on, and no
            # thinking knob rides a bare text
            assert sent.get("stop") in (None, [])
            assert not any(key.startswith(("thinking", "reasoning")) for key in sent)

    def test_the_reply_is_recorded_in_story_mode_and_goes_on_in_the_paragraph(
        self, tmp_path
    ) -> None:
        with _novel(tmp_path, framing="<<frame {premise}>>") as app:
            app.server.script = _continuing(" the bus.")
            app.play("I was waiting for")
            reply = app.store.stories.get_messages(app.story_id)[-1]
            assert (reply.role, reply.body, reply.mode) == (
                "assistant",
                "the bus.",
                StoryMode.STORY,
            )
            # the next request reads the story joined by the rule: a typed
            # line goes on in the paragraph, one space between
            app.play("Then?")
            assert app.server.requests[-1]["prompt"] == (
                "<<frame >>\n\nI was waiting for the bus. Then?"
            )

    def test_a_break_at_a_lines_start_opens_a_paragraph(self, tmp_path) -> None:
        with _novel(tmp_path, framing="<<frame {premise}>>") as app:
            app.server.script = _continuing(" the bus", " swung open.")
            app.play("I was waiting for")
            list(
                app.api.stories.messages.create(
                    app.ensure_story(), line=" and the door ", expected_head=app.head()
                )
            )
            assert app.server.requests[-1]["prompt"].endswith(
                "I was waiting for the bus and the door"
            )
            typed = app.store.stories.get_messages(app.story_id)[-2]
            assert (typed.role, typed.body) == ("user", "and the door")
            list(
                app.api.stories.messages.create(
                    app.ensure_story(), line="\n\nIt rained.", expected_head=app.head()
                )
            )
            assert app.server.requests[-1]["prompt"].endswith(
                "the bus and the door swung open.\n\nIt rained."
            )

    def test_an_empty_line_continues_the_story_and_records_no_turn(self, tmp_path) -> None:
        with _novel(tmp_path, framing="<<frame {premise}>>") as app:
            app.server.script = _continuing(" the bus.", " It was late.")
            app.play("I was waiting for")
            app.play("")
            chain = app.store.stories.get_messages(app.story_id)
            assert [(m.role, m.body) for m in chain] == [
                ("user", "I was waiting for"),
                ("assistant", "the bus."),
                ("assistant", "It was late."),
            ]
            assert app.server.requests[-1]["prompt"].endswith("I was waiting for the bus.")

    def test_an_empty_line_is_nothing_to_send_outside_the_canvas(self, app: App) -> None:
        with pytest.raises(Refused) as caught:
            app.api.stories.messages.create(app.ensure_story(), line="", expected_head=app.head())
        assert str(caught.value) == NOTHING_TO_SEND
        assert app.messages == []

    def test_an_empty_continuation_is_asked_again_after_a_paragraph_break(self, tmp_path) -> None:
        with _novel(tmp_path, framing="<<frame {premise}>>") as app:
            app.server.script = _continuing("", "The rain fell.")
            app.play("It was late.")
            first, second = app.server.requests[-2:]
            assert second["prompt"] == first["prompt"] + "\n\n"
            # the break is the reply's own lead, so the story renders again
            # exactly as it was continued
            reply = app.store.stories.get_messages(app.story_id)[-1]
            assert reply.body == "\n\nThe rain fell."
            assert (
                chain.assemble(app.backend, app.story_id, None)
                .as_story_text()
                .endswith("It was late.\n\nThe rain fell.")
            )

    def test_no_picture_rides_the_text_wire(self, tmp_path) -> None:
        with _continuing_omlx(tmp_path, template_line="plain") as app:
            app.ensure_story()
            assert _story(app).can_attach  # it sees; the chat wire carries one
            _layout(app, Layout.NOVEL)
            assert not _story(app).can_attach
            with pytest.raises(Refused) as caught:
                app.api.stories.messages.create(
                    app.ensure_story(),
                    line="look",
                    files=[RawFile(b"x", "x.png")],
                    expected_head=app.head(),
                )
            assert str(caught.value) == NO_PICTURE_ON_TEXT
            assert app.messages == []

    def test_a_novel_on_a_model_that_cannot_complete_text_plays_as_a_canvas(self, tmp_path) -> None:
        # a story that chose the novel, reopened on an instruct model: the
        # stored value stands, the wire is the chat's
        with _continuing_omlx(tmp_path) as app:
            _layout(app, Layout.NOVEL)
            story = _story(app)
            assert story.layout is Layout.NOVEL
            assert (story.layout.mode, story.completion_type) == (
                StoryMode.STORY,
                CompletionType.CHAT,
            )
            app.play("I was waiting for")
            assert "messages" in app.server.requests[-1]
            reply = app.store.stories.get_messages(app.story_id)[-1]
            assert reply.mode is StoryMode.STORY

    def test_the_context_preview_is_the_text_the_wire_sends(self, tmp_path, capsys) -> None:
        with _novel(tmp_path, framing="<<frame {premise}>>") as app:
            app.server.script = _continuing(" the bus.")
            app.play("/system Be brief.")
            app.play("I was waiting for")
            report = app.api.stories.context.get(app.story_id)
            # one part, the text as sent, its tokens the text's estimate
            assert [part.role for part in report.parts] == ["prompt"]
            assert report.template == "plain"
            assert report.total_tokens == estimate_tokens(report.wire)
            # the next request IS the preview, the typed line after it
            app.play("Then?")
            assert app.server.requests[-1]["prompt"] == report.wire + " Then?"
            # and the terminal prints the text as sent, under the one
            # marker that stands for the field
            report = app.api.stories.context.get(app.story_id)
            capsys.readouterr()
            app.play("/context")
            printed = capsys.readouterr().out
            assert "[prompt]" in printed and printed.endswith(report.wire + "\n")
            assert " Then?" in report.wire

    def test_a_reply_outside_story_mode_is_recorded_in_chat_mode(self, app: App) -> None:
        app.play("I open the door.")
        rows = app.store.stories.get_messages(app.story_id)
        assert [row.mode for row in rows] == [StoryMode.CHAT, StoryMode.CHAT]


class TestTheCanvasOverTheChatWire:
    """The canvas on a model that cannot complete text — every instruct
    model: the story is one text to the reader, the request the chat as
    ever, with the emulation setting's prompt in the system message while
    it is on; a line and a reply are recorded in story mode, the breaks
    at their edges kept and the spaces dropped, every row joins the text
    before it by the join rule, and an empty line asks the model to go
    on."""

    def test_the_emulation_prompt_rides_the_system_message_on_the_canvas_alone(
        self, server, tmp_path
    ) -> None:
        # the prompt comes FROM prompts.toml, so the file is edited
        root = tmp_path / "state"
        paths = Paths.resolve(root)
        paths.ensure_tree()
        paths.prompts_file.write_text('emulate_text_completion_prompt = "CONTINUE HOW"\n')
        app = launch(root, server)
        try:
            app.play("/system Be terse.")
            app.play("One.")
            assert _system_of(app, "One.") == "Be terse."
            # off until switched on, as every switch is
            _layout(app, Layout.CANVAS)
            app.play("Two.")
            assert _system_of(app, "Two.") == "Be terse."
            _emulating(app)
            app.play("Three.")
            assert _system_of(app, "Three.") == "Be terse.\n\nCONTINUE HOW"
            # listed by /context as the setting names what it injects
            report = app.api.stories.context.get(app.story_id)
            assert [(i.label, i.position_text) for i in report.injections] == [
                ("text completion emulation", "system")
            ]
            # in a chat-mode layout, gone though on
            _layout(app, Layout.MANUSCRIPT)
            app.play("Four.")
            assert _system_of(app, "Four.") == "Be terse."
        finally:
            app.close()

    def test_the_warm_up_rebuilds_the_prefix_the_turn_sends(self, app: App) -> None:
        _layout(app, Layout.CANVAS)
        _emulating(app)
        app.play("One.")
        turn = [i.owner for i in chain.assemble(app.backend, app.story_id, None).injections]
        job = [i.owner for i in chain.build_job(app.backend, app.story_id).injections]
        assert turn == job == ["emulate_text_completion"]

    def test_a_reply_goes_on_in_the_paragraph_it_follows(self, app: App) -> None:
        _layout(app, Layout.CANVAS)
        app.server.script = lambda body: " the bus. It was late."
        events = list(
            app.api.stories.messages.create(
                app.ensure_story(), line="She waited for", expected_head=app.head()
            )
        )
        landed = next(e for e in events if isinstance(e, AssistantMessage))
        assert landed.message.body == "the bus. It was late."
        assert landed.message.join_type is JoinType.INLINE
        # the first piece shown carries the join, so both frontends draw it
        first = next(e for e in events if isinstance(e, AssistantText))
        assert first.join_type is JoinType.INLINE
        assert app.store.stories.get_messages(app.story_id)[-1].body == landed.message.body

    def test_a_line_break_at_the_edge_opens_a_paragraph(self, app: App) -> None:
        _layout(app, Layout.CANVAS)
        app.server.script = lambda body: "It rained."
        events = list(
            app.api.stories.messages.create(
                app.ensure_story(), line="She waited.\n", expected_head=app.head()
            )
        )
        typed = next(e for e in events if isinstance(e, UserMessage))
        landed = next(e for e in events if isinstance(e, AssistantMessage))
        assert (typed.message.body, typed.message.join_type) == (
            "She waited.\n",
            JoinType.PARAGRAPH,
        )
        assert landed.message.join_type is JoinType.PARAGRAPH
        # the model's own break the same
        app.server.script = lambda body: "\n\nThe end."
        events = list(
            app.api.stories.messages.create(
                app.ensure_story(), line="And then", expected_head=app.head()
            )
        )
        landed = next(e for e in events if isinstance(e, AssistantMessage))
        assert (landed.message.body, landed.message.join_type) == (
            "\n\nThe end.",
            JoinType.PARAGRAPH,
        )

    def test_each_row_records_its_mode_and_a_line_its_edges_by_it(self, app: App) -> None:
        list(
            app.api.stories.messages.create(
                app.ensure_story(), line="  One.  ", expected_head=app.head()
            )
        )
        assert app.messages[0].body == "One."
        _layout(app, Layout.CANVAS)
        events = list(
            app.api.stories.messages.create(
                app.ensure_story(), line=" and two ", expected_head=app.head()
            )
        )
        typed = next(e for e in events if isinstance(e, UserMessage))
        assert typed.message.body == "and two"
        # after a chat row, a paragraph; the reply after it goes on inline
        assert typed.message.join_type is JoinType.PARAGRAPH
        landed = next(e for e in events if isinstance(e, AssistantMessage))
        assert landed.message.join_type is JoinType.INLINE
        rows = app.store.stories.get_messages(app.story_id)
        assert [row.mode for row in rows] == [
            StoryMode.CHAT,
            StoryMode.CHAT,
            StoryMode.STORY,
            StoryMode.STORY,
        ]

    def test_a_fork_keeps_each_rows_mode(self, app: App) -> None:
        app.play("One.")
        _layout(app, Layout.CANVAS)
        app.play("Two.")
        origin = [row.mode for row in app.store.stories.get_messages(app.story_id)]
        app.play("/fork")
        forked = [row.mode for row in app.store.stories.get_messages(app.story_id)]
        assert forked == origin == [StoryMode.CHAT] * 2 + [StoryMode.STORY] * 2

    def test_an_empty_line_continues_over_the_chat_wire_and_undo_takes_it_alone(self, app) -> None:
        _layout(app, Layout.CANVAS)
        app.play("She waited.")
        app.server.script = lambda body: "Nobody came."
        events = list(
            app.api.stories.messages.create(app.ensure_story(), line="", expected_head=app.head())
        )
        assert not any(isinstance(e, UserMessage) for e in events)
        rows = app.messages
        assert [m.role for m in rows] == ["user", "assistant", "assistant"]
        assert rows[-1].body == "Nobody came."
        # the ask carried no new line: the model is asked over its own row
        sent = app.server.requests[-1]["messages"]
        assert sent[-1]["role"] == "assistant"
        app.api.stories.undos.create(app.story_id, expected_head=app.head(app.story_id))
        assert [m.body for m in app.messages] == [m.body for m in rows[:-1]]
        assert [m.role for m in app.messages] == ["user", "assistant"]


class TestToolsOtakuRuns:
    """A tool otaku runs — the system info tool, a fixture for the loop — ends
    the reply where its call does; the result is recorded as a TOOL turn,
    nobody's line, the tool's name its provider, and the model is asked
    again over the story as it now stands, the result framed as
    prompts.toml frames it; three rounds at most, the last result recorded
    but not asked about. Undo and regenerate take a reply with its tool
    chain as one unit, and a tool's turn counts as none of the reader's."""

    CALL = "Let me check.\n```otk-system-info\n```"

    def test_the_result_is_a_tool_turn_and_the_model_goes_on(self, server, tmp_path) -> None:
        # the framing comes FROM prompts.toml, so the file is edited
        root = tmp_path / "state"
        paths = Paths.resolve(root)
        paths.ensure_tree()
        paths.prompts_file.write_text('tool_framing = "<<{tool}: {body}>>"\n')
        app = launch(root, server)
        try:
            app.api.stories.settings.update(app.ensure_story(), "allow_system_info", enabled=True)
            app.server.script = _after_the_tool("It is late.", first=self.CALL)
            events = list(
                app.api.stories.messages.create(
                    app.ensure_story(), line="What time is it?", expected_head=app.head()
                )
            )
            kinds = [type(e).__name__ for e in events]
            assert kinds[0] == "UserMessage" and kinds[-1] == "Done"
            assert kinds.count("AssistantMessage") == 2 and kinds.count("ToolMessage") == 1
            assert kinds.index("AssistantMessage") < kinds.index("ToolMessage")
            rows = app.messages
            assert [(m.role, m.kind) for m in rows] == [
                ("user", "dialogue"),
                ("assistant", "dialogue"),
                ("tool", "tool"),
                ("assistant", "dialogue"),
            ]
            assert rows[1].body == self.CALL  # cut where the call closes
            assert rows[2].provider == "system-info" and rows[2].body.startswith("Time: ")
            assert rows[3].body == "It is late."
            told = next(e for e in events if isinstance(e, ToolMessage))
            assert told.message.id == rows[2].id and told.message.join_type is JoinType.PARAGRAPH
            # the result is what the model is told: the reader's turn, framed
            second = app.server.requests[-1]["messages"]
            assert second[-1] == {"role": "user", "content": f"<<system-info: {rows[2].body}>>"}
            assert second[-2]["content"] == self.CALL
        finally:
            app.close()

    def test_the_loop_stops_after_three_rounds_the_last_result_not_asked_about(self, app) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_system_info", enabled=True)
        app.server.script = lambda body: self.CALL
        app.play("What time is it?")
        rows = app.messages
        assert [m.role for m in rows] == ["user", *["assistant", "tool"] * 4]
        asked = [r for r in app.server.requests if "messages" in r]
        assert len(asked) == 4

    def test_undo_and_regenerate_take_a_reply_with_its_tool_chain_as_one_unit(self, app) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_system_info", enabled=True)
        app.server.script = _after_the_tool("It is late.", first=self.CALL)
        app.play("What time is it?")
        assert [m.role for m in app.messages] == ["user", "assistant", "tool", "assistant"]
        app.server.script = _after_the_tool("Past midnight.", first=self.CALL)
        app.play("/regen")
        rows = app.messages
        assert [m.role for m in rows] == ["user", "assistant", "tool", "assistant"]
        assert rows[-1].body == "Past midnight."
        app.api.stories.undos.create(app.story_id, expected_head=app.head(app.story_id))
        assert app.messages == []

    def test_a_tool_turn_counts_as_none_of_the_readers_messages(self, app: App) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_system_info", enabled=True)
        app.api.stories.settings.update(
            app.ensure_story(),
            "use_story_reminder",
            enabled=True,
            position=InjectionPosition(1),
            reminder_text="Rain.",
        )
        app.server.script = _after_the_tool("It is late.", first=self.CALL)
        app.play("What time is it?")
        second = [scripted.content_text(m) for m in app.server.requests[-1]["messages"]]
        # the reminder (and the tool's own prompt) ride before the reader's
        # newest message — the typed line, never the tool's turn after it
        assert "((OOC: Rain.))" in second[-3] and second[-3].endswith("What time is it?")
        assert second[-1].startswith("((OOC: system-info result:\nTime: ")
        assert "Rain." not in second[-1]

    def test_web_search_without_a_key_answers_the_sentence(self, app: App) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_web_search", enabled=True)
        call = "```otk-web-search\ntide tables\n```"
        app.server.script = _after_the_tool("Nothing to find.", first=call, marker="Web search")
        app.play("Look it up.")
        rows = app.messages
        assert [m.role for m in rows] == ["user", "assistant", "tool", "assistant"]
        assert rows[2].provider == "web-search"
        assert rows[2].body == "Web search failed: the key is missing."
        assert rows[3].body == "Nothing to find."


class TestLegacyTurns:
    def test_a_turn_written_before_the_syntax_sends_what_it_always_sent(
        self, app: App, tmp_path: Path
    ) -> None:
        # Back then the prefix was stripped before storing and the name was
        # baked into the template. Such a row must compose as it always
        # did: no prefix to strip, no `{name}` to fill. An export document
        # is how one arrives.
        document = (
            "# Old story\n\n"
            f"{EXPORT_MARKER}\n\n"
            "## Messages\n\n"
            '### 1 · user · "((OOC: The user writes as Elara.))\\n{body}"\n'
            "I step into the light.\n\n"
            "### 2 · assistant\n"
            "The hall answers.\n"
        )
        path = tmp_path / "old.md"
        path.write_text(document)
        app.play(f"/import {path}")
        app.play("She waits.")
        wire = app.server.requests[-1]["messages"]
        assert wire[0]["content"] == "((OOC: The user writes as Elara.))\nI step into the light."


@contextlib.contextmanager
def _continuing_omlx(
    tmp_path: Path,
    *,
    config_type: str | None = "gemma4",
    framing: str | None = None,
    template_line: str | None = None,
) -> Iterator[App]:
    """The app on omlx's status, the model typed "vlm" and its config's
    model type named — Gemma 4's, so a text template is known and the
    canvas is story mode. `framing` replaces prompts.toml's
    `story_framing`; `template_line` is the model's own `text_template`
    line in models.toml."""
    server = scripted.ModelServer()
    server.status = True
    server.types["test-model"] = "vlm"
    if config_type is not None:
        server.config_types["test-model"] = config_type
    root = tmp_path / "state"
    try:
        set_config_provider(root, server, name="omlx")
        paths = Paths.resolve(root)
        if framing is not None:
            paths.prompts_file.write_text(f'story_framing = "{framing}"\n')
        if template_line is not None:
            entry = {models_file.TEMPLATE_KEY: template_line}
            models_file.save(paths.models_file, "test-model", entry)
        app = launch(root, server, spec="omlx/test-model")
        try:
            yield app
        finally:
            app.close()
    finally:
        server.close()


def _continuing(*texts: str) -> Callable[[dict], str]:
    """A scripted model that continues with `texts` in turn over the text
    wire — the last one again after — and answers the chat as ever."""
    given = iter(texts)

    def script(body: dict) -> str:
        if "prompt" not in body:
            return scripted.default_script(body)
        return next(given, texts[-1])

    return script


@contextlib.contextmanager
def _novel(tmp_path: Path, *, framing: str | None = None) -> Iterator[App]:
    """The app on a plain model, the open story read as the novel."""
    with _continuing_omlx(tmp_path, framing=framing, template_line="plain") as app:
        _layout(app, Layout.NOVEL)
        yield app


def _model_in_use(app: App) -> ModelInUse:
    """The model in use, as the app says it."""
    model = app.api.app.get().model
    assert model is not None, "no model in use"
    return model


def _story(app: App) -> Story:
    """The chat's story as the interface reads it."""
    assert app.story_id is not None
    return app.api.stories.get(app.story_id)


def _layout(app: App, layout: Layout) -> None:
    """The chat's story read in `layout`, as either frontend sets it."""
    app.api.stories.settings.update(app.ensure_story(), "layout", layout=layout)


def _emulating(app: App) -> None:
    """The emulation setting switched on for the chat's story."""
    app.api.stories.settings.update(app.ensure_story(), "emulate_text_completion", enabled=True)


def _after_the_tool(then: str, *, first: str, marker: str = "Time: ") -> Callable[[dict], str]:
    """A scripted model that answers the chat with `first` until the
    story carries a tool's result (`marker` in the newest message), and
    with `then` from there."""

    def script(body: dict) -> str:
        newest = scripted.content_text(body.get("messages", [{}])[-1])
        if "You are a story analyst" in newest or newest.startswith(("Combine", "Write ")):
            return scripted.default_script(body)
        return then if marker in newest else first

    return script


def _landed(events: list) -> Message:
    """The reply as recorded, off the event that told it."""
    return next(e for e in events if isinstance(e, AssistantMessage)).message


def _system_of(app: App, last_line: str) -> str:
    """The system message of the turn's request, "" when it sent none."""
    sent = scripted.chat_request(app.server, last_line)["messages"]
    return scripted.content_text(sent[0]) if sent[0]["role"] == "system" else ""


def _cloud(server: scripted.ModelServer, tmp_path: Path, *, prompt_cache: str = "") -> App:
    """The app over a provider the registry builds as the marking cloud
    client — the section's NAME picks the class."""
    root = tmp_path / "state"
    set_config_provider(root, server, name="openrouter", prompt_cache=prompt_cache)
    return launch(root, server, spec="openrouter/test-model")


def sent(app: App) -> str:
    """The content of the last message the server was actually given."""
    return str(app.server.requests[-1]["messages"][-1]["content"])


def sent_headers(app: App) -> dict[str, str]:
    """The last request's headers, keyed lowercase — a header name is
    case-insensitive on the wire, and the assertion should not care."""
    return {name.lower(): value for name, value in app.server.request_headers[-1].items()}


def typed(app: App, *strokes: str) -> str:
    """The line the prompt returns for `strokes`, sent a beat apart."""
    return Prompting(app).drive(*strokes)


def frames(app: App, *strokes: str, top_row: bool = False) -> list[str]:
    """Every rendering of the prompt's message, as text, over `strokes`;
    `top_row` as the loop sets it when it leaves the blank before the
    prompt to the menu."""
    run = Prompting(app)
    run.drive(*strokes, top_row=top_row)
    return run.drawn


class Prompting:
    """One prompt over a pipe: the real `build_prompt`, its session built
    INSIDE the pipe's app session (prompt_toolkit binds an application to
    the input it is created under), the keys fed a beat apart."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.drawn: list[str] = []
        self.session: PromptSession[str]

    def drive(self, *strokes: str, top_row: bool = False) -> str:
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            chat = self.app.chat
            self.session, answers = build_prompt(
                self.app.api,
                Carry(),
                LineAssembler(),
                story=lambda: chat.story_id,
                can_attach=lambda: chat.can_attach,
                shortcuts=SHORTCUTS,
                aside=lambda: "",
            )
            answers.top_row = top_row

            def feed() -> None:
                for stroke in strokes:
                    time.sleep(0.15)
                    pipe.send_text(stroke)

            def message() -> StyleAndTextTuples:
                fragments = answers.message("> ")
                self.drawn.append("".join(text for _, text in fragments))
                return fragments

            threading.Thread(target=feed, daemon=True).start()
            return self.session.prompt(message, pre_run=answers.open)

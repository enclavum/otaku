"""The scripted model's lines — the terminal twin of demo/script.js,
prose and arc included, so both demos continue "The River That Forgot
Its Name" with the same voice. `reply` answers every wire call the
demo client forwards: the chat continuations in playing order (an alt
take when the same turn is asked again — a /regen), and the extraction
family recognized the way scenarios/support/server.py recognizes it,
by each lore prompt's fixed opening words, so the REAL extractor runs
end to end on canned answers."""

import json
import re

# The shipped character card (/card /cards/odo.json): a real V2 card
# file the REAL parser reads — nothing about the import is faked. The
# greeting's {{user}} shows the persona binding the ask decides.
SAMPLE_CARD = """\
{
  "spec": "chara_card_v2",
  "spec_version": "2.0",
  "data": {
    "name": "Odo",
    "description": "The toll-taker at Harrow Lock: a short, broad man of sixty-odd years in an oilskin coat worn to the color of tea, who keeps the lock's ledger — every vessel, every walker, every rumor that passes the gates, in a hand small as bird tracks.",
    "personality": "Unhurried, exact, dry as a lock ledger; secretly sentimental about water.",
    "scenario": "The river has left its bed and the lock stands dry, and Odo keeps opening the gates on schedule anyway, because the schedule is the last thing still flowing.",
    "first_mes": "Odo does not look up from the ledger. \\"Boat or no boat, {{user}}, the toll is the same: a story. Where you are from, where you are bound, and what you saw of the water on the way. Pay in full, and mind the wet step. Habit, you understand.\\"",
    "mes_example": "<START>\\n{{user}}: Has anything come through today?\\n{{char}}: \\"Two swallows and a rumor. The rumor paid.\\""
  }
}
"""

# The continuations, in playing order; `alt` is the regenerate's other
# take. The prose is demo/script.js's, verbatim.
CONTINUATIONS = [
    {
        "text": (
            "The voice does not come closer, and it does not need to: the culvert carries "
            "it to you the way a speaking-tube carries a captain, round and intimate and "
            "completely without direction.\n\n"
            "“Late,” it says again, tasting the word. “Three nights I have been walking "
            "uphill with my whole life on my back, and the one person in the valley whose "
            "job it is to notice where rivers are drew me in the wrong place the entire "
            "time.”\n\n"
            "Something shifts in the dark ahead — not a splash, exactly. The sound water "
            "would make clearing its throat.\n\n"
            "“Well. You have ink on your hands and my old bed on your boots, mapmaker. "
            "Come in out of the sky and we will talk about where I am going — and about "
            "what you are going to do with that map of yours, which I notice has already "
            "started telling the truth without your permission.”"
        ),
        "alt": (
            "For a while the culvert only breathes at you — a slow push of cool air with "
            "the smell of deep stone in it, the way a well smells at noon.\n\n"
            "“You brought the map,” the voice says at last. There is water in it, "
            "unmistakably: the shape of the words comes rounded at the edges, worn like "
            "the stones under your boots. “Good. Paper listens better than people. Yours "
            "has been listening for two days.”\n\n"
            "A thin sheet of current curls around your ankle, unhurried, and lets go.\n\n"
            "“Come inside, mapmaker. Mind the third step — I have not decided where it "
            "goes yet.”"
        ),
    },
    {
        "text": (
            "You have to stoop for the first few yards, one hand on the crown of the "
            "culvert, and then the dark opens out in a way the lock's builders never "
            "planned: the brick gives way to something older, dressed stone the size of "
            "cart beds, and the ceiling lifts until your held breath comes back to you "
            "late, like an echo with second thoughts.\n\n"
            "The water is ankle-deep here and all of it is moving uphill.\n\n"
            "“The weir,” the voice says, conversational, from everywhere. “Four hundred "
            "years I asked it to open. Ask a thing every day for four hundred years and "
            "the asking wears a channel — in you, not in the thing.” A pause, and the "
            "current tugs once at your boots, gently, the way Tallis tugged his rope. "
            "“So I stopped asking. Sit down on the dry ledge to your left, mapmaker. You "
            "cannot draw standing up, and I want you to draw.”"
        ),
        "alt": (
            "Inside, the culvert is not a culvert for long. Ten paces in, your boots find "
            "stairs — stairs, going down under a river that is going up — and the sound "
            "of the water reorganizes itself around you until it is less like a stream "
            "and more like a room full of quiet people who have just stopped talking.\n\n"
            "“I will tell you what I told the weir,” the voice says. “I am done with the "
            "sea. Everything I carry ends up there, everything I am gets handed over at "
            "the mouth, and in four hundred years it has not once said thank you.”\n\n"
            "The current noses at your ankle like a dog deciding about you.\n\n"
            "“The map, please. I want to see where you think I have been.”"
        ),
    },
    {
        "text": (
            "You unroll the survey on the ledge, and the red line you drew at the ferry "
            "stirs like an eyelash. Where your lantern light falls on the paper, the "
            "water in the chamber grows very still — the particular stillness of someone "
            "reading over your shoulder.\n\n"
            "“There,” the voice says, and a bead of water lands precisely on the weir at "
            "Harrow Lock, soaking a small circle of the paper dark. “That is where I "
            "turned. And here—” a second drop, higher, above the springline, in the blank "
            "hills where you have never had reason to draw anything, “—is where I am "
            "going. There is a valley up there with no river in it. There has never been "
            "a river in it. Think of that, mapmaker. A whole valley, waiting four hundred "
            "years with its bed made.”\n\n"
            "The chamber breathes. The drops on your map do not dry.\n\n"
            "“Draw me the way. Not the way I came — the way I finish. Draw it, and I "
            "will tell you what to do about the village I am leaving behind.”"
        ),
        "alt": (
            "You spread the map. For a long moment nothing happens — and then the wet "
            "climbs the paper's edge in a neat, deliberate line, one contour at a time, "
            "the way a careful reader keeps their place with a finger.\n\n"
            "“Your survey is honest,” the voice says, grudging, as if honesty in paper "
            "were a trick it had not seen before. “Wrong now, but honestly wrong. I can "
            "work with that.”\n\n"
            "Above the weir, in the empty hills, the paper darkens by itself: a thin "
            "wandering line, climbing.\n\n"
            "“That is my road. I have been drawing it for three nights and nobody would "
            "look. You will look, mapmaker, and you will make it true in red — and then "
            "we will discuss what a valley owes its river, and what a river owes a "
            "ferryman who stayed.”"
        ),
    },
    {
        "text": (
            "“The village,” the voice says, before you can ask, “will want to know it "
            "was not their fault. It was not. Tell them that first — people can hear "
            "nothing until they have heard that.”\n\n"
            "The water walks its slow way up the stairs beside you, riser by riser, "
            "patient as arithmetic.\n\n"
            "“The wells will hold. The mill will not, and the miller knows it — he has "
            "been overdue for honesty with himself since before you were born. And "
            "Tallis—” the voice softens the way the current softened at your ankle, "
            "“—Tallis stays, whatever you tell him. He is the only one of them who ever "
            "talked TO me and not ABOUT me. So you will carry him a message, mapmaker, "
            "and you will carry it exactly: the crossing is not closed. It has moved. "
            "And it is uphill from here, and it will need a ferryman who is not afraid "
            "of a river that learned to climb.”"
        ),
        "alt": (
            "“Ask your questions,” the voice says. “You have been holding them so hard "
            "the chamber can taste the iron.”\n\n"
            "And when you ask the only one that matters — why — the water goes quiet for "
            "the length of three slow breaths.\n\n"
            "“Because the sea takes,” it says at last. “Every river ends by being "
            "swallowed and none of us ever asked what would happen if one of us said no. "
            "I said no. That is all the mystery there is, mapmaker. The rest is "
            "engineering.”\n\n"
            "A drop lands on the back of your writing hand, cool and exact.\n\n"
            "“Write THAT under your red line, where the next surveyor will find it.”"
        ),
    },
]

# Once the authored arc runs out, shorter beats that keep answering in
# voice — demo/script.js's refrains, verbatim.
REFRAINS = [
    "The water considers what you said the way the older stones consider frost — "
    "slowly, and from every side at once. “Go on,” the voice says. “You are getting "
    "somewhere, even if it is not where you think.”",
    "Somewhere above you, faint through forty feet of hill, a bell rings in the "
    "village — noon, or an alarm, or both. The river laughs, a sound like a lock "
    "filling. “They will get used to me being gone. Valleys always think they are "
    "the river. The river knows better.”",
    "Your lantern gutters and steadies. In the moment of dark, you would swear the "
    "water had climbed three more steps — and in the light, it politely has not. "
    "“Careful,” says the voice, amused. “I am on my best behaviour while you are "
    "drawing.”",
    "“Tallis used to sing on the crossing,” the voice says, apropos of your pen "
    "scratching. “Badly. Every day for fifty years. You may tell him the river said "
    "so — he will know what I mean by it.”",
    "The red line on your map is longer than you have drawn it. You measure twice "
    "against your last bearing before you accept it: the map is keeping up with the "
    "river now, and it is doing it without you. “You see,” the voice says gently, "
    "“why I needed a mapmaker who could stand being corrected.”",
]

# The scenes an extraction pass closes, in closing order — the same
# titles and summaries the web demo's canned pass writes, filed as the
# analyst JSON the REAL extractor parses. The journal speaks as Maren;
# Tallis is far above ground, so only her diary moves.
SCENES = [
    {
        "title": "The Voice in the Culvert",
        "summary": (
            "Maren followed the impossible water into the lock's culvert and met what "
            "has been walking uphill for three nights: the Meade itself, done with the "
            "sea and bound for a dry valley in the hills. It wants its new course drawn "
            "true — and it has opinions about the village, the miller, and the ferryman "
            "it left behind."
        ),
        "entry": (
            "I went in. The river talks, and it talks like something that has been "
            "rehearsing for four hundred years. I am keeping my hand steady by keeping "
            "it drawing."
        ),
        "state": (
            "On the dry ledge in the chamber under Harrow Lock, map unrolled, drawing "
            "by lantern light."
        ),
    },
    {
        "title": "Terms and Directions",
        "summary": (
            "In the chamber under Harrow Lock the river laid out its terms: the way up "
            "drawn in red, a message for Tallis — the crossing has moved, not closed — "
            "and the truth carried back to the village that none of this was their "
            "fault."
        ),
        "entry": (
            "It gave me a message for Tallis and a road to draw that does not exist "
            "yet. I have stopped asking whether this is surveying. It is; the ground "
            "just moves now."
        ),
        "state": "Underground with the Meade, taking dictation in red ink.",
    },
]

STORY_SO_FAR = (
    "Maren came down into the Meade valley to update her survey and found the river "
    "gone from its bed. Tallis, the ferryman who stayed, said the water went uphill; "
    "the scour marks agreed, and her own map began redrawing itself under his finger. "
    "At Harrow Lock she followed the sound of water into the culvert and met the "
    "Meade itself — walking uphill, done with the sea, and asking for a mapmaker: its "
    "new course drawn true in red, a message carried to Tallis, and the village told "
    "the truth, that none of this was their fault."
)

_HISTORIES = {
    "maren": (
        "Maren came to the valley to bring an old survey up to date and has instead "
        "become the river's own cartographer: she wrote Tallis's impossible testimony "
        "down where he could watch, followed the dry bed to Harrow Lock, and now draws "
        "the Meade's new course by lantern light while it dictates terms."
    ),
    "tallis": (
        "Tallis stayed when everyone else left the crossing. He told the mapmaker the "
        "truth and was believed — and somewhere under the hill the river is composing "
        "a message for him: the crossing is not closed, it has moved, and it will need "
        "a ferryman who is not afraid of water that learned to climb."
    ),
}

_state = {"step": 0, "refrain": 0, "closed": 0, "last_user": None}


def reply(body: dict, purpose: str) -> tuple[str, str]:
    """(thinking, text) for one wire request. The lore prompts are
    recognized by their fixed openings (the same recognition the
    scenario server uses); everything else is play."""
    prompt = _content(body.get("messages", [{}])[-1])
    if "You are a story analyst" in prompt:
        scene = SCENES[min(_state["closed"], len(SCENES) - 1)]
        _state["closed"] += 1
        return "", json.dumps(
            {
                "scene": {"title": scene["title"], "summary": scene["summary"]},
                "speakers": [],
                "characters": [],
                "journals": [
                    {"character": "Maren", "entry": scene["entry"], "state": scene["state"]}
                ],
            },
            ensure_ascii=False,
        )
    if prompt.startswith("Combine the scene summaries"):
        return "", STORY_SO_FAR
    history = re.match(r"Write (.+?)'s history", prompt)
    if history:
        name = history.group(1).strip().lower()
        return "", _HISTORIES.get(
            name, f"{history.group(1)} has walked through these events and remembers them."
        )
    return _play(prompt, body)


def _play(prompt: str, body: dict) -> tuple[str, str]:
    thinking = ""
    # "none" is the app ACTIVELY disabling reasoning, and it is the
    # default — only a real effort asks.
    if body.get("reasoning_effort") not in (None, "none"):
        thinking = "The mapmaker holds her ground; the river wants to be believed. Stay in the water's voice, keep the map at the center. "
    # The same last line asked twice is a regenerate: answer the standing
    # beat's other take, as a fresh sample would differ.
    step = _state["step"]
    if prompt == _state["last_user"] and 0 < step <= len(CONTINUATIONS):
        return thinking, CONTINUATIONS[step - 1]["alt"]
    _state["last_user"] = prompt
    if step < len(CONTINUATIONS):
        _state["step"] += 1
        return thinking, CONTINUATIONS[step]["text"]
    beat = REFRAINS[_state["refrain"] % len(REFRAINS)]
    _state["refrain"] += 1
    _state["step"] += 1
    return thinking, beat


def _content(message: dict) -> str:
    """A wire message's text, whichever shape it was sent in (the parts
    form is the prompt-cache markers')."""
    content = message.get("content", "")
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content)

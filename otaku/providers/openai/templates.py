"""Text templates: a model family's chat markers, rendered by otaku for the text wire.

An instruct model was trained on its family's turn markers; fed a bare
text it repeats itself or answers as an assistant. So the text wire
renders the markers around the story itself — the way SillyTavern's
instruct presets do (its default/content/presets/instruct is the
reference) — as ONE model turn left open: the premise before it, in the
system turn where the family has one and in a user turn otherwise; then
the model turn opened and the story inside it, unclosed, so the model
continues the story where it ends. `PLAIN` is no template at all, for a
base or story-trained model — chosen on purpose in models.toml, never
by default.

Which family a model is: the engine says where it can
(`family_of_config_type` for omlx's model type, `family_of_chat_template`
for llama.cpp's own template), the model's name is the last resort
(`family_of_name`), and a user's own word — the session hands over the
model's models.toml line — is laid over both (`resolve`). BOS is spelled
in the text only where the engine adds none of its own — omlx's engines
— since llama.cpp adds one, and two harm the output.
"""

import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class TextTemplate:
    """One family's markers: each turn kind's opener and closer; `system`
    None where the family has no system role, and the premise rides a
    user turn. `bos` is the token spelled at the head. `source` is where
    the word came from on the copy `resolve` hands out — "override" (the
    user's own line), "engine" (the engine's word on the model), "model"
    (the model's name) — and empty on the catalogue's own."""

    name: str
    user: tuple[str, str]
    model: tuple[str, str]
    system: tuple[str, str] | None = None
    bos: str = ""
    source: str = ""

    def render(self, premise: str, story: str) -> str:
        """The story as the model's own turn in progress, the premise
        before it; nothing closed, so the model continues where the
        story ends."""
        parts = [self.bos]
        if premise.strip():
            opener, closer = self.system or self.user
            parts += [opener, premise, closer]
        parts += [self.model[0], story]
        return "".join(parts)

    @property
    def stops(self) -> tuple[str, ...]:
        """Where a continuation must end: a marker that closes the
        model's turn or opens another — the model has stopped telling.
        Trimmed of the newline a marker is written with."""
        out: list[str] = []
        for marker in (self.model[1], self.user[0], *(self.system or ())):
            marker = marker.strip()
            if marker and marker not in out:
                out.append(marker)
        return tuple(out)


# No template: the premise, a blank line, the story — for a model that
# continues any text.
PLAIN = TextTemplate("plain", user=("", "\n\n"), model=("", ""))


TEXT_TEMPLATES: dict[str, TextTemplate] = {
    template.name: template
    for template in (
        TextTemplate(
            "gemma4",
            bos="<bos>",
            system=("<|turn>system\n", "<turn|>\n"),
            user=("<|turn>user\n", "<turn|>\n"),
            model=("<|turn>model\n", "<turn|>\n"),
        ),
        # Gemma 2 and 3: no system role; the premise rides the user turn.
        TextTemplate(
            "gemma",
            bos="<bos>",
            user=("<start_of_turn>user\n", "<end_of_turn>\n"),
            model=("<start_of_turn>model\n", "<end_of_turn>\n"),
        ),
        # Mistral's Tekken tokenizer (Nemo): no spaces around the markers,
        # no system role, the answer straight after [/INST].
        TextTemplate("mistral-tekken", bos="<s>", user=("[INST]", "[/INST]"), model=("", "</s>")),
        TextTemplate(
            "chatml",
            system=("<|im_start|>system\n", "<|im_end|>\n"),
            user=("<|im_start|>user\n", "<|im_end|>\n"),
            model=("<|im_start|>assistant\n", "<|im_end|>\n"),
        ),
        TextTemplate(
            "llama3",
            bos="<|begin_of_text|>",
            system=("<|start_header_id|>system<|end_header_id|>\n\n", "<|eot_id|>"),
            user=("<|start_header_id|>user<|end_header_id|>\n\n", "<|eot_id|>"),
            model=("<|start_header_id|>assistant<|end_header_id|>\n\n", "<|eot_id|>"),
        ),
        PLAIN,
    )
}

# A family's own marker, found in the model's chat template (llama.cpp's
# /props): the first match tells. `[INST]` also spells the older
# SentencePiece Mistrals, which want spaces the Tekken form has not —
# accepted, a models.toml line overriding where it matters.
_TEMPLATE_MARKS = (
    ("<|turn>", "gemma4"),
    ("<start_of_turn>", "gemma"),
    ("<|im_start|>", "chatml"),
    ("<|start_header_id|>", "llama3"),
    ("[INST]", "mistral-tekken"),
)
# The family off a model's name, the last resort — the community's
# tunes carry their base in the name (g4-…, mn-…); gemma4 before gemma.
_NAME_MARKS = (
    (re.compile(r"gemma[-_ ]?4|(^|[^a-z0-9])g4[-_]"), "gemma4"),
    (re.compile(r"gemma"), "gemma"),
    (re.compile(r"nemo|(^|[^a-z0-9])mn[-_]"), "mistral-tekken"),
    (re.compile(r"qwen"), "chatml"),
    (re.compile(r"llama[-_ ]?3"), "llama3"),
)


def family_of_config_type(kind: object) -> str | None:
    """The family a config's `model_type` (omlx's status word) names —
    where it names one alone: "mistral" and "llama" each cover more
    than one family, so they say nothing."""
    if not isinstance(kind, str):
        return None
    if kind.startswith("gemma4"):
        return "gemma4"
    if kind.startswith("gemma"):
        return "gemma"
    if kind.startswith("qwen"):
        return "chatml"
    return None


def family_of_chat_template(text: object) -> str | None:
    """The family whose marker a chat template carries; None for none."""
    if not isinstance(text, str):
        return None
    return next((family for mark, family in _TEMPLATE_MARKS if mark in text), None)


def family_of_name(model: str) -> str | None:
    """The family a model's name spells; None where it spells none."""
    name = model.lower()
    return next((family for pattern, family in _NAME_MARKS if pattern.search(name)), None)


def resolve(override: object, engine: str | None, model: str) -> TextTemplate | None:
    """The text template for `model`, its `source` set: `override` where
    it names one (an unknown word is the caller's to report, and stands
    for none here), else the engine's word, else the model's name; None
    when none of the three knows — and there is no text wire for the
    model, rather than a bare text an instruct model cannot continue."""
    if isinstance(override, str) and override in TEXT_TEMPLATES:
        return replace(TEXT_TEMPLATES[override], source="override")
    if engine is not None and engine in TEXT_TEMPLATES:
        return replace(TEXT_TEMPLATES[engine], source="engine")
    named = family_of_name(model)
    if named is not None and named in TEXT_TEMPLATES:
        return replace(TEXT_TEMPLATES[named], source="model")
    return None

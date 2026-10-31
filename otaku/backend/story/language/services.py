"""The story's language as the domain knows it: the directions a line
may open with and the inliners it may carry inside, each a token and
the shape of its argument (`context.syntax`'s, read here), what a line
with none of them does, the direction a line opens with, a line's story
and its words with the model (`spans`), and whether a line of a question
still streaming is one of its options — the question tool's own rule.
No App: the language is the same in every story."""

from otaku.backend.story.tools import ToolQuestion
from otaku.context import syntax

# Re-exported: a line's story and its words with the model.
from otaku.context.syntax import spans as spans

# What a line with no framing does — the sentence a help page opens the
# language with, in both frontends.
PROSE_DESCRIPTION = "Your character speaks or acts; the model continues the scene — sent verbatim"

# The directions a line may open with, in the order a menu offers them:
# each token and the shape of its argument.
OPENERS: tuple[tuple[str, str], ...] = tuple(
    (token, direction.args) for token, direction in syntax.DIRECTIONS.items()
)

# The inliners a line may carry after whitespace, each written behind a
# `…`: `/ooc` opens a line AND closes one, and a table keyed by token
# must not collide the two.
INLINERS: tuple[tuple[str, str], ...] = tuple((f"… {token}", "TEXT") for token in syntax.INLINERS)


def opener(line: str) -> str:
    """The direction a typed line opens with — its token, "" for a line
    none opens."""
    return syntax.read(line).token


def is_option(line: str) -> bool:
    """Whether a line of a question call is an answer to pick from — the
    rule the question's own reading parts it by, for a client drawing the
    call while it streams."""
    return ToolQuestion.is_option(line)

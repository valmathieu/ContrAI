"""The explainability channel: what an AI decided, and why.

Every AI strategy returns a *decision* — the :class:`Bid` or :class:`Card`
it chose, paired with a :class:`Rationale` naming the rule that fired and
the table knobs it consulted. The trace rides the **return type** rather
than a side-channel attribute on the strategy, and that is the whole
design:

- A side-channel would be read *after the fact*, off an object the caller
  happens to still hold. A strategy evaluated off the live turn order —
  a search rolling out a hypothetical world, a harness scoring one seat's
  policy against another's — would overwrite it before anyone looked.
- The rungs above the expert rules need the same channel for a different
  payload. An MCTS level explains itself with visit counts, win-rate
  estimates and a principal variation; a learned policy with top-k action
  probabilities. Both are "why this move", both must reach the same
  consumer, and both fit :attr:`Rationale.considered` /
  :attr:`Rationale.citations` without a second seam
  (CLAUDE.md §6.1, AI roadmap §6.1).

The four types below are frozen, slotted, and carry no behaviour: they
are what a decision *is*, not something that computes.
"""

from dataclasses import dataclass

from contrai_core.bid import Bid
from contrai_core.card import Card


@dataclass(frozen=True, slots=True)
class RuleCitation:
    """One ``RuleConfig`` knob that shaped a decision.

    Attributes:
        knob: The :class:`~contrai_core.RuleConfig` field name, e.g.
            ``"under_trump_exemption"``.
        value: Its rendered value, e.g. ``"True"`` or ``"single"``.
        effect: What it changed *here* — not what the knob means in
            general, but what this decision did differently because of
            it, e.g. ``"discarded instead of under-trumping"``.
    """

    knob: str
    value: str
    effect: str


@dataclass(frozen=True, slots=True)
class Rationale:
    """Why a strategy chose what it chose.

    Attributes:
        rule: The rule that fired, named as the strategy's own docstrings
            name it, e.g. ``"cash the master"``.
        detail: One sentence in §10 vocabulary saying what that meant for
            this hand and this trick.
        considered: The alternatives weighed, already rendered to strings
            — runner-up contracts, the cards ranked below the chosen one.
        citations: The table knobs consulted, as :class:`RuleCitation`
            records.
    """

    rule: str
    detail: str
    considered: tuple[str, ...] = ()
    citations: tuple[RuleCitation, ...] = ()


@dataclass(frozen=True, slots=True)
class BidDecision:
    """A chosen :class:`Bid` and the reasoning behind it.

    Attributes:
        bid: The bid the engine will validate and apply.
        rationale: Why this bid rather than the alternatives.
    """

    bid: Bid
    rationale: Rationale


@dataclass(frozen=True, slots=True)
class CardDecision:
    """A chosen :class:`Card` and the reasoning behind it.

    Attributes:
        card: The card the engine will play, drawn from the seat's legal
            cards.
        rationale: Why this card rather than the alternatives.
    """

    card: Card
    rationale: Rationale

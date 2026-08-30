"""Rich-free debug projections of engine state.

Plain-typed, view-agnostic functions that turn live game state into
data any interface can render: the Rich TUI's debug strip today, a web
or richer play interface tomorrow. Everything here returns built-in
containers (``dict``/``list``/``str``) over :mod:`contrai_core` value
objects — no Rich imports, no engine-view imports, no I/O.

The module also owns the canonical trump-first display ordering
(:func:`sort_cards_trump_first`); the Rich view's hand sorting
delegates here so both surfaces order cards identically.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Optional

from contrai_core import (
    BasePlayer,
    Card,
    ContractSuit,
    Position,
    Rank,
    Suit,
    TeamSide,
    TrumpRules,
    rules_for,
)

if TYPE_CHECKING:
    from contrai_engine.model.round import Round

# Short rank labels for plain-text card dumps ("A♠ 10♠ …"). The numeric
# ranks' ``Rank.value`` strings are already short ("7" … "10"); only the
# face ranks spell their names in full and need abbreviating.
_RANK_SHORT = {
    Rank.JACK: "J",
    Rank.QUEEN: "Q",
    Rank.KING: "K",
    Rank.ACE: "A",
}


def _card_label(card: Card) -> str:
    """Plain-text card label, e.g. ``"A♠"`` or ``"10♥"`` — no markup."""
    rank = _RANK_SHORT.get(card.rank, card.rank.value)
    return f"{rank}{Card.SUIT_SYMBOLS[card.suit]}"


def _seat_letter(position: Position) -> str:
    """Single-letter seat label: ``Position.NORTH`` -> ``"N"``.

    The four seat names have distinct initials, so the enum member's
    name is the mapping — no lookup table needed.
    """
    return position.name[0]


def _sorted_suit_cards(
    cards: list[Card], suit: Suit, rules: TrumpRules
) -> list[Card]:
    """One suit's cards from ``cards``, strongest first.

    Args:
        cards: The pool to filter. Not mutated.
        suit: The suit to pick out.
        rules: The regime supplying the scale. ``rank_in_suit`` already
            answers with the trump ladder for the trump suit and the
            plain one elsewhere, so no branch is needed here.

    Returns:
        A new list of the matching cards, highest rank first.
    """
    in_suit = [c for c in cards if c.suit == suit]
    # Comparing ``rank_in_suit`` across suits would be meaningless, but
    # this list holds one suit only — exactly its valid domain.
    in_suit.sort(key=rules.rank_in_suit, reverse=True)
    return in_suit


def sort_cards_trump_first(
    cards: list[Card], trump_suit: Optional[ContractSuit]
) -> list[Card]:
    """Sort cards trump-first then by suit; within each suit by rank.

    Display convention shared by every interface: trump cards on the
    far left (in trump order), then non-trump suits in
    spades/hearts/diamonds/clubs preference, skipping suits with no
    cards. Within a suit, highest rank first.

    Args:
        cards: The cards to order. Not mutated — a new list is returned.
        trump_suit: The active trump, or ``None`` when no contract is
            established (plain suit-preference ordering).

    Returns:
        A new list holding the same ``Card`` objects in display order.
    """
    # Suit's definition order IS the display preference — no need to
    # restate it here.
    rules = rules_for(trump_suit)
    suit_order = list(Suit)
    if trump_suit and trump_suit in suit_order:
        suit_order.remove(trump_suit)
        suit_order.insert(0, trump_suit)

    sorted_cards: list[Card] = []
    for suit in suit_order:
        sorted_cards.extend(_sorted_suit_cards(cards, suit, rules))
    return sorted_cards


def cards_still_in_play(
    players: Iterable[BasePlayer],
) -> dict[Suit, list[Card]]:
    """Group every card still held at the table by suit, high-to-low.

    "Still in play" means sitting in some player's hand — the
    complement of what has already been played this round. Every suit
    appears as a key; an exhausted suit maps to an empty list so
    renderers can show a placeholder instead of skipping it.

    Args:
        players: The players whose hands to aggregate (typically all
            four seats).

    Returns:
        A dict with one entry per :class:`Suit`, each holding that
        suit's unplayed cards ordered strongest-first on the plain
        (non-trump) scale.
    """
    remaining = [card for player in players for card in player.hand]
    plain = rules_for(None)
    return {
        suit: _sorted_suit_cards(remaining, suit, plain) for suit in Suit
    }


def hand_snapshot(
    player: BasePlayer, trump: Optional[ContractSuit]
) -> list[Card]:
    """A trump-first sorted copy of ``player``'s current hand.

    Args:
        player: The seat whose hand to snapshot.
        trump: The active trump, or ``None`` before a contract exists.

    Returns:
        A new list of the hand's ``Card`` objects in display order —
        safe to hold across subsequent plays.
    """
    return sort_cards_trump_first(list(player.hand), trump)


def deal_lines(round_: "Round") -> list[str]:
    """Plain-text snapshot of a fresh deal, one line per seat.

    Args:
        round_: The round whose just-dealt hands to dump.

    Returns:
        A header line (round number + dealer) followed by one
        ``"N: A♠ 10♠ …"`` line per seat in canonical seating order —
        plain suit glyphs, no markup.
    """
    dealer = (
        _seat_letter(round_.dealer.position) if round_.dealer else "—"
    )
    lines = [f"Round #{round_.round_number} dealt — dealer {dealer}"]
    seating = sorted(
        round_.players_order,
        key=lambda p: list(Position).index(p.position),
    )
    for player in seating:
        cards = " ".join(
            _card_label(c)
            for c in sort_cards_trump_first(list(player.hand), None)
        )
        lines.append(f"{_seat_letter(player.position)}: {cards}")
    return lines


def round_result_lines(
    round_: "Round", running_scores: dict[TeamSide, int]
) -> list[str]:
    """Plain-text summary of a finished (or all-passed) round.

    Reads ``round_.contract_made`` — the round's single source of truth
    for the outcome — rather than re-deriving made/failed from scores.

    Args:
        round_: The round to summarize. ``contract is None`` means the
            deal was passed out.
        running_scores: The game's total scores after folding this
            round in, keyed by :class:`TeamSide`.

    Returns:
        The outcome line, a round-points line (skipped for an all-pass,
        which scores nothing), and a totals line.
    """

    def _totals(scores: dict[TeamSide, int]) -> str:
        # ``str(TeamSide.NS)`` is the short "NS" token, which is exactly
        # the register a diagnostics line wants — the full "North-South"
        # label is the view's business.
        return " · ".join(f"{side} {pts}" for side, pts in scores.items())

    contract = round_.contract
    if contract is None:
        lines = [f"Round #{round_.round_number}: all passed — redeal."]
    else:
        glyph = Card.SUIT_SYMBOLS.get(contract.suit, str(contract.suit))
        by = _seat_letter(contract.player.position)
        outcome = "made" if round_.contract_made else "failed"
        lines = [
            f"Round #{round_.round_number}: contract "
            f"{contract.value} {glyph} by {by} — {outcome}.",
            f"Round points: {_totals(round_.round_scores)}",
        ]
    lines.append(f"Totals: {_totals(running_scores)}")
    return lines


def last_decisions(round_, limit: int = 4) -> list[dict]:
    """Plain-container projection of what the AI seats decided this round.

    The debug strip's rationale panel reads this; so could a web view, a
    replay browser, or a training harness logging why a policy acted.
    Nothing Rich, nothing view-shaped: each entry is a ``dict`` of
    strings and lists of strings, exactly like the rest of this module.

    A **human seat contributes nothing**. ``HumanPlayer``'s hooks return
    ``None``, so ``Round`` appends no decision for it — a person's
    reasoning is not the engine's to record — and that seat simply has no
    entry here.

    Args:
        round_: The round whose ``card_decisions`` / ``bid_decisions``
            to project. ``None``, or any object without those lists,
            projects an empty list rather than raising: the debug strip
            renders during frames where no round exists yet.
        limit: How many entries to keep, newest first.

    Returns:
        Up to ``limit`` entries, card plays before bids and newest first
        within each, each holding:

        - ``kind`` — ``"card"`` or ``"bid"``;
        - ``action`` — the card label (``"J♠"``) or the bid's rendering;
        - ``rule`` — the rule that fired;
        - ``detail`` — one sentence on what that meant here;
        - ``considered`` — the alternatives weighed, as strings;
        - ``citations`` — the table knobs consulted, each a
          ``{"knob", "value", "effect"}`` dict.
    """

    cards = list(getattr(round_, "card_decisions", ()) or ())
    bids = list(getattr(round_, "bid_decisions", ()) or ())

    entries: list[dict] = [
        _decision_entry("card", _card_label(decision.card), decision.rationale)
        for decision in reversed(cards)
    ]
    entries.extend(
        _decision_entry("bid", str(decision.bid), decision.rationale)
        for decision in reversed(bids)
    )
    return entries[:limit]


def _decision_entry(kind: str, action: str, rationale) -> dict:
    """Flatten one :class:`Rationale` into plain containers.

    Args:
        kind: ``"card"`` or ``"bid"``.
        action: What was played or bid, already rendered.
        rationale: The decision's rationale.

    Returns:
        The entry dict :func:`last_decisions` documents.
    """

    return {
        "kind": kind,
        "action": action,
        "rule": rationale.rule,
        "detail": rationale.detail,
        "considered": list(rationale.considered),
        "citations": [
            {
                "knob": citation.knob,
                "value": citation.value,
                "effect": citation.effect,
            }
            for citation in rationale.citations
        ],
    }

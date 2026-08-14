"""Small game-state readers for the Rich terminal UI.

Pure functions that read a slice of round/trick state and answer one
question the screens need: who is currently winning the trick, which
trick of the eight is on the table, what constraint applies to the
human's playable cards, how to order the hand for display, which seats
have announced belote, and the env-tunable AI pacing delay. No I/O
beyond ``os.environ`` (read-only, for pacing).
"""

from __future__ import annotations

import os
from typing import Optional, Sequence

from contrai_core import (
    BasePlayer,
    Card,
    ContractSuit,
    NoTrumpRules,
    Play,
    Position,
    rules_for,
)
from contrai_core.trick import current_winner
from rich.text import Text

from contrai_engine.debug_state import sort_cards_trump_first
from contrai_engine.view.formatting import (
    _format_card_compact,
    _position_short,
    _suit_color,
    _suit_glyph,
)
from contrai_engine.view.theme import GREEN_FG


def _sort_hand_for_display(
    cards: list[Card], trump_suit: Optional[ContractSuit]
) -> list[Card]:
    """Sort cards trump-first then by suit; within each suit by rank.

    Thin delegate to :func:`contrai_engine.debug_state.sort_cards_trump_first`,
    the view-agnostic home of the display ordering, so every interface
    (Rich hand row, debug strip, future surfaces) orders cards the same
    way.
    """
    return sort_cards_trump_first(cards, trump_suit)


def _current_winner(
    plays: Sequence[Play], trump_suit: Optional[ContractSuit]
) -> Optional[BasePlayer]:
    """Return the player currently winning the (possibly incomplete) trick.

    Thin wrapper around :func:`contrai_core.trick.current_winner` — the
    module-level winner rule already accepts a raw sequence of
    ``(player, card)`` records (the shape ``_render_diamond`` uses), so
    no trick container needs synthesizing at render time.
    """
    return current_winner(list(plays), trump_suit)


def _trick_index(round_, plays: Sequence[Play]) -> int:
    """The 1-based index of the trick currently on the table.

    Derived from the round's authoritative play state: the count of
    completed tricks, plus one for the trick in progress. A trick whose
    fourth card has just landed is *already* folded into that completed
    history — the play state advances the instant the last card is
    applied, before the view is notified — so a full four-play sequence
    must not be counted a second time.

    Args:
        round_: The active round, or ``None`` before one exists.
        plays: The plays of the trick being rendered.

    Returns:
        The trick index, clamped to the 1-8 range. Falls back to ``1``
        when there is no round or its play phase has not been seeded
        yet — the next trick to be played is always the first one.
    """
    play_state = getattr(round_, "play_state", None) if round_ else None
    if play_state is None:
        return 1
    index = play_state.trick_number + (0 if len(plays) == 4 else 1)
    return min(max(index, 1), 8)


def _explain_constraint(
    player: BasePlayer,
    plays: Sequence[Play],
    playable: list[Card],
    trump_suit: Optional[ContractSuit],
) -> Text:
    """Build the hint line under the hand explaining *why* this is playable.

    ``plays`` are the core ``(player, card)`` records of the trick on the
    table — the same shape a completed :class:`~contrai_core.TrickRecord`
    iterates as, so an in-progress and a finished trick read alike.
    """
    if not plays:
        return Text("your lead — anything goes", style=GREEN_FG)

    led_suit = plays[0][1].suit
    has_led = player.hand.has_suit(led_suit)

    hint = Text("↑ playable ", style=GREEN_FG)
    if has_led:
        hint.append("(must follow ", style=GREEN_FG)
        hint.append(_suit_glyph(led_suit), style=_suit_color(led_suit))
        hint.append(")", style=GREEN_FG)
        return hint

    # No card of led suit. See if we're forced to trump. The first test asks
    # whether the round has a trump suit at all — a plain ``if trump_suit``
    # was true for a NO_TRUMP contract too, since every enum member is
    # truthy; the isinstance check against the sealed no-trump leaf is the
    # honest regime test.
    rules = rules_for(trump_suit)
    if not isinstance(rules, NoTrumpRules) and all(
        rules.is_trump(c.suit) for c in playable
    ):
        # Identify the partner / opponent that led, for the message.
        leader = plays[0][0]
        leader_label = _position_short(leader.position)
        hint.append("(must trump — ", style=GREEN_FG)
        hint.append(f"{leader_label} led ", style=GREEN_FG)
        hint.append(_format_card_compact(plays[0][1]))
        hint.append(")", style=GREEN_FG)
        return hint

    hint.append("(free discard)", style=GREEN_FG)
    return hint


def _belote_by_position(round_) -> dict[Position, str]:
    """Project ``round_.belote_state`` (player → kind) onto positions.

    Returns an empty dict when no round is active, the round has no
    belote_state, or none has been triggered yet. Used to render the
    persistent ★ Belote/Rebelote badge in the trick diamond.
    """
    if round_ is None:
        return {}
    state = getattr(round_, "belote_state", None) or {}
    return {player.position: kind for player, kind in state.items()}


def _resolve_delay(env_var: str, default: float) -> float:
    """Read a float pacing value from the environment with a default.

    Pacing for AI actions is tunable so the user can dial the game
    speed without code edits. Garbage values fall back to ``default``
    rather than raising — this is UI pacing, not a correctness path.
    """
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(0.0, value)

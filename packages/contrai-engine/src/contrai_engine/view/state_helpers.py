"""Small game-state readers for the Rich terminal UI.

Pure functions that read a slice of round/trick state and answer one
question the screens need: who is currently winning the trick, what
constraint applies to the human's playable cards, how to order the hand
for display, which seats have announced belote, and the env-tunable AI
pacing delay. No I/O beyond ``os.environ`` (read-only, for pacing).
"""

from __future__ import annotations

import os
from typing import Optional

from contrai_core import BasePlayer, Card, Suit, Trick
from rich.text import Text

from contrai_engine.view.formatting import (
    _format_card_compact,
    _position_short,
    _suit_color,
    _suit_glyph,
)
from contrai_engine.view.theme import GREEN_FG


def _sort_hand_for_display(cards: list[Card], trump_suit: Optional[Suit]) -> list[Card]:
    """Sort cards trump-first then by suit; within each suit by rank.

    Mockup convention: trump cards on the far left (in trump order),
    then non-trump suits in spades/hearts/diamonds/clubs preference,
    skipping suits with no cards. Within a suit, highest rank first.
    """
    suit_order = [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]
    if trump_suit and trump_suit in suit_order:
        suit_order.remove(trump_suit)
        suit_order.insert(0, trump_suit)

    sorted_cards: list[Card] = []
    for suit in suit_order:
        in_suit = [c for c in cards if c.suit == suit]
        in_suit.sort(key=lambda c: c.get_order(trump_suit), reverse=True)
        sorted_cards.extend(in_suit)
    return sorted_cards


def _current_winner(
    plays: list[tuple[BasePlayer, Card]], trump_suit: Optional[Suit]
) -> Optional[BasePlayer]:
    """Return the player currently winning the (possibly incomplete) trick.

    Thin wrapper around :meth:`contrai_core.trick.Trick.get_current_winner`
    that accepts a raw ``plays`` list (the shape ``_render_diamond`` already
    uses) instead of forcing a Trick allocation at every render.
    """
    if not plays:
        return None
    # Synthesize a minimal Trick for the delegate. Cheap: no game logic
    # depends on the wrapper instance — only the plays list is read.
    proxy = Trick()
    for p, c in plays:
        proxy.plays.append((p, c))
    return proxy.get_current_winner(trump_suit)


def _explain_constraint(
    player: BasePlayer,
    trick: Trick,
    playable: list[Card],
    trump_suit: Optional[Suit],
) -> Text:
    """Build the hint line under the hand explaining *why* this is playable."""
    plays = trick.get_plays() if trick else []
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

    # No card of led suit. See if we're forced to trump.
    if trump_suit and all(c.suit == trump_suit for c in playable):
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


def _belote_by_position(round_) -> dict[str, str]:
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

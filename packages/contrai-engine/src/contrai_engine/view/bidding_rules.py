"""Auction-legality messaging for the Rich terminal UI.

Builds the specific nudge shown when a human types an illegal bid. The
authoritative legality verdict always remains :meth:`Auction.is_legal`;
this never decides anything, it only explains. The adaptive prompt hint
(which actions are legal for the next bidder) is derived directly from
:meth:`Auction.legal_actions` in
:func:`contrai_engine.view.screens.bidding._bidding_prompt_text`.
"""

from __future__ import annotations

from contrai_core import Auction
from contrai_core.bid import (
    Bid,
    ContractBid,
    DoubleBid,
    RedoubleBid,
    SlamLevel,
    bookable_suits,
    ladder_top,
)

from contrai_engine.view.theme import TRUMP_LABEL


def _illegal_bid_reason(bid: Bid, auction: Auction) -> str:
    """Return a human-readable reason ``bid`` is illegal in ``auction``.

    Used by the bid prompt loop to give the player a specific nudge
    instead of a generic rejection. Pure string builder that mirrors the
    rule checks in :class:`contrai_core.auction.Auction` for messaging
    only — the authoritative legality verdict is
    :meth:`Auction.is_legal`. Callers should only invoke this once the
    bid is already known to be illegal.

    The two table limits (an unoffered trump choice, a value past that
    mode's ladder top) read :func:`~contrai_core.bookable_suits` and
    :func:`~contrai_core.ladder_top` directly rather than restating the
    numbers, so this stays a mirror of the rules and not a second
    implementation of them.
    """
    if isinstance(bid, DoubleBid):
        if auction.last_contract_bid is None:
            return "There's no contract to double yet."
        if auction.has_double or auction.has_redouble:
            return "This contract has already been doubled."
        return (
            "You can only double the opposing team's contract, "
            "not your own side's."
        )
    if isinstance(bid, RedoubleBid):
        return (
            "Redouble is only legal right after the opposing team "
            "doubles your team's contract."
        )
    if isinstance(bid, ContractBid):
        # The table's own limits come first, and for the same reason the
        # auction checks them first: they hold whatever the bid history is,
        # so explaining "you must outrank 100" to someone who named a trump
        # this table does not play would be answering the wrong question.
        if bid.suit not in bookable_suits(auction.rules):
            return (
                f"This table doesn't play {TRUMP_LABEL[bid.suit].lower()} — "
                f"the trump choices are the four suits."
            )
        if isinstance(bid.value, int):
            top = ladder_top(bid.suit, auction.rules)
            if bid.value > top:
                return (
                    f"{TRUMP_LABEL[bid.suit]} tops out at {top}: past that "
                    f"there aren't enough points on the table to take."
                )
        last = auction.last_contract_bid
        if last is not None and isinstance(last.value, SlamLevel):
            return f"Nothing outranks a {last.value} bid — you can only pass."
        if last is not None:
            return f"Your bid must outrank the current contract ({last.value})."
        return "That contract bid isn't legal here."
    return "That bid isn't legal right now."

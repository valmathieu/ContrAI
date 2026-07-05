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
)


def _illegal_bid_reason(bid: Bid, auction: Auction) -> str:
    """Return a human-readable reason ``bid`` is illegal in ``auction``.

    Used by the bid prompt loop to give the player a specific nudge
    instead of a generic rejection. Pure string builder that mirrors the
    rule checks in :class:`contrai_core.auction.Auction` for messaging
    only — the authoritative legality verdict is
    :meth:`Auction.is_legal`. Callers should only invoke this once the
    bid is already known to be illegal.
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
        last = auction.last_contract_bid
        if last is not None and isinstance(last.value, SlamLevel):
            return f"Nothing outranks a {last.value} bid — you can only pass."
        if last is not None:
            return f"Your bid must outrank the current contract ({last.value})."
        return "That contract bid isn't legal here."
    return "That bid isn't legal right now."

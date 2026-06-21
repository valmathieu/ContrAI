"""Auction-legality helpers for the Rich terminal UI.

Messaging-only mirrors of the :class:`~contrai_core.auction.Auction`
rules, operating on the legacy ``(player, wire_bid)`` history shape the
bidding renderer consumes. They narrow the prompt hint (which actions
are actually legal for the next bidder) and build the specific nudge
shown when a human types an illegal bid. The authoritative legality
verdict always remains :meth:`Auction.is_legal`; these never decide
anything, they only explain.
"""

from __future__ import annotations

from typing import Optional

from contrai_core import Auction, BasePlayer
from contrai_core.bid import (
    Bid,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
    SlamLevel,
)


def _bid_to_legacy(bid: Bid):
    """Convert a Bid object to the legacy tuple/string history shape."""
    if isinstance(bid, PassBid):
        return "Pass"
    if isinstance(bid, DoubleBid):
        return "Double"
    if isinstance(bid, RedoubleBid):
        return "Redouble"
    if isinstance(bid, ContractBid):
        return (bid.value, bid.suit)
    return "Pass"


def _redouble_available_to(history: list, player: BasePlayer) -> bool:
    """True if *player* may currently redouble — narrows the prompt hint.

    Mirrors :class:`contrai_core.bid.RedoubleBid.is_valid_after` for the
    legacy-format history this view receives, without re-deriving
    Contract objects. The rule: the most recent non-pass bid is a
    Double, no passes have occurred since it, and the previous
    ContractBid was made by *player*'s team.
    """
    if not history or player is None or getattr(player, "team", None) is None:
        return False

    # Walk backwards looking for the most recent Double; abort if we
    # see a Pass first or a Redouble has already fired.
    saw_double = False
    contract_team = None
    for bid_player, bid in reversed(history):
        if bid == "Pass":
            if not saw_double:
                # Pass before any Double — Double slot is closed.
                return False
            # Pass after the Double we already found — also closes the window.
            return False
        if bid == "Redouble":
            return False
        if bid == "Double":
            saw_double = True
            continue
        if isinstance(bid, tuple):
            # That's the ContractBid the Double refers to.
            contract_team = getattr(bid_player, "team", None)
            break

    if not saw_double or contract_team is None:
        return False
    return contract_team == player.team


def _double_available_to(history: list, player: BasePlayer) -> bool:
    """True if *player* may currently double — narrows the prompt hint.

    Mirrors :meth:`contrai_core.auction.Auction._is_double_legal` for
    the legacy-format history this view receives. The rule: there is a
    live :class:`ContractBid`, it was made by the *opposing* team, and
    no Double/Redouble already stands against it. Intervening passes do
    **not** close the Coinche window, so they're skipped over.

    This is messaging only — the authoritative verdict comes from
    :meth:`contrai_core.auction.Auction.is_legal`. It exists so the hint
    stops offering ``double`` when it would be rejected (e.g. doubling
    one's own partner's contract).
    """
    if not history or player is None or getattr(player, "team", None) is None:
        return False

    # Walk backwards: the first non-pass bid decides. A Double/Redouble
    # means the contract is already (re)doubled; a ContractBid is the
    # live contract whose team we compare against.
    for bid_player, bid in reversed(history):
        if bid in ("Double", "Redouble"):
            return False
        if isinstance(bid, tuple):
            contract_team = getattr(bid_player, "team", None)
            if contract_team is None:
                return False
            return contract_team != player.team
    return False


def _min_legal_contract_value(history: list) -> Optional[int]:
    """Lowest contract value a fresh numeric bid could legally announce.

    The prompt's worked example ("e.g. '100 H'") should track the live
    auction rather than always parroting the ``80`` floor: a new contract
    must strictly outrank the standing one, so once someone has bid 90 the
    cheapest legal raise is 100, not 80. Mirrors
    :meth:`contrai_core.auction.Auction._is_contract_value_legal` for the
    legacy-format history this view receives, without re-deriving
    :class:`~contrai_core.auction.Auction` state.

    Args:
        history: The legacy ``(player, wire_bid)`` history. Contract bids
            appear as ``(value, suit)`` tuples; passes/doubles as strings.

    Returns:
        The lowest legal numeric value (80–180) for a new contract bid, or
        ``None`` when no numeric bid is legal anymore — either a standing
        contract of 180 (where only Slam/SoloSlam outrank it) or a
        ``Slam``/``SoloSlam`` that nothing outranks. Callers drop the
        contract example from the hint in that case.
    """
    # Contracts climb monotonically, so the most recent contract bid is
    # also the highest. The first tuple seen walking backwards is it.
    last_value: int | str | None = None
    for _bid_player, bid in reversed(history):
        if isinstance(bid, tuple):
            last_value = bid[0]
            break

    values = ContractBid.VALID_VALUES
    if last_value is None:
        # No contract on the table — the ladder opens at its floor (80).
        return values[0]
    if isinstance(last_value, SlamLevel):
        # Nothing outranks a Slam; no numeric raise is legal.
        return None
    # First numeric step strictly above the standing contract. Past 180
    # only the Slam sentinels remain, so the example is dropped instead.
    for value in values[values.index(last_value) + 1 :]:
        if isinstance(value, int):
            return value
    return None


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

# Wire format bridge between the legacy AI representation and Bid objects

from contrai_core.bid import (
    Bid,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
)
from contrai_core.exceptions import InvalidContractError
from contrai_core.player import BasePlayer

# ---------------------------------------------------------------------------
# Wire format bridge
# ---------------------------------------------------------------------------
# The AI strategy in this package still operates internally on the
# legacy "wire" representation of a bid:
#
#     'Pass' | 'Double' | 'Redouble' | (value, suit)
#
# The Auction API works on real :class:`Bid` instances. These two
# module-level helpers bridge between the two formats so the engine
# boundary can pass Bid objects while the AI's expert table keeps
# using its existing tuple-based helpers. Future AI families should
# consume :meth:`Auction.legal_actions` directly and let these go.


def wire_to_bid(player: BasePlayer, wire) -> Bid:
    """Lift a legacy wire bid choice to a :class:`Bid` instance.

    Args:
        player: The player making the bid (attached to the result).
        wire: ``'Pass'``, ``'Double'``, ``'Redouble'`` or
            ``(value, suit)``. Unrecognised payloads fall back to a
            :class:`PassBid` so the caller can still hand the result
            to :meth:`Auction.apply`, which raises
            :class:`IllegalBidError` if the engine wiring is broken.

    Returns:
        The matching :class:`Bid` subclass instance.
    """

    if wire == 'Pass':
        return PassBid(player)
    if wire == 'Double':
        return DoubleBid(player)
    if wire == 'Redouble':
        return RedoubleBid(player)
    if isinstance(wire, tuple) and len(wire) == 2:
        value, suit = wire
        try:
            return ContractBid(player, value, suit)
        except InvalidContractError:
            # Bad contract value/suit — fall back to Pass. Catch the
            # specific domain error rather than the ValueError umbrella
            # so an unrelated ValueError from ContractBid still surfaces.
            return PassBid(player)
    return PassBid(player)


def bid_to_wire(bid: Bid):
    """Project a :class:`Bid` instance back to the legacy wire format.

    Used by the AI strategy and by the Rich view's bidding-history
    renderer, both of which still consume the legacy
    ``'Pass'`` / ``'Double'`` / ``'Redouble'`` / ``(value, suit)``
    shape.
    """

    if isinstance(bid, PassBid):
        return 'Pass'
    if isinstance(bid, DoubleBid):
        return 'Double'
    if isinstance(bid, RedoubleBid):
        return 'Redouble'
    if isinstance(bid, ContractBid):
        return (bid.value, bid.suit)
    return 'Pass'

"""Putting back the passes the wire never sends.

An observed table does not transmit a pass from a seat that has no other
legal bid. Once a contract is doubled, the doubler's partner can only pass,
so the table skips that turn; after a redouble nobody has anything left but
a pass, so nothing at all follows it; and the partner of a Slam bidder passes
the same way. The table still spends each skipped turn's sequence number,
which is what shows up as a gap in the wire's numbering.

The record keeps the whole auction. A replay of it then closes on the same
three consecutive passes a live game does (``contree-domain.md`` §5.4), and
the engine's own records, which write every forced pass, share its shape.

Nothing is invented. Which seat speaks next follows from the dealer and the
table's direction, and whether that seat had a choice is ``contrai_core``'s
own ruling, asked of the auction as it stood. Each restored pass must also
fill a gap in the wire's numbering: a gap with no forced pass behind it, or a
forced pass with no gap for it, means a bid was lost rather than skipped, and
the round is refused rather than repaired.
"""

from __future__ import annotations

from collections.abc import Sequence

from contrai_core import Auction, Bid, PassBid, Position, RuleConfig

from ..exceptions import ParseError


def restore_forced_passes(
    transmitted: Sequence[tuple[int, Bid[Position]]],
    *,
    dealer: Position,
    rules: RuleConfig,
) -> tuple[Bid[Position], ...]:
    """Rebuild a whole auction from the bids the wire transmitted.

    Args:
        transmitted: ``(wire sequence number, bid)`` pairs, in wire order.
        dealer: The round's dealer. The seat after it, in the table's
            direction, speaks first.
        rules: The table ruleset, which decides the direction and which
            seats had nothing but a pass.

    Returns:
        The auction in speaking order, forced passes included. Where the
        transmitted bids stop, only passes that were forced follow — a seat
        that still had a choice is never given one.

    Raises:
        ParseError: If a skipped seat had a choice, if the passes restored
            before a bid disagree with the gap in the wire's numbering, or if
            a bid arrives after the auction has closed.
    """

    auction = Auction.empty(rules=rules)
    speaker = dealer.next_in(rules.turn_direction)
    previous: int | None = None
    for wire_seq, bid in transmitted:
        restored = 0
        while speaker is not bid.player:
            _refuse_if_closed(auction, bid)
            auction = _put_back(auction, speaker, bid.player)
            speaker = speaker.next_in(rules.turn_direction)
            restored += 1
        _refuse_if_closed(auction, bid)
        # The first bid has nothing before it to measure a gap against, the
        # numbering does not start at the same value everywhere, and no seat
        # can be forced before anyone has opened.
        if previous is not None and restored != wire_seq - previous - 1:
            raise ParseError(
                f"The wire numbers {wire_seq - previous - 1} skipped turn(s) "
                f"before bid {wire_seq}, but {restored} forced pass(es) fall "
                "there"
            )
        # Appended as sent rather than applied: judging a transmitted bid is
        # the verifier's job, and a parser that refused one would hide the
        # very disagreement the verifier exists to surface.
        auction = Auction(bids=auction.bids + (bid,), rules=rules)
        speaker = speaker.next_in(rules.turn_direction)
        previous = wire_seq

    # Past the last transmitted bid: the three passes after a redouble, say.
    # The walk stops at the first seat with a choice, because the wire stopped
    # describing the auction there and a pass for that seat would be a guess.
    while not auction.is_terminal() and _is_forced(auction, speaker):
        auction = auction.apply(PassBid(player=speaker))
        speaker = speaker.next_in(rules.turn_direction)
    return auction.bids


def _is_forced(auction: Auction, seat: Position) -> bool:
    """Whether a pass is the only bid ``seat`` could make."""

    return len(auction.legal_actions(seat)) == 1


def _put_back(auction: Auction, seat: Position, next_actor: Position) -> Auction:
    """Restore one skipped pass, refusing if the seat had a choice."""

    if not _is_forced(auction, seat):
        raise ParseError(
            f"The wire skipped {seat.value} before {next_actor.value}'s bid, "
            f"but {seat.value} had a choice there — a bid is missing"
        )
    return auction.apply(PassBid(player=seat))


def _refuse_if_closed(auction: Auction, bid: Bid[Position]) -> None:
    """Refuse a bid that arrives once the auction is already over."""

    if auction.is_terminal():
        raise ParseError(
            f"A bid by {bid.player.value} arrived after the auction closed"
        )

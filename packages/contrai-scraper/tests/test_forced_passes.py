"""Pins the passes the wire never sends: where they go back, and when not."""

import pytest
from contrai_core import (
    PRESETS,
    ContractBid,
    DoubleBid,
    PassBid,
    Position,
    RedoubleBid,
    RuleConfig,
    SlamLevel,
    Suit,
    TurnDirection,
)

from contrai_scraper import ParseError, restore_forced_passes

NORTH, EAST, SOUTH, WEST = (
    Position.NORTH, Position.EAST, Position.SOUTH, Position.WEST)

#: West deals, so North speaks first and the turn runs N, E, S, W.
TOURNAMENT = PRESETS["tournament"]


def _restore(sent, *, dealer=WEST, rules=TOURNAMENT):
    return restore_forced_passes(sent, dealer=dealer, rules=rules)


class TestNothingToRestore:
    def test_an_auction_without_a_forced_pass_comes_back_as_sent(self):
        sent = [
            (1, ContractBid(player=NORTH, value=80, suit=Suit.HEARTS)),
            (2, PassBid(player=EAST)),
            (3, PassBid(player=SOUTH)),
            (4, PassBid(player=WEST)),
        ]
        assert _restore(sent) == tuple(bid for _, bid in sent)

    def test_the_numbering_may_start_anywhere(self):
        # The wire counts from zero and the fixtures from one; only the gaps
        # between numbers mean anything.
        sent = [
            (0, ContractBid(player=NORTH, value=80, suit=Suit.HEARTS)),
            (1, PassBid(player=EAST)),
            (2, PassBid(player=SOUTH)),
            (3, PassBid(player=WEST)),
        ]
        assert len(_restore(sent)) == 4

    def test_an_unfinished_auction_is_not_finished_for_it(self):
        # South still had a choice when the wire stopped, so nothing is
        # added: a pass there would be a guess.
        sent = [
            (1, ContractBid(player=NORTH, value=80, suit=Suit.HEARTS)),
            (2, PassBid(player=EAST)),
        ]
        assert len(_restore(sent)) == 2


class TestRestoredPasses:
    def test_the_doublers_partner_pass_goes_back_into_its_gap(self):
        # East doubles North; South declines; West — East's partner — can
        # only pass, so the wire spends number 4 on it; North declines.
        sent = [
            (1, ContractBid(player=NORTH, value=80, suit=Suit.HEARTS)),
            (2, DoubleBid(player=EAST)),
            (3, PassBid(player=SOUTH)),
            (5, PassBid(player=NORTH)),
        ]
        restored = _restore(sent)
        assert restored[3] == PassBid(player=WEST)
        assert len(restored) == 5

    def test_the_three_passes_after_a_redouble_go_back_at_the_end(self):
        sent = [
            (1, ContractBid(player=NORTH, value=80, suit=Suit.HEARTS)),
            (2, DoubleBid(player=EAST)),
            (3, RedoubleBid(player=SOUTH)),
        ]
        assert _restore(sent)[3:] == (
            PassBid(player=WEST), PassBid(player=NORTH), PassBid(player=EAST))

    def test_the_slam_bidders_partner_pass_goes_back(self):
        # North bids Slam. East and West may still double it; South, North's
        # partner, has nothing but a pass.
        sent = [
            (1, ContractBid(player=NORTH, value=SlamLevel.SLAM, suit=Suit.HEARTS)),
            (2, PassBid(player=EAST)),
            (4, PassBid(player=WEST)),
        ]
        restored = _restore(sent)
        assert restored[2] == PassBid(player=SOUTH)
        assert len(restored) == 4

    def test_what_is_forced_follows_the_table_rules(self):
        # At a table that never doubles a Slam nobody has anything left after
        # one, so the wire sends nothing more at all.
        rules = RuleConfig(slam_can_be_doubled=False,
                           turn_direction=TurnDirection.CLOCKWISE)
        sent = [(1, ContractBid(player=NORTH, value=SlamLevel.SLAM, suit=Suit.HEARTS))]
        assert _restore(sent, rules=rules)[1:] == (
            PassBid(player=EAST), PassBid(player=SOUTH), PassBid(player=WEST))

    def test_the_walk_follows_the_table_direction(self):
        # Anticlockwise, East deals, North opens and West sits after it, so
        # the doubler is West and the partner whose pass goes back is East.
        sent = [
            (1, ContractBid(player=NORTH, value=80, suit=Suit.HEARTS)),
            (2, DoubleBid(player=WEST)),
            (3, PassBid(player=SOUTH)),
            (5, PassBid(player=NORTH)),
        ]
        restored = _restore(sent, dealer=EAST, rules=RuleConfig.classic())
        assert restored[3] == PassBid(player=EAST)


class TestRefusals:
    def test_a_skipped_seat_that_had_a_choice_is_refused(self):
        sent = [
            (1, ContractBid(player=NORTH, value=80, suit=Suit.HEARTS)),
            (3, PassBid(player=SOUTH)),
        ]
        with pytest.raises(ParseError, match="East had a choice"):
            _restore(sent)

    def test_a_first_bid_from_the_wrong_seat_is_refused(self):
        # Every seat may open, so nobody is ever skipped before the first bid.
        with pytest.raises(ParseError, match="North had a choice"):
            _restore([(1, PassBid(player=EAST))])

    def test_a_forced_pass_with_no_gap_for_it_is_refused(self):
        # West's pass is forced, but the numbers leave no room for it:
        # something other than a skipped turn happened there.
        sent = [
            (1, ContractBid(player=NORTH, value=80, suit=Suit.HEARTS)),
            (2, DoubleBid(player=EAST)),
            (3, PassBid(player=SOUTH)),
            (4, PassBid(player=NORTH)),
        ]
        with pytest.raises(ParseError, match="turn"):
            _restore(sent)

    def test_a_gap_with_no_forced_pass_behind_it_is_refused(self):
        sent = [
            (1, ContractBid(player=NORTH, value=80, suit=Suit.HEARTS)),
            (3, PassBid(player=EAST)),
        ]
        with pytest.raises(ParseError, match="turn"):
            _restore(sent)

    def test_a_bid_after_the_auction_closed_is_refused(self):
        sent = [
            (1, ContractBid(player=NORTH, value=80, suit=Suit.HEARTS)),
            (2, PassBid(player=EAST)),
            (3, PassBid(player=SOUTH)),
            (4, PassBid(player=WEST)),
            (5, PassBid(player=NORTH)),
        ]
        with pytest.raises(ParseError, match="closed"):
            _restore(sent)

    def test_a_bid_after_a_close_reached_mid_walk_is_refused(self):
        # At a table that never doubles a Slam, West's pass is forced and is
        # the third in a row, so the auction closes while the walk is still on
        # its way to East, whose bid can only have come after the close.
        rules = RuleConfig(slam_can_be_doubled=False,
                           turn_direction=TurnDirection.CLOCKWISE)
        sent = [
            (1, ContractBid(player=NORTH, value=SlamLevel.SLAM, suit=Suit.HEARTS)),
            (2, PassBid(player=EAST)),
            (3, PassBid(player=SOUTH)),
            (6, PassBid(player=EAST)),
        ]
        with pytest.raises(ParseError, match="closed"):
            _restore(sent, rules=rules)

"""Pins the deal reconstruction and the eighth trick that is never transmitted."""

from collections import Counter

import pytest
from contrai_core import Card, Position, Rank, Suit

from contrai_scraper import ParseError, deal_hands, final_trick, resolve_dealer


def stock() -> list[Card]:
    """A fixed 32-card order — suit by suit, low to high."""

    return [Card(suit, rank) for suit in Suit for rank in Rank]


ROTATION = (Position.NORTH, Position.WEST, Position.SOUTH, Position.EAST)


class TestDeal:
    def test_every_seat_gets_eight_distinct_cards(self):
        hands = deal_hands(stock(), Position.WEST, ROTATION)
        assert sorted(len(h) for h in hands.values()) == [8, 8, 8, 8]
        assert len({c for h in hands.values() for c in h}) == 32

    def test_the_packets_are_three_two_three(self):
        # 3-2-3 in rotation from the seat after the dealer, which is what the
        # first-receiver argument names.
        cards = stock()
        hands = deal_hands(cards, Position.NORTH, ROTATION)
        assert hands[Position.NORTH][:3] == tuple(cards[0:3])
        assert hands[Position.WEST][:3] == tuple(cards[3:6])
        assert hands[Position.NORTH][3:5] == tuple(cards[12:14])
        assert hands[Position.NORTH][5:] == tuple(cards[20:23])

    def test_the_deal_is_a_permutation_of_the_stock(self):
        cards = stock()
        hands = deal_hands(cards, Position.EAST, ROTATION)
        # A multiset comparison, not a sort: Card carries no ordering, and
        # what matters is that every card was dealt exactly once.
        assert Counter(c for h in hands.values() for c in h) == Counter(cards)

    def test_a_stock_that_is_not_a_deck_is_refused(self):
        with pytest.raises(ParseError):
            deal_hands(stock()[:31], Position.NORTH, ROTATION)


class TestDealerResolution:
    def test_the_one_rotation_that_holds_every_play_wins(self):
        cards = stock()
        hands = deal_hands(cards, Position.SOUTH, ROTATION)
        played = {seat: set(hand[:2]) for seat, hand in hands.items()}
        # first receiver SOUTH means the dealer is the seat before it
        assert resolve_dealer(cards, played, ROTATION) is Position.WEST

    def test_an_ambiguous_round_resolves_to_none(self):
        assert resolve_dealer(stock(), {}, ROTATION) is None

    def test_plays_that_fit_no_rotation_resolve_to_none(self):
        cards = stock()
        impossible = {Position.NORTH: {cards[0], cards[3]}}
        assert resolve_dealer(cards, impossible, ROTATION) is None

    def test_no_stock_resolves_to_none(self):
        assert resolve_dealer([], {Position.NORTH: set()}, ROTATION) is None


class TestFinalTrick:
    def test_the_last_four_cards_are_played_in_rotation_from_the_leader(self):
        # Twenty-eight plays reach the wire; the eighth trick is forced, so the
        # site never sends it and the record marks it derived.
        hands = deal_hands(stock(), Position.NORTH, ROTATION)
        played = {card for hand in hands.values() for card in hand[:7]}
        trick = final_trick(hands, played, Position.SOUTH, ROTATION)
        assert [seat for seat, _ in trick] == [
            Position.SOUTH, Position.EAST, Position.NORTH, Position.WEST]
        assert [card for _, card in trick] == [hands[seat][7] for seat, _ in trick]

    def test_a_round_with_cards_left_over_is_refused(self):
        # Fewer than twenty-eight plays means the capture ended mid-round;
        # reconstructing from an incomplete hand would invent a trick.
        hands = deal_hands(stock(), Position.NORTH, ROTATION)
        played = {card for hand in hands.values() for card in hand[:6]}
        with pytest.raises(ParseError):
            final_trick(hands, played, Position.SOUTH, ROTATION)
